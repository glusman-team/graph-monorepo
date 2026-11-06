package worker

import (
	"strings"
	"testing"
)

func TestParseRequestStrictStagePayload(t *testing.T) {
	extras := map[string]any{
		"operation":   "stage",
		"config_json": `{"operation":"stage","key":"infores:test-kp-1.0.0","nodes_path":"/tmp/nodes.ndjson","edges_path":"/tmp/edges.ndjson","workdir":"/tmp/work","base_key":null,"dictionary_path":null,"zstd_level":0,"no_repack":false,"restart":false,"ids_path":null,"sample_full_release":false,"dry_run":false,"sample_size":1024,"sample_seed":0,"sampled_at":null,"concurrency":2,"threads":0,"engine":"fake","progress":false}`,
	}
	request, err := ParseRequest(extras)
	if err != nil {
		t.Fatalf("ParseRequest: %v", err)
	}
	cfg, ok := request.Config.(*StageConfig)
	if !ok || cfg.SampleSize != 1024 || cfg.Concurrency != 2 || cfg.Engine != "fake" {
		t.Fatalf("decoded config = %#v", request.Config)
	}
}

func TestParseRequestRejectsUnknownFieldsAndSecrets(t *testing.T) {
	_, err := ParseRequest(map[string]any{
		"operation":   "push",
		"config_json": `{"operation":"push","manifest_path":"/tmp/stage.json","report_path":"/tmp/report.json","target_store":"cosmos","concurrency":1,"connection_string":"secret"}`,
	})
	if err == nil || !strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("expected unknown-field rejection, got %v", err)
	}
}

func TestParseRequestRejectsOperationSubstitution(t *testing.T) {
	_, err := ParseRequest(map[string]any{
		"operation":   "purge",
		"config_json": `{"operation":"status","store":"mem://","output_path":"/tmp/status.json","dictionary_path":null,"scope":"all","kg":"","key":"","dry_run":true,"confirmation":""}`,
	})
	if err == nil || !strings.Contains(err.Error(), "does not match") {
		t.Fatalf("expected operation mismatch, got %v", err)
	}
}

func TestParseRequestRejectsUnexpectedExtra(t *testing.T) {
	_, err := ParseRequest(map[string]any{
		"operation": "init", "config_json": `{"operation":"init","store":"mem://","output_path":"/tmp/init.json"}`,
		"command": "rm -rf /",
	})
	if err == nil || !strings.Contains(err.Error(), "unknown pipes extra") {
		t.Fatalf("expected strict extras rejection, got %v", err)
	}
}

func TestPushAndPurgeSafetyValidation(t *testing.T) {
	push := &PushConfig{Operation: "push", ManifestPath: "/tmp/stage", ReportPath: "/tmp/report", TargetStore: "mem://", Concurrency: 1}
	if err := (Request{Operation: "push", Config: push}).Validate(); err == nil {
		t.Fatal("ephemeral push target accepted")
	}
	purge := &PurgeConfig{Operation: "purge", Store: "file:/tmp/docs", OutputPath: "/tmp/purge", Scope: "release", DryRun: true}
	if err := (Request{Operation: "purge", Config: purge}).Validate(); err == nil || !strings.Contains(err.Error(), "requires key") {
		t.Fatalf("release purge without key: %v", err)
	}
}

func TestFingerprintIsStable(t *testing.T) {
	value := map[string]any{"b": 2, "a": "one"}
	first, err := fingerprintDigest(value)
	if err != nil {
		t.Fatal(err)
	}
	second, err := fingerprintDigest(value)
	if err != nil {
		t.Fatal(err)
	}
	if first != second || !digestPattern.MatchString(first) {
		t.Fatalf("unstable fingerprint %q vs %q", first, second)
	}
	if got, _ := StagingDir("/tmp/work", first); !strings.HasSuffix(got, first[3:]) {
		t.Fatalf("staging dir = %q", got)
	}
	if _, err := StagingDir("/tmp/work", "../../production"); err == nil {
		t.Fatal("unsafe digest accepted")
	}
}

func TestDestructiveWorkerGate(t *testing.T) {
	if truthy("") || truthy("0") || truthy("false") {
		t.Fatal("empty/false value enabled destructive jobs")
	}
	for _, value := range []string{"1", "true", "YES", "on"} {
		if !truthy(value) {
			t.Errorf("%q did not enable destructive jobs", value)
		}
	}
}

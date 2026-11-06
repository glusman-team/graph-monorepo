package worker

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestParseRequestStrictLoadPayload(t *testing.T) {
	extras := map[string]any{
		"operation":   "load",
		"config_json": `{"operation":"load","key":"infores:test-kp-1.0.0","nodes_path":"/tmp/nodes.ndjson","edges_path":"/tmp/edges.ndjson","store":"file:/tmp/target.ndjson","report_path":"/tmp/load.json","base_key":null,"dictionary_path":null,"zstd_level":0,"no_repack":true,"sample_size":1024,"sample_seed":0,"sampled_at":null,"concurrency":2,"threads":0,"engine":"fake","progress":false}`,
	}
	request, err := ParseRequest(extras)
	if err != nil {
		t.Fatalf("ParseRequest: %v", err)
	}
	cfg, ok := request.Config.(*LoadConfig)
	if !ok || cfg.SampleSize != 1024 || cfg.Concurrency != 2 || cfg.Engine != "fake" || !cfg.NoRepack {
		t.Fatalf("decoded config = %#v", request.Config)
	}
}

func TestLoadRequiresPersistentTargetAndPositiveSample(t *testing.T) {
	base := &LoadConfig{
		Operation: "load", Key: "infores:test-kp-1.0.0", NodesPath: "/tmp/nodes", EdgesPath: "/tmp/edges",
		Store: "file:/tmp/target.ndjson", ReportPath: "/tmp/load.json", SampleSize: 1024, Concurrency: 2, Engine: "fake",
	}
	if err := (Request{Operation: "load", Config: base}).Validate(); err != nil {
		t.Fatalf("valid load config rejected: %v", err)
	}
	ephemeral := *base
	ephemeral.Store = "mem://"
	if err := (Request{Operation: "load", Config: &ephemeral}).Validate(); err == nil || !strings.Contains(err.Error(), "survive") {
		t.Fatalf("ephemeral load target accepted: %v", err)
	}
	noSample := *base
	noSample.SampleSize = 0
	if err := (Request{Operation: "load", Config: &noSample}).Validate(); err == nil || !strings.Contains(err.Error(), "positive") {
		t.Fatalf("zero-sample load accepted: %v", err)
	}
}

func TestLoadReportValidation(t *testing.T) {
	report := LoadReport{
		SchemaVersion: LoadSchema, Key: "infores:test-kp-1.0.0", Store: "file:/tmp/target.ndjson",
		CompletedAt: time.Now().UTC(), Edges: 2, Created: 2, Versions: 2, Sampled: 4, Offered: 4, BlobBytes: 10,
	}
	if err := report.ValidateComplete(); err != nil {
		t.Fatalf("valid load report rejected: %v", err)
	}
	for name, mutate := range map[string]func(*LoadReport){
		"failed documents": func(r *LoadReport) { r.Failed = 1 },
		"no edges":         func(r *LoadReport) { r.Edges = 0 },
		"no versions":      func(r *LoadReport) { r.Versions = 0 },
		"no sample":        func(r *LoadReport) { r.Sampled = 0 },
		"no documents":     func(r *LoadReport) { r.BlobBytes = 0 },
		"ephemeral store":  func(r *LoadReport) { r.Store = "mem://" },
	} {
		changed := report
		mutate(&changed)
		if err := changed.ValidateComplete(); err == nil {
			t.Errorf("%s passed validation", name)
		}
	}
}

func testLoadConfig(t *testing.T, key, target, reportPath string) *LoadConfig {
	t.Helper()
	stamp := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	return &LoadConfig{
		Operation: "load", Key: key, NodesPath: copyFixture(t, "nodes.ndjson"), EdgesPath: copyFixture(t, "edges.ndjson"),
		Store: "file:" + target, ReportPath: reportPath, NoRepack: true,
		SampleSize: 4, SampleSeed: 42, SampledAt: &stamp, Concurrency: 2, Engine: "fake", Progress: false,
	}
}

func TestDirectLoadMergesIntoExistingStore(t *testing.T) {
	target := filepath.Join(t.TempDir(), "direct.ndjson")
	reportPath := filepath.Join(t.TempDir(), "load-1.json")
	first := testLoadConfig(t, "infores:drugapprovals-kp-1.23.3", target, reportPath)
	metadata, err := runLoad(context.Background(), first, testSettings())
	if err != nil {
		t.Fatalf("first load: %v", err)
	}
	var firstReport LoadReport
	if err := readJSONStrict(reportPath, &firstReport); err != nil {
		t.Fatal(err)
	}
	if err := firstReport.ValidateComplete(); err != nil {
		t.Fatalf("first load report: %v", err)
	}
	if firstReport.Created == 0 || firstReport.Edges == 0 || firstReport.Versions == 0 || firstReport.Sampled != 4 {
		t.Fatalf("unexpected first load report: %+v", firstReport)
	}
	if info, err := os.Stat(target); err != nil || info.Size() == 0 {
		t.Fatalf("direct load target missing after load: %v %v", target, err)
	}
	if metadata["cache_key"] == "" || metadata["store"] != first.Store || metadata["report_path"] != reportPath {
		t.Fatalf("unexpected load metadata: %#v", metadata)
	}

	second := testLoadConfig(t, "infores:drugapprovals-kp-1.23.4", target, filepath.Join(t.TempDir(), "load-2.json"))
	metadata, err = runLoad(context.Background(), second, testSettings())
	if err != nil {
		t.Fatalf("second load: %v", err)
	}
	var secondReport LoadReport
	if err := readJSONStrict(second.ReportPath, &secondReport); err != nil {
		t.Fatal(err)
	}
	if err := secondReport.ValidateComplete(); err != nil {
		t.Fatalf("second load report: %v", err)
	}
	if secondReport.Merged == 0 || secondReport.Versions <= firstReport.Versions {
		t.Fatalf("second load did not preserve merged history: first=%+v second=%+v", firstReport, secondReport)
	}
	if metadata["cache_key"] == "" {
		t.Fatalf("second load metadata missing cache key: %#v", metadata)
	}
}

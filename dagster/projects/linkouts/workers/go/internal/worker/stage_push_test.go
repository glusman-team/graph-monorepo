package worker

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/config"
)

func testSettings() config.Config {
	return config.Config{RUps: 900}
}

func copyFixture(t *testing.T, name string) string {
	t.Helper()
	_, here, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate worker test")
	}
	source := filepath.Join(filepath.Dir(here), "..", "..", "..", "..", "fixtures", "kgx", name)
	raw, err := os.ReadFile(source)
	if err != nil {
		t.Fatalf("read fixture %s: %v", source, err)
	}
	target := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(target, raw, 0o644); err != nil {
		t.Fatal(err)
	}
	return target
}

func testStageConfig(t *testing.T) *StageConfig {
	t.Helper()
	stamp := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	return &StageConfig{
		Operation:   "stage",
		Key:         "infores:drugapprovals-kp-1.23.3",
		NodesPath:   copyFixture(t, "nodes.ndjson"),
		EdgesPath:   copyFixture(t, "edges.ndjson"),
		Workdir:     t.TempDir(),
		SampleSize:  4,
		SampleSeed:  42,
		SampledAt:   &stamp,
		Concurrency: 2,
		Engine:      "fake",
		Progress:    false,
	}
}

func stageForTest(t *testing.T, cfg *StageConfig) (StageManifest, string) {
	t.Helper()
	metadata, err := runStage(context.Background(), cfg, testSettings())
	if err != nil {
		t.Fatalf("runStage: %v", err)
	}
	path, ok := metadata["manifest_path"].(string)
	if !ok {
		t.Fatalf("manifest_path missing from %#v", metadata)
	}
	var manifest StageManifest
	if err := readJSONStrict(path, &manifest); err != nil {
		t.Fatal(err)
	}
	return manifest, path
}

func TestStageIsContentAddressedAndPushIsIdempotent(t *testing.T) {
	cfg := testStageConfig(t)
	manifest, manifestPath := stageForTest(t, cfg)
	if err := manifest.ValidateForPublication(); err != nil {
		t.Fatalf("staged manifest: %v", err)
	}
	if manifest.Sampled != 4 || manifest.Edges == 0 || !manifest.Complete || manifest.Filtered {
		t.Fatalf("unexpected manifest: %+v", manifest)
	}
	before, err := os.ReadFile(manifest.Store[len("file:"):])
	if err != nil {
		t.Fatal(err)
	}
	metadata, err := runStage(context.Background(), cfg, testSettings())
	if err != nil {
		t.Fatal(err)
	}
	if hit, _ := metadata["cache_hit"].(bool); !hit {
		t.Fatalf("identical staging was not a cache hit: %#v", metadata)
	}
	after, err := os.ReadFile(manifest.Store[len("file:"):])
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != string(after) {
		t.Fatal("cache-hit staging rewrote the store")
	}

	target := filepath.Join(t.TempDir(), "published.ndjson")
	push := &PushConfig{
		Operation: "push", ManifestPath: manifestPath, ReportPath: filepath.Join(t.TempDir(), "push.json"),
		ExpectedKey: cfg.Key, TargetStore: "file:" + target, Concurrency: 2,
	}
	if _, err := runPush(context.Background(), push, testSettings()); err != nil {
		t.Fatalf("runPush: %v", err)
	}
	var report PublicationReport
	if err := readJSONStrict(push.ReportPath, &report); err != nil {
		t.Fatal(err)
	}
	if report.SourceDigest != manifest.Digest || report.Key != cfg.Key || report.Read < manifest.Edges || report.BlobBytes == 0 {
		t.Fatalf("unexpected publication report: %+v", report)
	}
	second, err := runPush(context.Background(), push, testSettings())
	if err != nil {
		t.Fatalf("idempotent runPush: %v", err)
	}
	if hit, _ := second["cache_hit"].(bool); !hit {
		t.Fatalf("identical publication was not a cache hit: %#v", second)
	}
	if err := readJSONStrict(push.ReportPath, &report); err != nil {
		t.Fatal(err)
	}
	if report.Skipped != report.Read || report.Created != 0 || report.Replaced != 0 {
		t.Fatalf("rerun mutated publication: %+v", report)
	}
}

func TestChangedInputsSelectNewStoreAndRestartRebuildsSameStore(t *testing.T) {
	cfg := testStageConfig(t)
	first, firstPath := stageForTest(t, cfg)
	fh, err := os.OpenFile(cfg.NodesPath, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	_, writeErr := fh.WriteString("{\"id\":\"CHEBI:999\",\"name\":\"added\",\"category\":\"biolink:ChemicalEntity\"}\n")
	closeErr := fh.Close()
	if writeErr != nil || closeErr != nil {
		t.Fatalf("append fixture: %v %v", writeErr, closeErr)
	}
	second, secondPath := stageForTest(t, cfg)
	if firstPath == secondPath || first.Digest == second.Digest {
		t.Fatal("changed input reused the staged digest")
	}
	metadata, err := runStage(context.Background(), cfg, testSettings())
	if err != nil {
		t.Fatal(err)
	}
	if hit, _ := metadata["cache_hit"].(bool); !hit {
		t.Fatal("second input was not cached before restart")
	}
	cfg.Restart = true
	metadata, err = runStage(context.Background(), cfg, testSettings())
	if err != nil {
		t.Fatal(err)
	}
	if hit, _ := metadata["cache_hit"].(bool); hit {
		t.Fatal("restart reused the completed cache")
	}
}

func TestFilteredIncompleteAndMismatchedStoresCannotPublish(t *testing.T) {
	cfg := testStageConfig(t)
	cfg.IDsPath = filepath.Join(t.TempDir(), "ids.txt")
	if err := os.WriteFile(cfg.IDsPath, []byte(firstEdgeID(t, cfg.EdgesPath)+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	manifest, manifestPath := stageForTest(t, cfg)
	if !manifest.Filtered {
		t.Fatal("ID-filtered manifest was not marked diagnostic")
	}
	fh, err := os.OpenFile(cfg.IDsPath, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	_, writeErr := fh.WriteString("\n")
	closeErr := fh.Close()
	if writeErr != nil || closeErr != nil {
		t.Fatalf("change ID filter: %v %v", writeErr, closeErr)
	}
	_, changedPath := stageForTest(t, cfg)
	if changedPath == manifestPath {
		t.Fatal("changed ID-filter content reused the staged digest")
	}
	push := &PushConfig{
		Operation: "push", ManifestPath: manifestPath, ReportPath: filepath.Join(t.TempDir(), "push.json"),
		TargetStore: "file:" + filepath.Join(t.TempDir(), "out.ndjson"), Concurrency: 1,
	}
	if _, err := runPush(context.Background(), push, testSettings()); err == nil || !strings.Contains(err.Error(), "cannot be published") {
		t.Fatalf("filtered publication error = %v", err)
	}

	manifest.Filtered = false
	manifest.Complete = false
	if err := writeJSONAtomic(manifestPath, manifest); err != nil {
		t.Fatal(err)
	}
	if _, err := runPush(context.Background(), push, testSettings()); err == nil || !strings.Contains(err.Error(), "incomplete") {
		t.Fatalf("incomplete publication error = %v", err)
	}
}

func TestPushGuardsTargetIdentity(t *testing.T) {
	cfg := testStageConfig(t)
	manifest, manifestPath := stageForTest(t, cfg)
	push := &PushConfig{
		Operation: "push", ManifestPath: manifestPath, ReportPath: filepath.Join(t.TempDir(), "push.json"),
		ExpectedKey: "infores:drugapprovals-kp-9.9.9", TargetStore: manifest.Store, Concurrency: 1,
	}
	if _, err := runPush(context.Background(), push, testSettings()); err == nil || !strings.Contains(err.Error(), "expected") {
		t.Fatalf("wrong-key publication error = %v", err)
	}
	push.ExpectedKey = cfg.Key
	if _, err := runPush(context.Background(), push, testSettings()); err == nil || !strings.Contains(err.Error(), "must differ") {
		t.Fatalf("same-source publication error = %v", err)
	}

	push.TargetStore = "cosmos"
	push.Confirmation = "yes"
	settings := testSettings()
	settings.Cosmos.Endpoint = "https://example.documents.azure.com:443"
	settings.Cosmos.Database = "edge_linkouts"
	settings.Cosmos.Container = "edges"
	_, err := runPush(context.Background(), push, settings)
	if err == nil || !strings.Contains(err.Error(), "PUSH https://example.documents.azure.com:443/edge_linkouts/edges") {
		t.Fatalf("Cosmos confirmation error = %v", err)
	}
}

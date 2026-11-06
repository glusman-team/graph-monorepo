package worker

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/operations"
)

func publishedFixture(t *testing.T) (*StageConfig, StageManifest, string, string) {
	t.Helper()
	stage := testStageConfig(t)
	manifest, manifestPath := stageForTest(t, stage)
	target := filepath.Join(t.TempDir(), "published.ndjson")
	push := &PushConfig{
		Operation: "push", ManifestPath: manifestPath, ReportPath: filepath.Join(t.TempDir(), "push.json"),
		ExpectedKey: stage.Key, TargetStore: "file:" + target, Concurrency: 2,
	}
	if _, err := runPush(context.Background(), push, testSettings()); err != nil {
		t.Fatalf("publish fixture: %v", err)
	}
	return stage, manifest, target, push.ReportPath
}

func firstEdgeID(t *testing.T, path string) string {
	t.Helper()
	fh, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = fh.Close() }()
	scanner := bufio.NewScanner(fh)
	for scanner.Scan() {
		var doc map[string]any
		if err := json.Unmarshal(scanner.Bytes(), &doc); err != nil {
			t.Fatal(err)
		}
		if id, _ := doc["id"].(string); id != "" {
			return id
		}
	}
	t.Fatalf("no edge id in %s", path)
	return ""
}

func TestReadOperationsWriteStructuredArtifacts(t *testing.T) {
	stage, _, target, _ := publishedFixture(t)
	statusCfg := &StatusConfig{
		Operation: "status", Store: "file:" + target, OutputPath: filepath.Join(t.TempDir(), "status.json"), CheckPools: true,
	}
	if _, err := runStatus(context.Background(), statusCfg, testSettings()); err != nil {
		t.Fatalf("status: %v", err)
	}
	var status operations.StatusReport
	if err := readJSONStrict(statusCfg.OutputPath, &status); err != nil {
		t.Fatal(err)
	}
	if !status.PoolIndexPresent || len(status.KnowledgeGraphs) == 0 || status.EdgeDocuments == 0 {
		t.Fatalf("unexpected status report: %+v", status)
	}

	seed := int64(42)
	probeCfg := &ProbeConfig{
		Operation: "probe", Store: "file:" + target, OutputPath: filepath.Join(t.TempDir(), "probe.json"),
		Count: 3, Seed: &seed,
	}
	if _, err := runProbe(context.Background(), probeCfg, testSettings()); err != nil {
		t.Fatalf("probe: %v", err)
	}
	var probe operations.ProbeReport
	if err := readJSONStrict(probeCfg.OutputPath, &probe); err != nil {
		t.Fatal(err)
	}
	if probe.Count != 3 || probe.ResolvedBytes == 0 {
		t.Fatalf("unexpected probe report: %+v", probe)
	}

	getCfg := &GetConfig{
		Operation: "get", Store: "file:" + target, OutputPath: filepath.Join(t.TempDir(), "edge.json"),
		EdgeID: firstEdgeID(t, stage.EdgesPath),
	}
	if _, err := runGet(context.Background(), getCfg, testSettings()); err != nil {
		t.Fatalf("get: %v", err)
	}
	var get operations.GetResult
	if err := readJSONStrict(getCfg.OutputPath, &get); err != nil {
		t.Fatal(err)
	}
	if get.ID != getCfg.EdgeID || len(get.Document) == 0 || get.VersionKey == "" {
		t.Fatalf("unexpected get result: %+v", get)
	}
}

func TestRigAndDictionaryArtifacts(t *testing.T) {
	stage := testStageConfig(t)
	rig := &RigConfig{
		Operation: "rig", EdgesPath: stage.EdgesPath, OutputPath: filepath.Join(t.TempDir(), "rig.txt"), Limit: 20,
	}
	metadata, err := runRig(rig)
	if err != nil {
		t.Fatalf("rig: %v", err)
	}
	if rows, _ := metadata["rows"].(int); rows == 0 {
		t.Fatalf("rig metadata = %#v", metadata)
	}
	rig.EmitConfig = true
	if _, err := runRig(rig); err != nil {
		t.Fatalf("rig config: %v", err)
	}
	raw, err := os.ReadFile(rig.OutputPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(raw, []byte("evidence: [")) {
		t.Fatal("rig did not emit display grammar")
	}

	train := &TrainDictConfig{
		Operation: "train_dict", NodesPath: stage.NodesPath, EdgesPath: stage.EdgesPath,
		OutputPath: filepath.Join(t.TempDir(), "fixture.dict"), ReportPath: filepath.Join(t.TempDir(), "train.json"),
		Samples: 4, Engine: "fake",
	}
	if _, err := runTrainDict(context.Background(), train, testSettings()); err != nil {
		t.Fatalf("train dictionary: %v", err)
	}
	dict, err := os.ReadFile(train.OutputPath)
	if err != nil {
		t.Fatal(err)
	}
	if len(dict) == 0 || codec.DictID(dict) == 0 {
		t.Fatal("dictionary artifact is empty or lacks its id")
	}
}

func TestProvisionAndPurgeGuardsUseOnlyTheSelectedStore(t *testing.T) {
	initCfg := &InitConfig{Operation: "init", Store: "mem://", OutputPath: filepath.Join(t.TempDir(), "init.json")}
	if _, err := runInit(context.Background(), initCfg, testSettings()); err != nil {
		t.Fatalf("init mem store: %v", err)
	}

	stage, _, target, _ := publishedFixture(t)
	before, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	purge := &PurgeConfig{
		Operation: "purge", Store: "file:" + target, OutputPath: filepath.Join(t.TempDir(), "purge.json"),
		Scope: "release", Key: stage.Key, DryRun: true,
	}
	if _, err := runPurge(context.Background(), purge, testSettings()); err != nil {
		t.Fatalf("dry-run purge: %v", err)
	}
	after, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, after) {
		t.Fatal("dry-run purge changed the file store")
	}

	purge.DryRun = false
	purge.Confirmation = "yes"
	_, err = runPurge(context.Background(), purge, testSettings())
	if err == nil || !strings.Contains(err.Error(), "DELETE file:"+target+" release "+stage.Key) {
		t.Fatalf("purge confirmation error = %v", err)
	}
	purge.Scope = "container"
	purge.Confirmation = "DROP file:" + target
	if _, err := runPurge(context.Background(), purge, testSettings()); err == nil || !strings.Contains(err.Error(), "requires Cosmos") {
		t.Fatalf("file container-drop error = %v", err)
	}
}

// Package contract verifies the byte contract shared by the Go writer and Elixir reader.
package contract

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/engine"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/operations"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/pipeline"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

const (
	v1 = "infores:drugapprovals-kp-1.11.2"
	v2 = "infores:drugapprovals-kp-1.16.0"
)

var contractFiles = []string{
	"dictdocs.ndjson", "docs.ndjson", "drift.ndjson", "fixture.dict", "unresolvable.ndjson",
}

func projectRoot(t *testing.T) string {
	t.Helper()
	_, here, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate contract test")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(here), "..", "..", "..", ".."))
}

func TestContractFixturesAreByteCurrent(t *testing.T) {
	root := projectRoot(t)
	generated := filepath.Join(t.TempDir(), "contract")
	if err := generate(root, generated); err != nil {
		t.Fatalf("generate contract corpus: %v", err)
	}
	canonical := filepath.Join(root, "contracts")
	for _, name := range contractFiles {
		got, err := os.ReadFile(filepath.Join(generated, name))
		if err != nil {
			t.Fatal(err)
		}
		if os.Getenv("LINKOUTS_UPDATE_CONTRACTS") == "1" {
			if err := writeAtomic(filepath.Join(canonical, name), got); err != nil {
				t.Fatal(err)
			}
			continue
		}
		want, err := os.ReadFile(filepath.Join(canonical, name))
		if err != nil {
			t.Fatalf("read canonical %s (set LINKOUTS_UPDATE_CONTRACTS=1 intentionally to regenerate): %v", name, err)
		}
		if string(got) != string(want) {
			t.Errorf(
				"%s is stale: generated %d bytes, committed %d bytes; regenerate with LINKOUTS_UPDATE_CONTRACTS=1 go test ./internal/contract",
				name, len(got), len(want),
			)
		}
	}
}

func generate(root, out string) error {
	if err := os.MkdirAll(out, 0o755); err != nil {
		return err
	}
	input := func(name string) string {
		return filepath.Join(root, "fixtures", "kgx", name)
	}
	nodes := input("nodes.ndjson")
	if err := load(out, "docs.ndjson", nodes, input("edges.ndjson"), v1, nil); err != nil {
		return err
	}
	if err := load(out, "docs.ndjson", nodes, input("edges.v2.ndjson"), v2, nil); err != nil {
		return err
	}
	if err := load(out, "drift.ndjson", nodes, input("edges.drift.ndjson"), v2, nil); err != nil {
		return err
	}
	if err := load(out, "unresolvable.ndjson", nodes, input("edges.unresolvable.ndjson"), v2, nil); err != nil {
		return err
	}

	eng := engine.NewFake()
	dictionary, err := operations.TrainDictionary(context.Background(), operations.TrainDictionaryOptions{
		NodesPath: nodes, EdgesPath: input("edges.ndjson"), Samples: 64, Engine: eng,
	})
	if err != nil {
		return fmt.Errorf("train fixture dictionary: %w", err)
	}
	if err := eng.Close(); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(out, "fixture.dict"), dictionary.Bytes, 0o644); err != nil {
		return fmt.Errorf("write fixture dictionary: %w", err)
	}
	return load(out, "dictdocs.ndjson", nodes, input("edges.ndjson"), v1, dictionary.Bytes)
}

func load(out, name, nodes, edges, key string, dict []byte) error {
	path := filepath.Join(out, name)
	store, err := cosmos.OpenFile(path, ratelimit.NewUnlimited())
	if err != nil {
		return err
	}
	eng := engine.NewFake()
	_, loadErr := pipeline.Load(context.Background(), pipeline.Options{
		Key:         key,
		NodesPath:   nodes,
		EdgesPath:   edges,
		Store:       store,
		Engine:      eng,
		Budget:      ratelimit.NewUnlimited(),
		Dict:        dict,
		Concurrency: 2,
		SampleSize:  pipeline.DefaultSampleSize,
		SampleSeed:  1,
		Progress:    pipeline.NopProgress{},
		Now:         func() time.Time { return time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC) },
	})
	if closeErr := store.Close(); closeErr != nil && loadErr == nil {
		loadErr = closeErr
	}
	if closeErr := eng.Close(); closeErr != nil && loadErr == nil {
		loadErr = closeErr
	}
	if loadErr != nil {
		return fmt.Errorf("load %s into %s: %w", key, name, loadErr)
	}
	return nil
}

func writeAtomic(path string, raw []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".contract-*")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	defer func() { _ = os.Remove(tmpPath) }()
	if _, err := tmp.Write(raw); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpPath, path)
}

func TestContractCorpusHasNoDuplicateOrUnexpectedFiles(t *testing.T) {
	entries, err := os.ReadDir(filepath.Join(projectRoot(t), "contracts"))
	if err != nil {
		t.Fatal(err)
	}
	want := append(append([]string(nil), contractFiles...), "README.md")
	sort.Strings(want)
	var got []string
	for _, entry := range entries {
		if !entry.IsDir() {
			got = append(got, entry.Name())
		}
	}
	sort.Strings(got)
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("unexpected contract corpus:\ngot %v\nwant %v", got, want)
	}
}

func TestContractFileListIsSortedAndUnique(t *testing.T) {
	if !sort.StringsAreSorted(contractFiles) {
		t.Fatal("contract file list must be sorted for deterministic checks")
	}
	seen := map[string]bool{}
	for _, name := range contractFiles {
		if seen[name] {
			t.Fatalf("duplicate contract file %q", name)
		}
		seen[name] = true
	}
}

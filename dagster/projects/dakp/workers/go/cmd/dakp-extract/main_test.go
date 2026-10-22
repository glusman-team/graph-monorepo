package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/glusman-team/dakp/go/internal/worker"
)

// The cmd tests cover the pure seams of the pipes worker: argument resolution
// (the extras contract with the python asset) and the refs-file round trip the
// stage boundary depends on. The full pipes session path is exercised
// end-to-end by the python asset tests (a real subprocess with pipes env).

func TestParseArgsRequiresTheContractKeys(t *testing.T) {
	cases := []struct {
		name   string
		extras map[string]any
		errOn  string
	}{
		{"nil extras", nil, "no pipes extras"},
		{"missing stage", map[string]any{"input_refs": "in.json", "output_refs": "out.json", "workdir": "/w"}, "extras.stage"},
		{"unknown stage", map[string]any{"stage": "ema", "input_refs": "in.json", "output_refs": "out.json", "workdir": "/w"}, "not one of"},
		{"missing input_refs", map[string]any{"stage": "faers", "output_refs": "out.json", "workdir": "/w"}, "extras.input_refs"},
		{"missing output_refs", map[string]any{"stage": "faers", "input_refs": "in.json", "workdir": "/w"}, "extras.output_refs"},
		{"missing workdir", map[string]any{"stage": "faers", "input_refs": "in.json", "output_refs": "out.json"}, "workdir is required"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := parseArgs(tc.extras)
			if err == nil {
				t.Fatalf("expected an error containing %q, got nil", tc.errOn)
			}
			if !strings.Contains(err.Error(), tc.errOn) {
				t.Fatalf("error %q does not mention %q", err, tc.errOn)
			}
		})
	}
}

func TestParseArgsResolvesFlatKeysAndNumbers(t *testing.T) {
	args, err := parseArgs(map[string]any{
		"stage":            "dailymed",
		"input_refs":       "in.json",
		"output_refs":      "out.json",
		"workdir":          "/w",
		"force":            true,
		"threads":          float64(8), // pipes extras decode JSON numbers as float64
		"memory_budget_gb": float64(16),
	})
	if err != nil {
		t.Fatalf("parseArgs: %v", err)
	}
	if args.Stage != "dailymed" || args.InputRefs != "in.json" || args.OutputRefs != "out.json" {
		t.Fatalf("bad resolution: %+v", args)
	}
	if !args.Config.Force || args.Config.Workdir != "/w" {
		t.Fatalf("bad config: %+v", args.Config)
	}
	if args.Config.Threads != 8 || args.Config.MemoryBudgetGB != 16 {
		t.Fatalf("int knobs must accept float64-encoded numbers: %+v", args.Config)
	}
}

func TestParseArgsAcceptsNestedConfigObject(t *testing.T) {
	args, err := parseArgs(map[string]any{
		"stage":       "faers",
		"input_refs":  "in.json",
		"output_refs": "out.json",
		"workdir":     "/flat-wins",
		"config": map[string]any{
			"workdir":       "/nested",
			"threads":       4,
			"quarter_limit": 2,
		},
	})
	if err != nil {
		t.Fatalf("parseArgs: %v", err)
	}
	if args.Config.Workdir != "/flat-wins" {
		t.Fatalf("flat workdir must win over the nested config: %+v", args.Config)
	}
	if args.Config.Threads != 4 || args.Config.QuarterLimit != 2 {
		t.Fatalf("nested knobs must survive: %+v", args.Config)
	}
}

func TestRefsFileRoundTripThroughTheStageBoundary(t *testing.T) {
	// The python asset writes input_refs with io.refs.refs_to_dicts and reads
	// output_refs with io.refs.refs_from_file; the worker uses ReadRefsFile /
	// WriteRefsFile. Both sides must round-trip the exact snake_case shape.
	rows := int64(12)
	fp := "b3:deadbeef"
	manifest := "manifests/aa/bb.json"
	dict := map[string]any{
		"uri":                "/tmp/x.parquet",
		"blake3":             "b3:abc",
		"media_type":         worker.ParquetMediaType,
		"rows":               rows,
		"schema_fingerprint": fp,
		"manifest":           manifest,
	}
	data, err := json.Marshal([]map[string]any{dict})
	if err != nil {
		t.Fatalf("marshal python-shaped refs: %v", err)
	}
	path := filepath.Join(t.TempDir(), "refs.json")
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatalf("write refs: %v", err)
	}

	refs, err := worker.ReadRefsFile(path)
	if err != nil {
		t.Fatalf("ReadRefsFile: %v", err)
	}
	if len(refs) != 1 || refs[0].URI != "/tmp/x.parquet" || refs[0].Blake3 != "b3:abc" {
		t.Fatalf("decoded refs mismatch: %+v", refs)
	}
	if refs[0].Rows == nil || *refs[0].Rows != rows || refs[0].SchemaFingerprint == nil || *refs[0].SchemaFingerprint != fp {
		t.Fatalf("nullable fields lost: %+v", refs[0])
	}

	out := filepath.Join(t.TempDir(), "out.json")
	if err := worker.WriteRefsFile(out, refs); err != nil {
		t.Fatalf("WriteRefsFile: %v", err)
	}
	again, err := worker.ReadRefsFile(out)
	if err != nil {
		t.Fatalf("re-read: %v", err)
	}
	// Pointer fields (rows/schema_fingerprint/manifest) do not compare by
	// value with ==, so compare the dereferenced contract fields.
	if len(again) != 1 || again[0].URI != refs[0].URI || again[0].Blake3 != refs[0].Blake3 ||
		again[0].MediaType != refs[0].MediaType || deref(again[0].Rows) != deref(refs[0].Rows) ||
		deref(again[0].SchemaFingerprint) != deref(refs[0].SchemaFingerprint) ||
		deref(again[0].Manifest) != deref(refs[0].Manifest) {
		t.Fatalf("round-trip mismatch: %+v vs %+v", again, refs)
	}
}

func deref[T any](p *T) T {
	var zero T
	if p == nil {
		return zero
	}
	return *p
}

func TestWriteRefsFileWritesEmptyArrayForNil(t *testing.T) {
	// JSON null would break the python reader (it expects a list); nil must
	// marshal as [].
	path := filepath.Join(t.TempDir(), "nil.json")
	if err := worker.WriteRefsFile(path, nil); err != nil {
		t.Fatalf("WriteRefsFile(nil): %v", err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if string(raw) != "[]" {
		t.Fatalf("nil refs must serialize as [], got %s", raw)
	}
}

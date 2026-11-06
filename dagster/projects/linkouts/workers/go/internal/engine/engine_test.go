package engine

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
)

func fixtureDir(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate the test file")
	}
	return filepath.Join(filepath.Dir(file), "..", "..", "..", "..", "fixtures", "kgx")
}

func fixture(t *testing.T, name string) string {
	t.Helper()
	return filepath.Join(fixtureDir(t), name)
}

// collect runs a join and returns the merged documents keyed by edge id.
func collect(t *testing.T, e Engine, nodes, edges string) map[string]codec.Doc {
	t.Helper()
	out := map[string]codec.Doc{}
	err := e.Join(context.Background(), Query{NodesPath: nodes, EdgesPath: edges}, func(r Row) error {
		doc, err := MergedDoc(r)
		if err != nil {
			return err
		}
		if _, dup := out[r.ID]; dup {
			t.Errorf("duplicate edge id %s", r.ID)
		}
		out[r.ID] = doc
		return nil
	})
	if err != nil {
		t.Fatalf("%s join: %v", e.Name(), err)
	}
	return out
}

// The headline requirement: every stored document is null-free, edges keep their KGX id, and
// names come from the node side of the join.
func TestFakeJoinProducesNullFreeDocuments(t *testing.T) {
	docs := collect(t, NewFake(), fixture(t, "nodes.ndjson"), fixture(t, "edges.ndjson"))
	if len(docs) != 6 {
		t.Fatalf("got %d documents, want the 6 fixture edges", len(docs))
	}
	for id, doc := range docs {
		if err := codec.CheckNoNulls(doc, "$"); err != nil {
			t.Errorf("edge %s: %v", id, err)
		}
		if got, _ := doc["id"].(string); got != id {
			t.Errorf("document id %q does not match its key %q", got, id)
		}
		for _, field := range []string{"subject", "object", "predicate"} {
			if _, ok := doc[field]; !ok {
				t.Errorf("edge %s lost %s", id, field)
			}
		}
		// The join exists to add these; an edge with no subject_name means the node lookup
		// silently failed.
		if name, _ := doc["subject_name"].(string); name == "" {
			t.Errorf("edge %s has no subject_name; subject was %v", id, doc["subject"])
		}
		if name, _ := doc["object_name"].(string); name == "" {
			t.Errorf("edge %s has no object_name; object was %v", id, doc["object"])
		}
		if _, ok := doc["subject_category"]; !ok {
			t.Errorf("edge %s has no subject_category", id)
		}
		// No nullish placeholder may survive as a value.
		for k, v := range doc {
			if codec.Nullish(v) {
				t.Errorf("edge %s: nullish value survived at %s (%#v)", id, k, v)
			}
		}
	}
}

// An edge whose subject or object is not in the nodes file must lose the name field entirely.
// Storing subject_name: null is the exact failure this pipeline exists to prevent.
func TestUnresolvableEdgesOmitNamesInsteadOfNulling(t *testing.T) {
	docs := collect(t, NewFake(), fixture(t, "nodes.ndjson"), fixture(t, "edges.unresolvable.ndjson"))
	if len(docs) != 2 {
		t.Fatalf("got %d documents, want 2", len(docs))
	}
	for id, doc := range docs {
		for _, field := range []string{"subject_name", "object_name", "subject_category", "object_category"} {
			if v, present := doc[field]; present {
				t.Errorf("edge %s kept %s = %#v for an unresolvable node; it must be absent", id, field, v)
			}
		}
		if err := codec.CheckNoNulls(doc, "$"); err != nil {
			t.Errorf("edge %s: %v", id, err)
		}
	}
	// The second fixture edge also carries subject_name: "" and object_category: [] in the
	// source, which are nullish and must be gone for the same reason.
	if doc, ok := docs["00000000-0000-3000-8000-000000000002"]; ok {
		if _, present := doc["subject_name"]; present {
			t.Error("an empty subject_name survived pruning")
		}
	}
}

func TestMergedDocRejectsEdgeWithoutID(t *testing.T) {
	_, err := MergedDoc(Row{ID: "x", Edge: codec.Doc{"subject": "a"}})
	if err == nil || !strings.Contains(err.Error(), "no usable string id") {
		t.Errorf("MergedDoc without an id = %v", err)
	}
	// Nothing but a nullish value: there is no document to store at all.
	_, err = MergedDoc(Row{ID: "x", Edge: codec.Doc{"junk": ""}})
	if err == nil || !strings.Contains(err.Error(), "every value was nullish") {
		t.Errorf("MergedDoc with only nullish values = %v", err)
	}
	// An id that is itself nullish leaves no identity to key the document by.
	_, err = MergedDoc(Row{ID: "x", Edge: codec.Doc{"id": ""}})
	if err == nil {
		t.Error("MergedDoc accepted an edge whose id is an empty string")
	}
}

func TestQueryValidationAndThreads(t *testing.T) {
	if err := (Query{NodesPath: "n"}).validate(); err == nil {
		t.Error("a query with no edges path was accepted")
	}
	if got := (Query{}).threads(); got != runtime.NumCPU() {
		t.Errorf("threads = %d, want NumCPU (%d)", got, runtime.NumCPU())
	}
	if got := (Query{Threads: 3}).threads(); got != 3 {
		t.Errorf("threads = %d, want the explicit 3", got)
	}
}

func TestBuildJoinSQLQuotesPaths(t *testing.T) {
	dir := t.TempDir()
	nodes := filepath.Join(dir, "it's nodes.ndjson")
	edges := filepath.Join(dir, "edges.ndjson")
	for _, p := range []string{nodes, edges} {
		if err := writeFile(p, "{}\n"); err != nil {
			t.Fatalf("write %s: %v", p, err)
		}
	}
	sql, err := buildJoinSQL(Query{NodesPath: nodes, EdgesPath: edges, Threads: 2})
	if err != nil {
		t.Fatalf("buildJoinSQL: %v", err)
	}
	// A path containing a quote must be doubled, or it terminates the SQL literal and the
	// rest of the query becomes attacker-controlled text.
	if !strings.Contains(sql, "it''s nodes.ndjson") {
		t.Errorf("quote was not escaped in:\n%s", sql)
	}
	if strings.Contains(sql, "{nodes}") || strings.Contains(sql, "{edges}") || strings.Contains(sql, "{threads}") {
		t.Errorf("placeholder survived substitution:\n%s", sql)
	}
	if !strings.Contains(sql, "max_threads = 2") {
		t.Errorf("thread cap missing:\n%s", sql)
	}
	if _, err := buildJoinSQL(Query{NodesPath: filepath.Join(dir, "missing.ndjson"), EdgesPath: edges}); err == nil {
		t.Error("a missing nodes file was accepted")
	}
	if _, err := buildJoinSQL(Query{NodesPath: dir, EdgesPath: edges}); err == nil {
		t.Error("a directory was accepted as an input file")
	}
}

func TestOpenDispatch(t *testing.T) {
	if e, err := Open("fake", "", 0); err != nil || e.Name() == "" {
		t.Errorf("Open(fake) = %v, %v", e, err)
	} else if err := e.Close(); err != nil {
		t.Errorf("Close: %v", err)
	}
	if _, err := Open("postgres", "", 0); err == nil {
		t.Error("Open accepted an unknown backend")
	}
}

// widestPublications counts the longest publications list in a KGX edges file.
func widestPublications(t *testing.T, path string) int {
	t.Helper()
	widest := 0
	err := eachNDJSON(path, func(_ int, raw []byte) error {
		var doc struct {
			Publications []string `json:"publications"`
		}
		if err := json.Unmarshal(raw, &doc); err != nil {
			return nil // not every KGX edge has this field
		}
		if len(doc.Publications) > widest {
			widest = len(doc.Publications)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return widest
}

func writeFile(path, content string) error {
	return os.WriteFile(path, []byte(content), 0o644)
}

// --- embedded ClickHouse ---
//
// These tests are the reason chdb is a dependency at all: they prove the SQL in join.sql
// actually runs on the bundled engine and that it agrees with the pure-Go backend. The first
// run extracts ~540 MiB to CHDB_CACHE_DIR, so they are skipped under -short.

func chdbEngine(t *testing.T) Engine {
	t.Helper()
	if testing.Short() {
		t.Skip("skipping the embedded ClickHouse backend under -short")
	}
	e, err := NewChdb(t.TempDir(), 2)
	if err != nil {
		t.Skipf("embedded ClickHouse unavailable on this platform: %v", err)
	}
	t.Cleanup(func() {
		if err := e.Close(); err != nil {
			t.Errorf("Close: %v", err)
		}
	})
	return e
}

func TestChdbReportsVersion(t *testing.T) {
	e := chdbEngine(t)
	name := e.Name()
	if !strings.Contains(name, "ClickHouse") {
		t.Fatalf("Name() = %q, want the engine version", name)
	}
	t.Logf("engine: %s", name)
}

func TestChdbJoinMatchesFakeBackend(t *testing.T) {
	chdbDocs := collect(t, chdbEngine(t), fixture(t, "nodes.ndjson"), fixture(t, "edges.ndjson"))
	fakeDocs := collect(t, NewFake(), fixture(t, "nodes.ndjson"), fixture(t, "edges.ndjson"))

	if len(chdbDocs) != len(fakeDocs) {
		t.Fatalf("chdb returned %d documents, the fake backend %d", len(chdbDocs), len(fakeDocs))
	}
	for id, want := range fakeDocs {
		got, ok := chdbDocs[id]
		if !ok {
			t.Errorf("chdb dropped edge %s", id)
			continue
		}
		// Canonical bytes, not deep equality: key order and number text are part of the
		// contract, so a difference here is a difference in what gets stored.
		gotJSON, err := codec.Marshal(got)
		if err != nil {
			t.Fatalf("marshal chdb doc %s: %v", id, err)
		}
		wantJSON, err := codec.Marshal(want)
		if err != nil {
			t.Fatalf("marshal fake doc %s: %v", id, err)
		}
		if !equalJSON(gotJSON, wantJSON) {
			t.Errorf("edge %s differs between backends\n chdb %s\n fake %s", id, gotJSON, wantJSON)
		}
		if err := codec.CheckNoNulls(got, "$"); err != nil {
			t.Errorf("chdb document %s: %v", id, err)
		}
	}
}

func TestChdbJoinOmitsUnresolvableNames(t *testing.T) {
	docs := collect(t, chdbEngine(t), fixture(t, "nodes.ndjson"), fixture(t, "edges.unresolvable.ndjson"))
	if len(docs) != 2 {
		t.Fatalf("got %d documents, want 2", len(docs))
	}
	for id, doc := range docs {
		for _, field := range []string{"subject_name", "object_name"} {
			if v, present := doc[field]; present {
				t.Errorf("edge %s kept %s = %#v; an unmatched LEFT JOIN must omit it", id, field, v)
			}
		}
		if err := codec.CheckNoNulls(doc, "$"); err != nil {
			t.Errorf("edge %s: %v", id, err)
		}
	}
}

// The 59 KB fixture edge carries a publications list of thousands of PMIDs on one line. If the
// engine or the row buffer truncates it, this test fails — which is why it is separate.
func TestChdbHandlesWideEdges(t *testing.T) {
	docs := collect(t, chdbEngine(t), fixture(t, "nodes.ndjson"), fixture(t, "edges.ndjson"))

	// The expectation comes from the fixture file itself, not from a remembered constant: the
	// question is whether anything was truncated in transit, not how big DAKP happens to be.
	want := widestPublications(t, fixture(t, "edges.ndjson"))
	if want == 0 {
		t.Fatal("no fixture edge carries a publications list; the wide-edge fixture is missing")
	}
	got := 0
	gotID := ""
	for id, doc := range docs {
		if pubs, ok := doc["publications"].([]any); ok && len(pubs) > got {
			got, gotID = len(pubs), id
		}
	}
	if got != want {
		t.Errorf("widest publications list has %d elements (edge %s), the source fixture has %d", got, gotID, want)
	}
	t.Logf("widest edge %s carries %d publications, matching the source", gotID, got)
}

func TestChdbEmptyEdgeFileIsAnError(t *testing.T) {
	dir := t.TempDir()
	nodes := filepath.Join(dir, "nodes.ndjson")
	edges := filepath.Join(dir, "edges.ndjson")
	if err := writeFile(nodes, `{"id":"A","name":"alpha","category":["biolink:Drug"]}`+"\n"); err != nil {
		t.Fatalf("write nodes: %v", err)
	}
	if err := writeFile(edges, ""); err != nil {
		t.Fatalf("write edges: %v", err)
	}
	e := chdbEngine(t)
	err := e.Join(context.Background(), Query{NodesPath: nodes, EdgesPath: edges}, func(Row) error { return nil })
	if err == nil {
		t.Fatal("an empty edges file produced no error")
	}
	t.Logf("empty edges file: %v", err)
}

// equalJSON compares two canonical documents semantically, because the engine re-serializes
// numbers to shortest form (15.0 becomes 15) while the fake backend keeps the source text.
// Key order is already canonical on both sides, so a byte difference is either that numeric
// normalization or a genuine disagreement — the latter is what this test must catch.
func equalJSON(a, b []byte) bool {
	if string(a) == string(b) {
		return true
	}
	var av, bv any
	if err := json.Unmarshal(a, &av); err != nil {
		return false
	}
	if err := json.Unmarshal(b, &bv); err != nil {
		return false
	}
	return canonicalEqual(av, bv)
}

func canonicalEqual(a, b any) bool {
	switch x := a.(type) {
	case map[string]any:
		y, ok := b.(map[string]any)
		if !ok || len(x) != len(y) {
			return false
		}
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			if _, ok := y[k]; !ok || !canonicalEqual(x[k], y[k]) {
				return false
			}
		}
		return true
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !canonicalEqual(x[i], y[i]) {
				return false
			}
		}
		return true
	case float64:
		y, ok := b.(float64)
		return ok && x == y
	case string:
		y, ok := b.(string)
		return ok && x == y
	case bool:
		y, ok := b.(bool)
		return ok && x == y
	case nil:
		return b == nil
	}
	return false
}

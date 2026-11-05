package pipeline

import (
	"context"
	"errors"
	"testing"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/engine"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

// loadBothReleases stores the two fixture releases and returns the store, so every purge test
// starts from the state a real container is in after two loads.
func loadBothReleases(t *testing.T) *cosmos.Fake {
	t.Helper()
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	first := baseOptions(t, store)
	first.SampleSize = 4
	if _, err := Load(ctx, first); err != nil {
		t.Fatalf("load %s: %v", keyV1, err)
	}
	second := baseOptions(t, store)
	second.Key = keyV2
	second.SampleSize = 4
	if _, err := Load(ctx, second); err != nil {
		t.Fatalf("load %s: %v", keyV2, err)
	}
	return store
}

func edgeDocs(t *testing.T, store *cosmos.Fake) map[string]cosmos.Doc {
	t.Helper()
	out := map[string]cosmos.Doc{}
	for id, d := range store.Docs() {
		if !cosmos.IsReservedID(id) {
			out[id] = d
		}
	}
	return out
}

// Purging one release must leave the other one fully working: the same documents, minus one
// version each, and minus that release's pool and index entry.
func TestPurgeOneReleaseKeepsTheOther(t *testing.T) {
	store := loadBothReleases(t)
	ctx := context.Background()
	before := edgeDocs(t, store)

	stats, err := Purge(ctx, PurgeOptions{Store: store, Slug: slugV, VersionLabel: "1.11.2"})
	if err != nil {
		t.Fatalf("Purge: %v", err)
	}
	if stats.Scanned != int64(len(before)) {
		t.Errorf("scanned %d documents, want the %d edges in the store", stats.Scanned, len(before))
	}
	if stats.Pools != 1 || stats.IndexEntries != 1 {
		t.Errorf("pools=%d indexEntries=%d, want 1 and 1", stats.Pools, stats.IndexEntries)
	}
	// Every fixture edge holds both releases, so none of them may be deleted outright.
	if stats.Deleted != 0 || stats.Rewritten != int64(len(before)) || stats.Versions != int64(len(before)) {
		t.Errorf("deleted=%d rewritten=%d versions=%d, want 0/%d/%d",
			stats.Deleted, stats.Rewritten, stats.Versions, len(before), len(before))
	}

	after := edgeDocs(t, store)
	if len(after) != len(before) {
		t.Fatalf("edge documents went from %d to %d", len(before), len(after))
	}
	for id, d := range after {
		blob := decode(t, d, nil)
		keys := blob.VersionKeys()
		if len(keys) != 1 || keys[0] != keyV2 {
			t.Errorf("%s holds %v, want only %s", id, keys, keyV2)
		}
		// The surviving version must still resolve to the same document it did before the purge.
		got, err := blob.Resolve(keyV2)
		if err != nil {
			t.Fatalf("%s: resolve %s after purge: %v", id, keyV2, err)
		}
		if err := codec.CheckNoNulls(got, "$"); err != nil {
			t.Errorf("%s: %v", id, err)
		}
		if d.KG != slugV {
			t.Errorf("%s: k = %q, want it preserved by the rewrite", id, d.KG)
		}
	}

	// The purged release's pool is gone and the index no longer lists it, so the web app cannot
	// hand out an id from a release that is no longer stored.
	if _, err := store.Read(ctx, cosmos.PoolDocID(slugV, "1.11.2")); !errors.Is(err, cosmos.ErrNotFound) {
		t.Errorf("the purged release's pool is still there: %v", err)
	}
	if _, err := store.Read(ctx, cosmos.PoolDocID(slugV, "1.16.0")); err != nil {
		t.Errorf("the surviving release's pool was removed: %v", err)
	}
	index := readIndex(t, store, nil)
	if _, still := index.KGs[slugV].Versions["1.11.2"]; still {
		t.Errorf("the index still lists 1.11.2: %v", index.KGs[slugV].Versions)
	}
	if rel := index.KGs[slugV].Versions["1.16.0"]; rel.Edges == 0 {
		t.Error("the surviving index entry lost its edge count")
	}
}

// Purging the last release of an edge deletes the document: a blob with no versions cannot be
// decoded, so leaving it behind would store an unreadable document.
func TestPurgeLastReleaseDeletesTheDocument(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	opt := baseOptions(t, store)
	opt.SampleSize = 4
	if _, err := Load(ctx, opt); err != nil {
		t.Fatalf("Load: %v", err)
	}
	edges := len(edgeDocs(t, store))

	stats, err := Purge(ctx, PurgeOptions{Store: store, Slug: slugV})
	if err != nil {
		t.Fatalf("Purge: %v", err)
	}
	if stats.Deleted != int64(edges) {
		t.Errorf("deleted %d documents, want all %d edges", stats.Deleted, edges)
	}
	if left := edgeDocs(t, store); len(left) != 0 {
		t.Errorf("%d edge documents survived a whole-graph purge: %v", len(left), keysOf(left))
	}
	// The pool is gone too, and the index is left empty rather than dangling: the web app reads
	// "no releases" from it and renders its empty state.
	if _, err := store.Read(ctx, cosmos.PoolDocID(slugV, "1.11.2")); !errors.Is(err, cosmos.ErrNotFound) {
		t.Errorf("pool survived: %v", err)
	}
	index := readIndex(t, store, nil)
	if len(index.KGs) != 0 {
		t.Errorf("index still lists %v", index.KGs)
	}
}

// A dry run must report what a purge would do without doing any of it.
func TestPurgeDryRunChangesNothing(t *testing.T) {
	store := loadBothReleases(t)
	ctx := context.Background()
	before := store.Docs()

	stats, err := Purge(ctx, PurgeOptions{Store: store, Slug: slugV, VersionLabel: "1.11.2", DryRun: true})
	if err != nil {
		t.Fatalf("Purge: %v", err)
	}
	if stats.Versions == 0 || stats.Pools != 1 || stats.IndexEntries != 1 {
		t.Errorf("dry run reported versions=%d pools=%d indexEntries=%d, want it to describe the work",
			stats.Versions, stats.Pools, stats.IndexEntries)
	}
	after := store.Docs()
	if len(after) != len(before) {
		t.Fatalf("dry run changed the document count from %d to %d", len(before), len(after))
	}
	for id, d := range before {
		if after[id].Blob != d.Blob {
			t.Errorf("dry run rewrote %s", id)
		}
	}
}

// A whole-store wipe drops everything including the reserved documents, and costs no scan.
func TestPurgeAllWipesTheStore(t *testing.T) {
	store := loadBothReleases(t)
	ctx := context.Background()
	stats, err := Purge(ctx, PurgeOptions{Store: store, All: true})
	if err != nil {
		t.Fatalf("Purge: %v", err)
	}
	if !stats.Dropped || stats.Scanned != 0 {
		t.Errorf("dropped=%v scanned=%d, want a wipe that never scans", stats.Dropped, stats.Scanned)
	}
	if left := store.Docs(); len(left) != 0 {
		t.Errorf("%d documents survived --all", len(left))
	}
	// Reloadable straight afterwards: the wipe is the first half of a schema migration.
	opt := baseOptions(t, store)
	if _, err := Load(ctx, opt); err != nil {
		t.Fatalf("Load after a wipe: %v", err)
	}
	if len(edgeDocs(t, store)) == 0 {
		t.Error("nothing was stored by the load after a wipe")
	}
}

func TestPurgeRejectsUnusableOptions(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	if _, err := Purge(ctx, PurgeOptions{}); err == nil {
		t.Error("Purge without a store was accepted")
	}
	if _, err := Purge(ctx, PurgeOptions{Store: store}); err == nil {
		t.Error("Purge with neither --all nor a KG was accepted")
	}
	if _, err := Purge(ctx, PurgeOptions{Store: store, All: true, Slug: slugV}); err == nil {
		t.Error("Purge accepted --all together with a KG")
	}
	// A graph that was never loaded has nothing to enumerate, and saying so beats a silent no-op.
	if _, err := Purge(ctx, PurgeOptions{Store: store, Slug: "never-loaded"}); err == nil {
		t.Error("purging an unknown KG was accepted")
	}
}

// The slug comparison is on the parsed key, so a graph whose name is a prefix of another's must
// not take the other's documents with it.
func TestPurgeMatchesOnlyTheNamedGraph(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	opt := baseOptions(t, store)
	opt.SampleSize = 4
	if _, err := Load(ctx, opt); err != nil {
		t.Fatalf("Load: %v", err)
	}
	stats, err := Purge(ctx, PurgeOptions{Store: store, Slug: "drugapprovals"})
	if err == nil {
		t.Fatalf("purging a prefix of the real slug reported success: %+v", stats)
	}
	if left := edgeDocs(t, store); len(left) == 0 {
		t.Error("a prefix match deleted documents belonging to a different graph")
	}
}

// Purging a release that a later one deltas against must leave the survivor resolvable, which
// means materializing it — the same rule the codec test checks, exercised through the store.
func TestPurgeBaseReleaseKeepsDependentsReadable(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	first := baseOptions(t, store)
	first.SampleSize = 4
	if _, err := Load(ctx, first); err != nil {
		t.Fatalf("load v1: %v", err)
	}
	second := baseOptions(t, store)
	second.Key = keyV2
	second.SampleSize = 4
	if _, err := Load(ctx, second); err != nil {
		t.Fatalf("load v2: %v", err)
	}
	// The fixture stores the second release as a delta against the first, so this is the
	// interesting case rather than a formality.
	deltas := 0
	for _, d := range edgeDocs(t, store) {
		if !decode(t, d, nil).Versions[keyV2].IsFull() {
			deltas++
		}
	}
	if deltas == 0 {
		t.Fatal("no fixture edge stores the second release as a delta; the test proves nothing")
	}

	want := map[string]codec.Doc{}
	for id, d := range edgeDocs(t, store) {
		doc, err := decode(t, d, nil).Resolve(keyV2)
		if err != nil {
			t.Fatalf("%s: resolve before purge: %v", id, err)
		}
		want[id] = doc
	}

	if _, err := Purge(ctx, PurgeOptions{Store: store, Slug: slugV, VersionLabel: "1.11.2"}); err != nil {
		t.Fatalf("Purge: %v", err)
	}
	for id, d := range edgeDocs(t, store) {
		blob := decode(t, d, nil)
		got, err := blob.Resolve(keyV2)
		if err != nil {
			t.Fatalf("%s: resolve after purging its delta base: %v", id, err)
		}
		if !blob.Versions[keyV2].IsFull() {
			t.Errorf("%s: the dependent was left as a delta against a version that is gone", id)
		}
		raw, err := codec.Marshal(got)
		if err != nil {
			t.Fatalf("%s: marshal: %v", id, err)
		}
		beforeRaw, err := codec.Marshal(want[id])
		if err != nil {
			t.Fatalf("%s: marshal: %v", id, err)
		}
		if string(raw) != string(beforeRaw) {
			t.Errorf("%s resolved differently after the purge:\n got %s\nwant %s", id, raw, beforeRaw)
		}
	}
}

// A dictionary-compressed store must purge with the same dictionary, and rewriting a document must
// keep using it — a purge that dropped the dictionary would inflate every surviving blob.
func TestPurgeWithDictionary(t *testing.T) {
	dict, err := buildTestDict(t)
	if err != nil {
		// Same escape hatch as the load path's dictionary test: the six fixture edges are thin
		// training material, and a builder that cannot find literals says so rather than
		// producing a dictionary that would mis-decode.
		t.Skipf("dictionary training needs more sample mass: %v", err)
	}
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	nodes, edges := fixtures(t)

	first := Options{
		Key: keyV1, NodesPath: nodes, EdgesPath: edges, Store: store,
		Engine: engine.NewFake(), Budget: ratelimit.NewUnlimited(), Concurrency: 2,
		SampleSize: 4, Dict: dict,
	}
	if _, err := Load(ctx, first); err != nil {
		t.Fatalf("load v1: %v", err)
	}
	second := first
	second.Key = keyV2
	if _, err := Load(ctx, second); err != nil {
		t.Fatalf("load v2: %v", err)
	}

	before := 0
	for _, d := range edgeDocs(t, store) {
		before += len(d.Blob)
	}
	if _, err := Purge(ctx, PurgeOptions{
		Store: store, Slug: slugV, VersionLabel: "1.11.2",
		Dict: dict,
	}); err != nil {
		t.Fatalf("Purge with a dictionary: %v", err)
	}
	after := 0
	for id, d := range edgeDocs(t, store) {
		if d.DictID != codec.DefaultDictID {
			t.Errorf("%s lost its dictionary id in the rewrite", id)
		}
		blob := decode(t, d, dict)
		if _, err := blob.Resolve(keyV2); err != nil {
			t.Errorf("%s: %v", id, err)
		}
		after += len(d.Blob)
	}
	if after >= before {
		t.Errorf("rewritten blobs total %d bytes, want fewer than the %d before dropping a version",
			after, before)
	}

	// Without the dictionary the purge must fail loudly rather than silently mis-decode.
	if _, err := Purge(ctx, PurgeOptions{Store: store, Slug: slugV, VersionLabel: "1.16.0"}); err == nil {
		t.Error("purging a dictionary-compressed store without the dictionary was accepted")
	}
}

// A purge reports its progress on a scan long enough to need it, and stays quiet on a short one:
// a progress line that repeats the summary is noise.
func TestPurgeReportsScanProgress(t *testing.T) {
	ctx := context.Background()
	t.Run("short scan stays quiet", func(t *testing.T) {
		store := loadBothReleases(t)
		var calls int
		if _, err := Purge(ctx, PurgeOptions{
			Store: store, Slug: slugV, VersionLabel: "1.11.2",
			OnScan: func(int64, int64) { calls++ },
		}); err != nil {
			t.Fatalf("Purge: %v", err)
		}
		if calls != 0 {
			t.Errorf("OnScan called %d times for a 6-document scan, want none", calls)
		}
	})

	t.Run("long scan reports at the interval and at the end", func(t *testing.T) {
		restore := scanReportInterval
		scanReportInterval = 2
		defer func() { scanReportInterval = restore }()

		store := loadBothReleases(t)
		var seen []int64
		if _, err := Purge(ctx, PurgeOptions{
			Store: store, Slug: slugV, VersionLabel: "1.11.2",
			OnScan: func(scanned, _ int64) { seen = append(seen, scanned) },
		}); err != nil {
			t.Fatalf("Purge: %v", err)
		}
		// Six documents at an interval of two: reports at 2, 4 and 6, then the final line.
		want := []int64{2, 4, 6, 6}
		if len(seen) != len(want) {
			t.Fatalf("OnScan saw %v, want %v", seen, want)
		}
		for i := range want {
			if seen[i] != want[i] {
				t.Errorf("OnScan call %d reported %d documents, want %d", i, seen[i], want[i])
			}
		}
	})
}

func keysOf(m map[string]cosmos.Doc) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

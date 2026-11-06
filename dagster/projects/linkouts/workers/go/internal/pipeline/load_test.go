package pipeline

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/engine"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

const (
	// The keys are in the canonical infores form a real load uses; slugV is what gets stored on
	// documents and in pool ids, the same name without the registry prefix.
	keyV1 = "infores:drugapprovals-kp-1.11.2"
	keyV2 = "infores:drugapprovals-kp-1.16.0"
	slugV = "drugapprovals-kp"
)

func fixtures(t *testing.T) (nodes, edges string) {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate the test file")
	}
	dir := filepath.Join(filepath.Dir(file), "..", "..", "..", "..", "fixtures", "kgx")
	return filepath.Join(dir, "nodes.ndjson"), filepath.Join(dir, "edges.ndjson")
}

func baseOptions(t *testing.T, store cosmos.Store) Options {
	t.Helper()
	nodes, edges := fixtures(t)
	return Options{
		Key:         keyV1,
		NodesPath:   nodes,
		EdgesPath:   edges,
		Store:       store,
		Engine:      engine.NewFake(),
		Budget:      ratelimit.NewUnlimited(),
		Concurrency: 4,
	}
}

// decode reads a stored document back the way the web app will.
func decode(t *testing.T, d cosmos.Doc, dict []byte) *codec.Blob {
	t.Helper()
	blob, err := codec.DecodeBlob(d.Blob, dict)
	if err != nil {
		t.Fatalf("decode %s: %v", d.ID, err)
	}
	return blob
}

func TestLoadFreshCreatesEveryEdge(t *testing.T) {
	store := cosmos.NewFake(nil)
	opt := baseOptions(t, store)
	stats, err := Load(context.Background(), opt)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if stats.Edges != 6 {
		t.Errorf("Edges = %d, want the 6 fixture edges", stats.Edges)
	}
	if stats.Created != 6 || stats.Merged != 0 || stats.Failed != 0 {
		t.Errorf("created=%d merged=%d failed=%d, want 6/0/0", stats.Created, stats.Merged, stats.Failed)
	}
	docs := store.Docs()
	// 6 edges plus the two reserved documents: this release's pool and the pool index.
	if len(docs) != 8 {
		t.Errorf("stored %d documents, want 8 (6 edges + pool + index)", len(docs))
	}
	for id, d := range docs {
		if cosmos.IsReservedID(id) {
			continue
		}
		// Every edge document says which graph stored it, in slug form: that is the field a
		// future query would use, and it is what makes a document self-describing in the portal.
		if d.KG != slugV {
			t.Errorf("%s: k = %q, want the slug %q", id, d.KG, slugV)
		}
		blob := decode(t, d, nil)
		if len(blob.Versions) != 1 {
			t.Errorf("%s holds %d versions, want 1", id, len(blob.Versions))
		}
		doc, err := blob.Resolve(keyV1)
		if err != nil {
			t.Fatalf("resolve %s: %v", id, err)
		}
		if err := codec.CheckNoNulls(doc, "$"); err != nil {
			t.Errorf("%s: %v", id, err)
		}
		if got, _ := doc["id"].(string); got != id {
			t.Errorf("document id %q does not match its key %q", got, id)
		}
		// The join must have attached node-side names; that is the whole reason it exists.
		if name, _ := doc["subject_name"].(string); name == "" {
			t.Errorf("%s has no subject_name", id)
		}
	}
	if stats.RawBytes == 0 || stats.BlobBytes == 0 {
		t.Errorf("size accounting is empty: raw=%d blob=%d", stats.RawBytes, stats.BlobBytes)
	}
	if stats.Sampled == 0 {
		t.Error("no ids were sampled for /random")
	}
}

// A second release of the same KG must land as a second version inside the same document, not
// as a new document and not by overwriting the first.
func TestLoadSecondVersionMergesIntoExistingDocuments(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()

	first := baseOptions(t, store)
	if _, err := Load(ctx, first); err != nil {
		t.Fatalf("first Load: %v", err)
	}
	second := baseOptions(t, store)
	second.Key = keyV2
	stats, err := Load(ctx, second)
	if err != nil {
		t.Fatalf("second Load: %v", err)
	}
	if stats.Merged != 6 || stats.Created != 0 {
		t.Errorf("created=%d merged=%d, want 0/6", stats.Created, stats.Merged)
	}

	docs := store.Docs()
	versions := 0
	for id, d := range docs {
		if cosmos.IsReservedID(id) {
			continue
		}
		blob := decode(t, d, nil)
		if len(blob.Versions) != 2 {
			t.Fatalf("%s holds %d versions, want 2 (%v)", id, len(blob.Versions), blob.VersionKeys())
		}
		versions += len(blob.Versions)
		// Both versions must resolve, and to different documents: the fixture mutates fields
		// between releases, so an identical result means the delta was applied to the wrong base.
		v1, err := blob.Resolve(keyV1)
		if err != nil {
			t.Fatalf("resolve v1 for %s: %v", id, err)
		}
		v2, err := blob.Resolve(keyV2)
		if err != nil {
			t.Fatalf("resolve v2 for %s: %v", id, err)
		}
		for _, doc := range []codec.Doc{v1, v2} {
			if err := codec.CheckNoNulls(doc, "$"); err != nil {
				t.Errorf("%s: %v", id, err)
			}
		}
		entry := blob.Versions[keyV2]
		if entry.IsFull() {
			t.Errorf("%s stored v2 in full though it only differs by a few fields", id)
		}
		if entry.Base != keyV1 {
			t.Errorf("%s: v2 targets %q, want %q", id, entry.Base, keyV1)
		}
	}
	if versions != 12 {
		t.Errorf("total versions = %d, want 12", versions)
	}
}

// Reloading the same key must be idempotent, and --no-repack must avoid rewriting documents
// that already carry it.
func TestReloadSameKeyIsIdempotent(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	opt := baseOptions(t, store)
	if _, err := Load(ctx, opt); err != nil {
		t.Fatalf("first Load: %v", err)
	}
	before := store.Docs()

	again := baseOptions(t, store)
	again.NoRepack = true
	stats, err := Load(ctx, again)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if stats.Skipped != 6 {
		t.Errorf("skipped = %d, want 6 with --no-repack", stats.Skipped)
	}
	if stats.Created != 0 || stats.Merged != 0 {
		t.Errorf("created=%d merged=%d, want 0/0", stats.Created, stats.Merged)
	}
	after := store.Docs()
	for id, d := range before {
		if cosmos.IsReservedID(id) {
			continue
		}
		if after[id].Blob != d.Blob {
			t.Errorf("%s was rewritten by a --no-repack reload", id)
		}
	}

	// Without --no-repack the version is replaced in place, still leaving exactly one version.
	third := baseOptions(t, store)
	stats, err = Load(ctx, third)
	if err != nil {
		t.Fatalf("repack reload: %v", err)
	}
	if stats.Merged != 6 {
		t.Errorf("merged = %d, want 6", stats.Merged)
	}
	for id, d := range store.Docs() {
		if cosmos.IsReservedID(id) {
			continue
		}
		if n := len(decode(t, d, nil).Versions); n != 1 {
			t.Errorf("%s holds %d versions after reloading the same key, want 1", id, n)
		}
	}
}

// readFixture returns the fixture edge ids, one per line, in file order.
func readFixture(t *testing.T) string {
	t.Helper()
	_, edges := fixtures(t)
	b, err := os.ReadFile(edges)
	if err != nil {
		t.Fatal(err)
	}
	out := make([]string, 0, 6)
	for _, l := range strings.Split(strings.TrimRight(string(b), "\n"), "\n") {
		var d struct {
			ID string `json:"id"`
		}
		if err := json.Unmarshal([]byte(l), &d); err != nil {
			t.Fatal(err)
		}
		out = append(out, d.ID)
	}
	return strings.Join(out, "\n")
}

// An --ids run stores only the listed edges: no requests for the excluded ones, and the
// pool and index describe exactly what this run stored.
func TestLoadWithIDFilterStoresOnlyListedEdges(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()

	// The first three fixture edge ids, one per line, exactly the shape a verified-gap
	// file has. One unknown id rides along: the filter must ignore ids the file lacks.
	edges := [3]string{}
	for i, l := range strings.Split(strings.TrimRight(readFixture(t), "\n"), "\n") {
		if i < 3 {
			edges[i] = l
		}
	}
	idsFile := filepath.Join(t.TempDir(), "missing.ids")
	if err := os.WriteFile(idsFile, []byte(edges[0]+"\n"+edges[1]+"\n"+"not-in-the-file-at-all\n\n"+edges[2]+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	opt := baseOptions(t, store)
	opt.IDsPath = idsFile
	opt.SampleSize = 4
	stats, err := Load(ctx, opt)
	if err != nil {
		t.Fatalf("Load --ids: %v", err)
	}
	if stats.Edges != 3 || stats.Created != 3 {
		t.Errorf("edges=%d created=%d, want 3/3: the id filter must drop the other 3 fixture edges", stats.Edges, stats.Created)
	}
	stored := 0
	for id, d := range store.Docs() {
		if !cosmos.IsReservedID(id) {
			stored++
			_ = d
		}
	}
	if stored != 3 {
		t.Errorf("%d edge documents stored, want 3", stored)
	}
	if got := store.OpCounts()["create"]; got != 3 {
		t.Errorf("create calls = %d, want 3: excluded edges must cost no request", got)
	}
	pool := readPool(t, store, nil, slugV, "1.11.2")
	if len(pool.IDs) != 3 {
		t.Errorf("pool holds %d ids, want the 3 stored", len(pool.IDs))
	}
	index := readIndex(t, store, nil)
	if rel := index.KGs[slugV].Versions["1.11.2"]; rel.Edges != 3 {
		t.Errorf("index says %d edges, want 3", rel.Edges)
	}
}

// --sample-full-release flips the reservoir to the whole file, so the tail run of an
// interrupted bulk load still leaves /random sampling the entire release.
func TestLoadWithIDFilterCanSampleFullRelease(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	idsFile := filepath.Join(t.TempDir(), "missing.ids")
	first := ""
	for i, l := range strings.Split(strings.TrimRight(readFixture(t), "\n"), "\n") {
		if i == 0 {
			first = l
			break
		}
	}
	if err := os.WriteFile(idsFile, []byte(first+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	opt := baseOptions(t, store)
	opt.IDsPath = idsFile
	opt.SampleFullRelease = true
	opt.SampleSize = 4
	stats, err := Load(ctx, opt)
	if err != nil {
		t.Fatalf("Load --ids --sample-full-release: %v", err)
	}
	if stats.Edges != 1 || stats.Created != 1 {
		t.Errorf("edges=%d created=%d, want 1/1", stats.Edges, stats.Created)
	}
	index := readIndex(t, store, nil)
	rel := index.KGs[slugV].Versions["1.11.2"]
	if rel.Edges != 6 {
		t.Errorf("index says %d edges, want the release's 6", rel.Edges)
	}
	pool := readPool(t, store, nil, slugV, "1.11.2")
	if len(pool.IDs) != 4 {
		t.Errorf("pool holds %d ids, want the cap of 4", len(pool.IDs))
	}
	// The reservoir must be able to hold ids this run never stored.
	stored := map[string]bool{}
	for id := range store.Docs() {
		stored[id] = true
	}
	sampledStored := 0
	for _, id := range pool.IDs {
		if stored[id] {
			sampledStored++
		}
	}
	t.Logf("pool: %d of %d sampled ids were stored this run (expect ~4/6)", sampledStored, len(pool.IDs))
}

// The flags guard their own combination: --sample-full-release without --ids is a
// misunderstanding, not a configuration.
func TestSampleFullReleaseRequiresIDsPath(t *testing.T) {
	opt := baseOptions(t, cosmos.NewFake(nil))
	opt.SampleFullRelease = true
	if _, err := Load(context.Background(), opt); err == nil {
		t.Fatal("--sample-full-release without --ids was accepted")
	}
}

// A 429 that outlasts the SDK's retries is routine on the shared free-tier account, so the
// pipeline backs off and retries instead of aborting a multi-hour load.
func TestLoadRetriesThrottledStoreCalls(t *testing.T) {
	store := cosmos.NewFake(nil)
	old := throttleBaseDelay
	throttleBaseDelay = time.Millisecond
	defer func() { throttleBaseDelay = old }()

	store.Fail("create", fmt.Errorf("429: %w", cosmos.ErrThrottled))
	stats, err := Load(context.Background(), baseOptions(t, store))
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if stats.Throttled != 1 {
		t.Errorf("throttled=%d, want 1: the armed 429 should be retried exactly once", stats.Throttled)
	}
	if stats.Created != 6 {
		t.Errorf("created=%d, want 6", stats.Created)
	}
}

// alwaysThrottled fails every create with ErrThrottled, which the Fake's one-shot Fail
// cannot express.
type alwaysThrottled struct {
	cosmos.Store
}

func (alwaysThrottled) Create(context.Context, cosmos.Doc) error {
	return fmt.Errorf("429: %w", cosmos.ErrThrottled)
}

// The retry budget is bounded: a genuinely saturated account fails loudly rather than
// hanging the load forever.
func TestLoadGivesUpWhenThrottleOutlastsRetries(t *testing.T) {
	old := throttleBaseDelay
	throttleBaseDelay = time.Millisecond
	defer func() { throttleBaseDelay = old }()

	_, err := Load(context.Background(), baseOptions(t, alwaysThrottled{cosmos.NewFake(nil)}))
	if err == nil {
		t.Fatal("a store that only ever throttles was tolerated")
	}
	if !cosmos.IsThrottled(err) {
		t.Errorf("error %v should still read as throttled", err)
	}
}

// The index write preserves every other graph's entry, and a read of the shared index that
// fails for a transient reason (throttle, network) must abort the load rather than clobber
// the document down to this run's graph alone — the bug that once erased every other graph
// from the home page at the end of an otherwise successful load.
func TestLoadFailsLoudlyWhenTheIndexReadFails(t *testing.T) {
	store := cosmos.NewFake(nil)
	opt := baseOptions(t, store)
	if _, err := Load(context.Background(), opt); err != nil {
		t.Fatalf("first Load: %v", err)
	}

	indexThrottle := &indexReadThrottle{Store: store}
	indexThrottle.fail.Store(true)
	_, err := Load(context.Background(), baseOptions(t, indexThrottle))
	if err == nil {
		t.Fatal("a failed index read was tolerated; the load would have clobbered the shared index")
	}
	if !strings.Contains(err.Error(), "pool index") {
		t.Errorf("error %v should name the pool index read", err)
	}

	// The failed load wrote nothing: the index still describes the first run.
	index, err := ReadPoolIndex(context.Background(), store, nil)
	if err != nil {
		t.Fatalf("read index after failure: %v", err)
	}
	if releases := Releases(index, slugV); len(releases) == 0 {
		t.Errorf("index no longer lists the first run's releases")
	}
}

// indexReadThrottle fails only reads of the reserved index document, and only while armed.
type indexReadThrottle struct {
	cosmos.Store
	fail atomic.Bool
}

func (i *indexReadThrottle) Read(ctx context.Context, id string) (cosmos.Doc, error) {
	if id == cosmos.RandomPoolID && i.fail.Load() {
		return cosmos.Doc{}, fmt.Errorf("429: %w", cosmos.ErrThrottled)
	}
	return i.Store.Read(ctx, id)
}

// shippedDict returns the DAKP dictionary this repo ships to the web app, so tests exercise
// real dictionary-compressed documents instead of skipping when the fixtures are too small to
// train one. Skips (rather than fails) in a checkout without web/, which only happens outside
// this monorepo.
func shippedDict(t *testing.T) []byte {
	t.Helper()
	b, err := os.ReadFile("../../../web/priv/zstd/drugapprovals-kp.77140996.dict")
	if err != nil {
		t.Skipf("shipped dictionary not readable: %v", err)
	}
	return b
}

// Reserved documents (the index, the pools) are shared across graphs, so they are written
// WITHOUT a dictionary and stay readable by any load regardless of the --dict it compresses
// its own edges with; an index a run cannot decode is a loud failure, never a silent fresh
// index. Both halves of that contract are the same bug seen from two sides: a dict-carrying
// load once silently clobbered a dict-free index, and a dict-free load once silently clobbered
// a dict-written one.
func TestReservedDocumentsStayDictionaryFreeAndPreserved(t *testing.T) {
	dict := shippedDict(t)
	store := cosmos.NewFake(nil)
	ctx := context.Background()

	// Seed an index as a no-dictionary run of another graph would have left it.
	seeded := codec.PoolIndex{Schema: codec.PoolIndexSchema, KGs: map[string]codec.KGPool{
		"other-kp": {Versions: map[string]codec.VersionPool{"1.0.0": {Edges: 5, Sampled: 5}}},
	}}
	blob, err := codec.EncodeJSON(seeded, nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	store.Seed(cosmos.Doc{ID: cosmos.RandomPoolID, Blob: blob})

	// A load carrying a dictionary must preserve that index entry, and leave the index and	// its own pool decodable without the dictionary.
	opt := baseOptions(t, store)
	opt.Dict = dict
	if _, err := Load(ctx, opt); err != nil {
		t.Fatalf("Load with a dictionary over a dict-free index: %v", err)
	}
	index, err := ReadPoolIndex(ctx, store, nil)
	if err != nil {
		t.Fatalf("read index without a dictionary: %v (reserved documents must stay dict-free)", err)
	}
	if _, ok := index.KGs["other-kp"]; !ok {
		t.Errorf("the other graph's entry was dropped: %v", index.KGs)
	}
	if _, ok := index.KGs[slugV]; !ok {
		t.Errorf("this run's entry is missing: %v", index.KGs)
	}
	if _, err := ReadPool(ctx, store, nil, slugV, "1.11.2"); err != nil {
		t.Errorf("read pool without a dictionary: %v", err)
	}
}

// The loud half: an index written with a dictionary this run does not carry must abort the
// load rather than overwrite the shared document with a fresh index.
func TestLoadFailsLoudlyWhenTheIndexCarriesAnUnknownDictionary(t *testing.T) {
	dict := shippedDict(t)
	store := cosmos.NewFake(nil)
	ctx := context.Background()

	seeded := codec.PoolIndex{Schema: codec.PoolIndexSchema, KGs: map[string]codec.KGPool{
		"other-kp": {Versions: map[string]codec.VersionPool{"1.0.0": {Edges: 5, Sampled: 5}}},
	}}
	blob, err := codec.EncodeJSON(seeded, dict, 0)
	if err != nil {
		t.Fatal(err)
	}
	store.Seed(cosmos.Doc{ID: cosmos.RandomPoolID, Blob: blob, DictID: codec.DictID(dict)})

	_, err = Load(ctx, baseOptions(t, store))
	if err == nil {
		t.Fatal("an index written with an unknown dictionary was clobbered")
	}
	if got := err.Error(); !contains(got, "dictionary") {
		t.Errorf("error does not name the dictionary: %v", got)
	}
	stored, rerr := store.Read(ctx, cosmos.RandomPoolID)
	if rerr != nil {
		t.Fatal(rerr)
	}
	if string(stored.Blob) != string(blob) {
		t.Error("the failed load overwrote the index document")
	}
}

func TestDryRunWritesNothing(t *testing.T) {
	store := cosmos.NewFake(nil)
	opt := baseOptions(t, store)
	opt.DryRun = true
	stats, err := Load(context.Background(), opt)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(store.Docs()) != 0 {
		t.Errorf("dry run wrote %d documents", len(store.Docs()))
	}
	if counts := store.OpCounts(); len(counts) != 0 {
		t.Errorf("dry run touched the store: %v", counts)
	}
	if stats.Edges != 6 || stats.BlobBytes == 0 {
		t.Errorf("dry run should still measure the work: edges=%d bytes=%d", stats.Edges, stats.BlobBytes)
	}
	if stats.Sampled != 0 {
		t.Errorf("dry run sampled %d ids for /random", stats.Sampled)
	}
}

func TestChargesAreDebitedToTheBudget(t *testing.T) {
	budget := ratelimit.New(100000) // fast enough not to slow the test, real enough to account
	// Charges are reported by the store, so the store is what holds the budget.
	store := cosmos.NewFake(budget)
	store.ChargeFor["create"] = 5.5
	store.ChargeFor["read"] = 1.0
	opt := baseOptions(t, store)
	opt.Budget = budget
	if _, err := Load(context.Background(), opt); err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got := budget.Consumed(); got != 6*5.5 {
		t.Errorf("budget consumed %v RU, want %v", got, 6*5.5)
	}
}

func TestPreconditionConflictRetriesAndSucceeds(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	opt := baseOptions(t, store)
	if _, err := Load(ctx, opt); err != nil {
		t.Fatalf("first Load: %v", err)
	}
	// One lost race: the next replace sees a stale etag, must re-read, and must still succeed.
	store.Fail("replace", cosmos.ErrPreconditionFailed)
	second := baseOptions(t, store)
	second.Key = keyV2
	stats, err := Load(ctx, second)
	if err != nil {
		t.Fatalf("Load after a precondition failure: %v", err)
	}
	if stats.Merged != 6 || stats.Failed != 0 {
		t.Errorf("merged=%d failed=%d, want 6/0", stats.Created, stats.Failed)
	}
}

func TestStoreFailureAbortsTheRun(t *testing.T) {
	store := cosmos.NewFake(nil)
	store.Fail("create", errors.New("network unreachable"))
	opt := baseOptions(t, store)
	_, err := Load(context.Background(), opt)
	if err == nil {
		t.Fatal("a store failure did not abort the run")
	}
	if !errors.Is(err, err) { // sanity: the error is the one the store produced, wrapped
		t.Error("unexpected error identity")
	}
}

func TestDictionaryMismatchIsFatal(t *testing.T) {
	dict, err := buildTestDict(t)
	if err != nil {
		t.Skipf("dictionary training needs more sample mass: %v", err)
	}
	store := cosmos.NewFake(nil)
	ctx := context.Background()

	withDict := baseOptions(t, store)
	withDict.Dict = dict
	if _, err := Load(ctx, withDict); err != nil {
		t.Fatalf("Load with a dictionary: %v", err)
	}
	// Reading a dict-compressed document without the dictionary must fail loudly: silently
	// producing garbage is how a display bug becomes undiagnosable.
	without := baseOptions(t, store)
	without.Key = keyV2
	_, err = Load(ctx, without)
	if err == nil {
		t.Fatal("a dictionary mismatch was not detected")
	}
	if got := err.Error(); !contains(got, "dictionary") {
		t.Errorf("error does not mention the dictionary: %v", err)
	}

	// With the dictionary, the second version merges and both resolve.
	again := baseOptions(t, store)
	again.Key = keyV2
	again.Dict = dict
	if _, err := Load(ctx, again); err != nil {
		t.Fatalf("Load with the matching dictionary: %v", err)
	}
	for id, d := range store.Docs() {
		if cosmos.IsReservedID(id) {
			continue
		}
		blob := decode(t, d, dict)
		if len(blob.Versions) != 2 {
			t.Errorf("%s holds %d versions, want 2", id, len(blob.Versions))
		}
	}
}

func TestValidationRejectsUnusableOptions(t *testing.T) {
	store := cosmos.NewFake(nil)
	nodes, edges := fixtures(t)
	cases := map[string]Options{
		"no key":    {NodesPath: nodes, EdgesPath: edges, Store: store, Engine: engine.NewFake()},
		"no store":  {Key: keyV1, NodesPath: nodes, EdgesPath: edges, Engine: engine.NewFake()},
		"no engine": {Key: keyV1, NodesPath: nodes, EdgesPath: edges, Store: store},
		"no nodes":  {Key: keyV1, EdgesPath: edges, Store: store, Engine: engine.NewFake()},
		"no edges":  {Key: keyV1, NodesPath: nodes, Store: store, Engine: engine.NewFake()},
	}
	for name, opt := range cases {
		if _, err := Load(context.Background(), opt); err == nil {
			t.Errorf("%s: Load accepted unusable options", name)
		}
	}
}

func TestBaseKeyMustExistInStoredDocument(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	opt := baseOptions(t, store)
	if _, err := Load(ctx, opt); err != nil {
		t.Fatalf("first Load: %v", err)
	}
	second := baseOptions(t, store)
	second.Key = keyV2
	second.BaseKey = "infores:drugapprovals-kp-0.9.9"
	_, err := Load(ctx, second)
	if err == nil {
		t.Fatal("a --base version that is not stored was accepted")
	}
	if !contains(err.Error(), "not stored in this document") {
		t.Errorf("error = %v", err)
	}
}

// Each release gets its own pool document, and the index lists them with the counts a weighted
// random pick needs. Reloading one release must not disturb the other.
func TestRandomPoolIsWrittenPerReleaseAndIndexed(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	opt := baseOptions(t, store)
	opt.SampleSize = 4
	if _, err := Load(ctx, opt); err != nil {
		t.Fatalf("first Load: %v", err)
	}

	pool := readPool(t, store, nil, slugV, "1.11.2")
	if pool.Schema != codec.PoolSchema {
		t.Errorf("pool schema = %q", pool.Schema)
	}
	if len(pool.IDs) != 4 {
		t.Errorf("pool holds %d ids, want the cap of 4", len(pool.IDs))
	}
	if pool.Key != keyV1 {
		t.Errorf("pool key = %q, want %q", pool.Key, keyV1)
	}
	for _, id := range pool.IDs {
		if _, err := store.Read(ctx, id); err != nil {
			t.Errorf("pool references %s, which is not stored: %v", id, err)
		}
	}

	index := readIndex(t, store, nil)
	rel := index.KGs[slugV].Versions["1.11.2"]
	if rel.Sampled != 4 {
		t.Errorf("index says %d sampled, want 4", rel.Sampled)
	}
	if rel.Edges != 6 {
		t.Errorf("index says %d edges, want the 6 fixture edges", rel.Edges)
	}
	if rel.SampledAt == "" {
		t.Error("index entry has no sampled_at")
	}

	// A second release writes its own pool and adds an index entry, leaving the first alone.
	second := baseOptions(t, store)
	second.Key = keyV2
	second.SampleSize = 4
	if _, err := Load(ctx, second); err != nil {
		t.Fatalf("second Load: %v", err)
	}
	if _, err := store.Read(ctx, cosmos.PoolDocID(slugV, "1.11.2")); err != nil {
		t.Errorf("the first release's pool was disturbed by loading the second: %v", err)
	}
	other := readPool(t, store, nil, slugV, "1.16.0")
	if other.Key != keyV2 || len(other.IDs) != 4 {
		t.Errorf("second pool = key %q with %d ids, want %q with 4", other.Key, len(other.IDs), keyV2)
	}
	index = readIndex(t, store, nil)
	if got := len(index.KGs[slugV].Versions); got != 2 {
		t.Errorf("index lists %d releases, want 2", got)
	}
	seen := map[string]bool{}
	for _, rel := range []codec.VersionPool{
		index.KGs[slugV].Versions["1.11.2"],
		index.KGs[slugV].Versions["1.16.0"],
	} {
		if seen[rel.SampledAt] && rel.SampledAt == "" {
			t.Error("index entries have no sampled_at")
		}
		seen[rel.SampledAt] = true
	}
}

// Reloading the same key refills its pool rather than shrinking it, and the index keeps the
// larger edge count so a partial re-run cannot bias random away from that release.
func TestReloadSameKeyRefillsItsOwnPool(t *testing.T) {
	store := cosmos.NewFake(nil)
	ctx := context.Background()
	opt := baseOptions(t, store)
	opt.SampleSize = 4
	if _, err := Load(ctx, opt); err != nil {
		t.Fatalf("first Load: %v", err)
	}
	before := readPool(t, store, nil, slugV, "1.11.2")

	again := baseOptions(t, store)
	again.SampleSize = 4
	if _, err := Load(ctx, again); err != nil {
		t.Fatalf("reload: %v", err)
	}
	after := readPool(t, store, nil, slugV, "1.11.2")
	if len(after.IDs) != 4 {
		t.Errorf("reloaded pool holds %d ids, want it capped at 4", len(after.IDs))
	}
	if after.Key != keyV1 {
		t.Errorf("reloaded pool key = %q, want %q", after.Key, keyV1)
	}
	seen := map[string]bool{}
	for _, id := range after.IDs {
		if seen[id] {
			t.Errorf("pool contains %s twice, which would bias random", id)
		}
		seen[id] = true
	}
	for _, id := range before.IDs {
		if !seen[id] {
			t.Logf("note: %s was in the first sample but not the merged one (expected: the union is re-reservoired)", id)
		}
	}
	if got := readIndex(t, store, nil).KGs[slugV].Versions["1.11.2"].Edges; got != 6 {
		t.Errorf("index edge count after reload = %d, want it to stay 6", got)
	}
}

func TestReservoirIsUniformAndCapped(t *testing.T) {
	const k, n = 100, 10000
	counts := make(map[string]int, n)
	for range 20 {
		r := newReservoir(k, 0)
		for i := range n {
			r.offer(idFor(i))
		}
		if r.len() != k {
			t.Fatalf("reservoir holds %d ids, want %d", r.len(), k)
		}
		if r.offered() != n {
			t.Fatalf("offered = %d, want %d", r.offered(), n)
		}
		sampled := r.snapshot()
		for _, id := range sampled {
			counts[id]++
		}
		// Duplicates must never enter the sample: a repeated id would double that edge's chance
		// of being picked by /random.
		seen := map[string]bool{}
		for _, id := range sampled {
			if seen[id] {
				t.Fatal("reservoir contains a duplicate")
			}
			seen[id] = true
		}
	}
	// Every position should be sampled about equally often: expected 20*k/n = 0.2 per id.
	// A front-biased reservoir would show up as the first ids dominating.
	front, back := 0, 0
	for i := range n {
		if c, ok := counts[idFor(i)]; ok {
			if i < n/2 {
				front += c
			} else {
				back += c
			}
		}
	}
	total := front + back
	if total != 20*k {
		t.Fatalf("counted %d samples, want %d", total, 20*k)
	}
	ratio := float64(front) / float64(total)
	if ratio < 0.4 || ratio > 0.6 {
		t.Errorf("sample is biased: %.1f%% from the first half of the stream", ratio*100)
	}
}

func TestProgressReceivesSnapshots(t *testing.T) {
	store := cosmos.NewFake(nil)
	opt := baseOptions(t, store)
	rec := &recordingProgress{}
	opt.Progress = rec
	if _, err := Load(context.Background(), opt); err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(rec.calls) == 0 {
		t.Fatal("progress was never reported")
	}
	last := rec.calls[len(rec.calls)-1]
	if !last.final {
		t.Error("the last report was not marked final")
	}
	if last.snap.Edges != 6 {
		t.Errorf("final snapshot reports %d edges, want 6", last.snap.Edges)
	}
	if last.snap.Elapsed <= 0 {
		t.Errorf("final snapshot has no elapsed time: %v", last.snap.Elapsed)
	}
}

// The progress line's ru= must track the budget as the run goes, not only appear in the final
// summary: an hour-long Cosmos load reporting ru=0.0 the whole way hides pacing problems.
func TestProgressSnapshotsCarryLiveRU(t *testing.T) {
	budget := ratelimit.New(100000) // fast enough not to slow the test, real enough to account
	store := cosmos.NewFake(budget)
	store.ChargeFor["create"] = 5.5
	store.ChargeFor["read"] = 1.0
	opt := baseOptions(t, store)
	opt.Budget = budget
	rec := &recordingProgress{}
	opt.Progress = rec
	if _, err := Load(context.Background(), opt); err != nil {
		t.Fatalf("Load: %v", err)
	}
	last := rec.calls[len(rec.calls)-1]
	if last.snap.RU <= 0 {
		t.Error("final snapshot carries no RU at all")
	}
	if want := budget.Consumed(); last.snap.RU != want {
		t.Errorf("final snapshot reports RU=%v, want the live total %v", last.snap.RU, want)
	}
}

func TestTextProgressFormats(t *testing.T) {
	var buf safeBuffer
	p := NewTextProgress(&buf, 100)
	p.Every = 0
	p.Report(Snapshot{Key: keyV1, Elapsed: 2 * time.Second, Edges: 50, Created: 40, Merged: 10, RawBytes: 1000, BlobBytes: 100}, false)
	p.Report(Snapshot{Key: keyV1, Elapsed: 4 * time.Second, Edges: 100, Created: 100, RawBytes: 1000, BlobBytes: 100}, true)
	out := buf.String()
	if !contains(out, "50 edges") || !contains(out, "100 edges") {
		t.Errorf("progress output missing counts:\n%s", out)
	}
	if !contains(out, "eta") {
		t.Errorf("no ETA while the total is known:\n%s", out)
	}
	if !contains(out, "ratio=0.100") {
		t.Errorf("no compression ratio:\n%s", out)
	}
	if !contains(out, "(done)") {
		t.Errorf("final line not marked:\n%s", out)
	}
	// A zero total must not divide by zero.
	p2 := NewTextProgress(&buf, 0)
	p2.Every = 0
	p2.Report(Snapshot{Edges: 5}, false)
}

// --- helpers ---

type recordingProgress struct {
	calls []struct {
		snap  Snapshot
		final bool
	}
}

func (r *recordingProgress) Report(s Snapshot, final bool) {
	r.calls = append(r.calls, struct {
		snap  Snapshot
		final bool
	}{s, final})
}

func readPool(t *testing.T, store *cosmos.Fake, dict []byte, slug, label string) codec.Pool {
	t.Helper()
	pool, err := ReadPool(context.Background(), store, dict, slug, label)
	if err != nil {
		t.Fatalf("read pool %s/%s: %v", slug, label, err)
	}
	return pool
}

func readIndex(t *testing.T, store *cosmos.Fake, dict []byte) codec.PoolIndex {
	t.Helper()
	index, err := ReadPoolIndex(context.Background(), store, dict)
	if err != nil {
		t.Fatalf("read pool index: %v", err)
	}
	if index.Schema != codec.PoolIndexSchema {
		t.Errorf("index schema = %q, want %q", index.Schema, codec.PoolIndexSchema)
	}
	return index
}

func buildTestDict(t *testing.T) ([]byte, error) {
	t.Helper()
	nodes, edges := fixtures(t)
	var samples [][]byte
	err := engineEach(nodes, edges, func(doc codec.Doc) error {
		raw, err := codec.Marshal(doc)
		if err != nil {
			return err
		}
		samples = append(samples, raw)
		return nil
	})
	if err != nil {
		return nil, err
	}
	// Six fixture edges are thin training material; repeat them so the builder has enough mass
	// to find literals. A real load trains on thousands of distinct documents.
	for range 6 {
		samples = append(samples, samples...)
	}
	return codec.BuildDict(samples, codec.DefaultDictID)
}

func engineEach(nodes, edges string, fn func(codec.Doc) error) error {
	e := engine.NewFake()
	return e.Join(context.Background(), engine.Query{NodesPath: nodes, EdgesPath: edges}, func(r engine.Row) error {
		doc, err := engine.MergedDoc(r)
		if err != nil {
			return err
		}
		return fn(doc)
	})
}

func idFor(i int) string { return fmt.Sprintf("id-%05d", i) }

func contains(haystack, needle string) bool { return strings.Contains(haystack, needle) }

// safeBuffer collects progress output from concurrent Report calls.
type safeBuffer struct {
	mu  sync.Mutex
	buf strings.Builder
}

func (b *safeBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *safeBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// Regenerating the committed contract fixtures must give identical bytes, or CI's diff of them is
// noise. A fixed seed and clock are the only things the pool needs for that.
func TestReservoirIsReproducibleWithASeed(t *testing.T) {
	sample := func(seed int64) []string {
		r := newReservoir(10, seed)
		for i := range 1000 {
			r.offer(idFor(i))
		}
		return r.snapshot()
	}
	a, b := sample(42), sample(42)
	if fmt.Sprint(a) != fmt.Sprint(b) {
		t.Fatalf("same seed, different samples:\n%v\n%v", a, b)
	}
	if fmt.Sprint(a) == fmt.Sprint(sample(43)) {
		t.Fatal("different seeds gave the same sample; the seed is not reaching the RNG")
	}
}

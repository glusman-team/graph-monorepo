package cosmos

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

func testDoc(id string) Doc {
	return Doc{ID: id, Blob: "KLUv/QAMAdw=", DictID: DefaultDictIDForTest}
}

// DefaultDictIDForTest keeps the tests honest about the omitted-zero case: a non-zero dict
// id must survive a round trip, and a zero one must not appear in the stored JSON.
const DefaultDictIDForTest = 0x454C4F31

// closeStore fails the test if Close returns an error. The file store compacts and renames in
// Close, so an unchecked Close can hide lost documents even in a test.
func closeStore(t *testing.T, s Store) {
	t.Helper()
	if err := s.Close(); err != nil {
		t.Errorf("Close: %v", err)
	}
}

func TestFileStoreRoundTripAndReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "docs.ndjson")
	ctx := context.Background()

	store, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("OpenFile: %v", err)
	}
	want := testDoc("575af3e8-8015-3718-be03-4da18a0bacfc")
	if err := store.Create(ctx, want); err != nil {
		t.Fatalf("Create: %v", err)
	}
	got, err := store.Read(ctx, want.ID)
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if got.Blob != want.Blob || got.DictID != want.DictID {
		t.Fatalf("Read = %+v, want %+v", got, want)
	}
	if got.ETag == "" {
		t.Error("stored document has no etag, so Replace preconditions cannot work")
	}
	if err := store.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	// Reopening must see what was written: the web app reads a file the worker wrote in a
	// different process.
	reopened, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer closeStore(t, reopened)
	again, err := reopened.Read(ctx, want.ID)
	if err != nil {
		t.Fatalf("Read after reopen: %v", err)
	}
	if again.Blob != want.Blob {
		t.Errorf("blob changed across reopen: %q", again.Blob)
	}
	if reopened.Count() != 1 {
		t.Errorf("Count = %d, want 1", reopened.Count())
	}
}

func TestFileStoreLastWriteWins(t *testing.T) {
	path := filepath.Join(t.TempDir(), "docs.ndjson")
	ctx := context.Background()
	store, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("OpenFile: %v", err)
	}
	defer closeStore(t, store)

	id := "11111111-1111-3111-8111-111111111111"
	if err := store.Upsert(ctx, Doc{ID: id, Blob: "first"}); err != nil {
		t.Fatalf("Upsert: %v", err)
	}
	if err := store.Upsert(ctx, Doc{ID: id, Blob: "second"}); err != nil {
		t.Fatalf("Upsert: %v", err)
	}
	got, err := store.Read(ctx, id)
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if got.Blob != "second" {
		t.Errorf("Blob = %q, want the newest write", got.Blob)
	}
	if store.Count() != 1 {
		t.Errorf("Count = %d, want 1 (two writes, one document)", store.Count())
	}

	// A fresh open must also resolve to the newest line, not the first.
	reopened, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer closeStore(t, reopened)
	after, err := reopened.Read(ctx, id)
	if err != nil {
		t.Fatalf("Read after reopen: %v", err)
	}
	if after.Blob != "second" {
		t.Errorf("after reopen Blob = %q, want second", after.Blob)
	}
}

func TestFileStoreCreateConflictAndReplacePrecondition(t *testing.T) {
	ctx := context.Background()
	store, err := OpenFile(filepath.Join(t.TempDir(), "docs.ndjson"), nil)
	if err != nil {
		t.Fatalf("OpenFile: %v", err)
	}
	defer closeStore(t, store)

	d := testDoc("22222222-2222-3222-8222-222222222222")
	if err := store.Create(ctx, d); err != nil {
		t.Fatalf("Create: %v", err)
	}
	err = store.Create(ctx, d)
	if !errors.Is(err, ErrConflict) {
		t.Errorf("second Create = %v, want ErrConflict", err)
	}

	stored, err := store.Read(ctx, d.ID)
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if err := store.Replace(ctx, Doc{ID: d.ID, Blob: "v2"}, stored.ETag); err != nil {
		t.Fatalf("Replace with a fresh etag: %v", err)
	}
	err = store.Replace(ctx, Doc{ID: d.ID, Blob: "v3"}, stored.ETag)
	if !errors.Is(err, ErrPreconditionFailed) {
		t.Errorf("Replace with a stale etag = %v, want ErrPreconditionFailed", err)
	}
	if err := store.Replace(ctx, Doc{ID: "nope", Blob: "x"}, ""); !errors.Is(err, ErrNotFound) {
		t.Errorf("Replace of a missing id = %v, want ErrNotFound", err)
	}
	if _, err := store.Read(ctx, "missing"); !errors.Is(err, ErrNotFound) {
		t.Errorf("Read of a missing id = %v, want ErrNotFound", err)
	}
}

func TestFileStoreRejectsBadDocuments(t *testing.T) {
	ctx := context.Background()
	store, err := OpenFile(filepath.Join(t.TempDir(), "docs.ndjson"), nil)
	if err != nil {
		t.Fatalf("OpenFile: %v", err)
	}
	defer closeStore(t, store)

	if err := store.Upsert(ctx, Doc{Blob: "x"}); err == nil {
		t.Error("accepted a document with no id")
	}
	if err := store.Upsert(ctx, Doc{ID: "abc"}); err == nil {
		t.Error("accepted a document with an empty blob")
	}
}

func TestFileStoreRejectsCorruptLine(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "docs.ndjson")
	if err := os.WriteFile(path, []byte("{\"id\":\"a\",\"b\":\"x\"}\nnot json\n"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, err := OpenFile(path, nil); err == nil {
		t.Error("opened a file with a corrupt line; a truncated write would be read as data")
	}
}

func TestFileStoreProvisionIsIdempotent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "docs.ndjson")
	store, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("OpenFile: %v", err)
	}
	defer closeStore(t, store)
	if err := store.Provision(context.Background()); err != nil {
		t.Fatalf("Provision: %v", err)
	}
	if err := store.Provision(context.Background()); err != nil {
		t.Fatalf("second Provision: %v", err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Errorf("Provision did not create the file: %v", err)
	}
}

func TestEncodeDocOmitsZeroDictID(t *testing.T) {
	withDict, err := encodeDoc(Doc{ID: "a", Blob: "b", DictID: DefaultDictIDForTest})
	if err != nil {
		t.Fatalf("encodeDoc: %v", err)
	}
	want := fmt.Sprintf(`{"id":"a","b":"b","d":%d}`, DefaultDictIDForTest)
	if string(withDict) != want {
		t.Errorf("stored shape = %s, want %s", withDict, want)
	}
	// No dictionary means no "d" key at all, so an old reader never sees a zero it might
	// mistake for a real dictionary id.
	noDict, err := encodeDoc(Doc{ID: "a", Blob: "b"})
	if err != nil {
		t.Fatalf("encodeDoc: %v", err)
	}
	if string(noDict) != `{"id":"a","b":"b"}` {
		t.Errorf("stored shape without a dict = %s", noDict)
	}
	if _, err := encodeDoc(Doc{Blob: "b"}); err == nil {
		t.Error("encoded a document with no id")
	}
	if _, err := encodeDoc(Doc{ID: "a"}); err == nil {
		t.Error("encoded a document with no blob")
	}
}

func TestDecodeDoc(t *testing.T) {
	got, err := decodeDoc([]byte(`{"id":"a","b":"blob","d":7}`))
	if err != nil {
		t.Fatalf("decodeDoc: %v", err)
	}
	if got.ID != "a" || got.Blob != "blob" || got.DictID != 7 {
		t.Errorf("decoded = %+v", got)
	}
	// Unknown extra fields must be ignored: Cosmos adds _rid, _ts, _etag, _self.
	if _, err := decodeDoc([]byte(`{"id":"a","b":"x","_rid":"r","_ts":1700000000}`)); err != nil {
		t.Errorf("service metadata broke decoding: %v", err)
	}
	for _, bad := range []string{`{}`, `{"id":"a"}`, `{"b":"x"}`, `nope`} {
		if _, err := decodeDoc([]byte(bad)); err == nil {
			t.Errorf("decodeDoc(%s) accepted an unusable document", bad)
		}
	}
}

func TestFakeStoreFailuresAndCounters(t *testing.T) {
	ctx := context.Background()
	fake := NewFake(nil)
	fake.ChargeFor["upsert"] = 12.5

	d := testDoc("33333333-3333-3333-8333-333333333333")
	if err := fake.Upsert(ctx, d); err != nil {
		t.Fatalf("Upsert: %v", err)
	}
	if got := fake.Docs()[d.ID].Blob; got != d.Blob {
		t.Errorf("stored blob = %q", got)
	}

	// A 409 on create is the normal case for a second KG version claiming an existing edge,
	// and the pipeline has to see it as ErrConflict to merge rather than fail.
	fake.Fail("create", ErrConflict)
	if err := fake.Create(ctx, d); !errors.Is(err, ErrConflict) {
		t.Errorf("armed Create = %v, want ErrConflict", err)
	}
	// One-shot: the next call behaves normally.
	if err := fake.Create(ctx, testDoc("44444444-4444-3444-8444-444444444444")); err != nil {
		t.Errorf("Create after the armed failure: %v", err)
	}

	fake.Fail("replace", ErrPreconditionFailed)
	if err := fake.Replace(ctx, d, "stale"); !errors.Is(err, ErrPreconditionFailed) {
		t.Errorf("armed Replace = %v, want ErrPreconditionFailed", err)
	}

	counts := fake.OpCounts()
	if counts["upsert"] != 1 || counts["create"] != 2 || counts["replace"] != 1 {
		t.Errorf("op counts = %v", counts)
	}
	if fake.Charges != 12.5 {
		t.Errorf("Charges = %v, want 12.5", fake.Charges)
	}
	if _, err := fake.Read(ctx, "missing"); !errors.Is(err, ErrNotFound) {
		t.Errorf("Read of a missing id = %v", err)
	}
}

func TestFakeSeed(t *testing.T) {
	fake := NewFake(nil).Seed(Doc{ID: "seeded", Blob: "b"})
	got, err := fake.Read(context.Background(), "seeded")
	if err != nil {
		t.Fatalf("Read seeded: %v", err)
	}
	if got.ETag == "" {
		t.Error("seeded document has no etag")
	}
	if len(fake.Ops) != 1 || fake.Ops[0] != "read" {
		t.Errorf("Seed should not record an op, got %v", fake.Ops)
	}
}

func TestOpenDispatch(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()

	file, err := Open(ctx, "file:"+filepath.Join(dir, "d.ndjson"), AzureConfig{}, nil)
	if err != nil {
		t.Fatalf("Open file: %v", err)
	}
	defer closeStore(t, file)
	if file.Name() != "file:"+filepath.Join(dir, "d.ndjson") {
		t.Errorf("Name = %q", file.Name())
	}

	mem, err := Open(ctx, "mem://", AzureConfig{}, nil)
	if err != nil {
		t.Fatalf("Open mem: %v", err)
	}
	defer closeStore(t, mem)

	// No account configured must fail loudly rather than silently writing nowhere.
	if _, err := Open(ctx, "cosmos", AzureConfig{}, nil); err == nil {
		t.Error("Open(cosmos) succeeded with no endpoint or key")
	}
	if _, err := Open(ctx, "s3://bucket", AzureConfig{}, nil); err == nil {
		t.Error("Open accepted an unknown scheme")
	}
}

func TestOpenChargesTheBudget(t *testing.T) {
	ctx := context.Background()
	budget := ratelimit.NewUnlimited()
	store, err := Open(ctx, "mem://", AzureConfig{}, budget)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	fake := store.(*Fake)
	fake.ChargeFor["create"] = 6.25
	if err := store.Create(ctx, testDoc("55555555-5555-3555-8555-555555555555")); err != nil {
		t.Fatalf("Create: %v", err)
	}
	if budget.Consumed() != 6.25 {
		t.Errorf("budget consumed %v RU, want 6.25", budget.Consumed())
	}
}

func TestMapErrorLeavesForeignErrorsAlone(t *testing.T) {
	if got := mapError(nil); got != nil {
		t.Errorf("mapError(nil) = %v", got)
	}
	sentinel := errors.New("boom")
	if got := mapError(sentinel); !errors.Is(got, sentinel) {
		t.Errorf("mapError wrapped an unrelated error: %v", got)
	}
}

func TestRandomPoolIDIsReserved(t *testing.T) {
	// The pool document shares the container, so its id must never collide with a UUID.
	var uuid json.RawMessage = []byte(`"__random_pool__"`)
	if string(uuid) != `"__random_pool__"` || RandomPoolID == "" {
		t.Fatal("RandomPoolID must be a fixed reserved id")
	}
}

func TestFileStoreCompactsOnClose(t *testing.T) {
	path := filepath.Join(t.TempDir(), "docs.ndjson")
	ctx := context.Background()

	store, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("OpenFile: %v", err)
	}
	// Three writes to two ids: append-only storage would keep all three lines.
	if err := store.Upsert(ctx, Doc{ID: "b-id", Blob: "b1"}); err != nil {
		t.Fatalf("Upsert: %v", err)
	}
	if err := store.Upsert(ctx, Doc{ID: "a-id", Blob: "a1"}); err != nil {
		t.Fatalf("Upsert: %v", err)
	}
	if err := store.Upsert(ctx, Doc{ID: "b-id", Blob: "b2"}); err != nil {
		t.Fatalf("Upsert: %v", err)
	}
	if err := store.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read back: %v", err)
	}
	lines := strings.Split(strings.TrimRight(string(raw), "\n"), "\n")
	if len(lines) != 2 {
		t.Fatalf("compacted file has %d lines, want one per document:\n%s", len(lines), raw)
	}
	// Sorted by id, so a regenerated golden fixture diffs only when the content changed.
	if !strings.Contains(lines[0], `"id":"a-id"`) || !strings.Contains(lines[1], `"id":"b-id"`) {
		t.Errorf("lines are not sorted by id:\n%s", raw)
	}
	if !strings.Contains(lines[1], `"b":"b2"`) {
		t.Errorf("compaction kept a superseded document:\n%s", lines[1])
	}
	if _, err := os.Stat(path + ".tmp"); !os.IsNotExist(err) {
		t.Errorf("the temporary compaction file was left behind: %v", err)
	}

	reopened, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer closeStore(t, reopened)
	if reopened.Count() != 2 {
		t.Errorf("Count = %d, want 2", reopened.Count())
	}
	got, err := reopened.Read(ctx, "b-id")
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if got.Blob != "b2" {
		t.Errorf("Blob = %q, want the newest write", got.Blob)
	}
}

// The KG slug is stored beside the blob, and like "d" it must be omitted when absent so an old
// reader never sees an empty field it cannot interpret.
func TestEncodeDocCarriesTheKGSlug(t *testing.T) {
	got, err := encodeDoc(Doc{ID: "a", Blob: "b", KG: "drugapprovals-kp"})
	if err != nil {
		t.Fatalf("encodeDoc: %v", err)
	}
	if string(got) != `{"id":"a","b":"b","k":"drugapprovals-kp"}` {
		t.Errorf("stored shape = %s", got)
	}
	both, err := encodeDoc(Doc{ID: "a", Blob: "b", DictID: 7, KG: "kg"})
	if err != nil {
		t.Fatalf("encodeDoc: %v", err)
	}
	if string(both) != `{"id":"a","b":"b","d":7,"k":"kg"}` {
		t.Errorf("stored shape with a dict = %s", both)
	}
	back, err := decodeDoc(got)
	if err != nil {
		t.Fatalf("decodeDoc: %v", err)
	}
	if back.KG != "drugapprovals-kp" {
		t.Errorf("decoded k = %q", back.KG)
	}
	// A document written before the field existed decodes with an empty slug rather than failing.
	old, err := decodeDoc([]byte(`{"id":"a","b":"b"}`))
	if err != nil || old.KG != "" {
		t.Errorf("legacy document decoded to %+v, %v", old, err)
	}
}

func TestFileStoreCarriesTheKGSlugAcrossReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "docs.ndjson")
	ctx := context.Background()
	store, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("OpenFile: %v", err)
	}
	if err := store.Create(ctx, Doc{ID: "id-1", Blob: "b", KG: "drugapprovals-kp"}); err != nil {
		t.Fatalf("Create: %v", err)
	}
	closeStore(t, store)

	reopened, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer closeStore(t, reopened)
	got, err := reopened.Read(ctx, "id-1")
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if got.KG != "drugapprovals-kp" {
		t.Errorf("k = %q after a close/reopen cycle, want it preserved by compaction", got.KG)
	}
}

func TestPoolDocIDs(t *testing.T) {
	id := PoolDocID("drugapprovals-kp", "1.16.0")
	if id != "__random_pool__:drugapprovals-kp:1.16.0" {
		t.Errorf("PoolDocID = %q", id)
	}
	if got := PoolDocID("kg", "1.0.0-rc1"); got != "__random_pool__:kg:1.0.0-rc1" {
		t.Errorf("pre-release PoolDocID = %q", got)
	}
	// Reserved documents must never be mistaken for edges: purge skips them by this test, and a
	// UUID can never start with the reserved prefix.
	if !IsReservedID(RandomPoolID) || !IsReservedID(id) {
		t.Error("IsReservedID missed a reserved document")
	}
	if IsReservedID("575af3e8-8015-3718-be03-4da18a0bacfc") {
		t.Error("IsReservedID claimed an edge document")
	}
}

func TestDeleteAllAndStats(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "docs.ndjson")

	for name, open := range map[string]func() (Store, error){
		"file": func() (Store, error) { return OpenFile(path, nil) },
		"mem":  func() (Store, error) { return NewFake(nil), nil },
	} {
		t.Run(name, func(t *testing.T) {
			store, err := open()
			if err != nil {
				t.Fatalf("open: %v", err)
			}
			defer closeStore(t, store)
			for _, id := range []string{"a", "b", "c"} {
				if err := store.Create(ctx, Doc{ID: id, Blob: "x"}); err != nil {
					t.Fatalf("Create %s: %v", id, err)
				}
			}
			stats, err := store.Stats(ctx)
			if err != nil {
				t.Fatalf("Stats: %v", err)
			}
			if stats.Items != 3 {
				t.Errorf("Stats.Items = %d, want 3", stats.Items)
			}

			// All streams every document exactly once, in id order where the backend can order.
			var seen []string
			if err := store.All(ctx, func(d Doc) error {
				seen = append(seen, d.ID)
				return nil
			}); err != nil {
				t.Fatalf("All: %v", err)
			}
			if strings.Join(seen, ",") != "a,b,c" {
				t.Errorf("All visited %v, want a,b,c in order", seen)
			}
			// A callback error stops the scan rather than being swallowed.
			sentinel := errors.New("stop")
			if err := store.All(ctx, func(Doc) error { return sentinel }); !errors.Is(err, sentinel) {
				t.Errorf("All returned %v, want the callback's error", err)
			}

			if err := store.Delete(ctx, "b"); err != nil {
				t.Fatalf("Delete: %v", err)
			}
			if _, err := store.Read(ctx, "b"); !errors.Is(err, ErrNotFound) {
				t.Errorf("Read after Delete = %v, want ErrNotFound", err)
			}
			if err := store.Delete(ctx, "b"); !errors.Is(err, ErrNotFound) {
				t.Errorf("deleting twice = %v, want ErrNotFound so a re-run is safe", err)
			}
			// Reserved documents delete like any other: that is how purge drops a pool.
			if err := store.Create(ctx, Doc{ID: RandomPoolID, Blob: "x"}); err != nil {
				t.Fatalf("Create reserved: %v", err)
			}
			if err := store.Delete(ctx, PoolDocID("kg", "1.0.0")); !errors.Is(err, ErrNotFound) {
				t.Errorf("deleting an absent pool = %v, want ErrNotFound", err)
			}

			if err := store.DropAll(ctx); err != nil {
				t.Fatalf("DropAll: %v", err)
			}
			stats, err = store.Stats(ctx)
			if err != nil {
				t.Fatalf("Stats after DropAll: %v", err)
			}
			if stats.Items != 0 {
				t.Errorf("Stats.Items after DropAll = %d, want 0", stats.Items)
			}
			// The store stays usable: a wipe is followed by a reload, not by a restart.
			if err := store.Create(ctx, Doc{ID: "after", Blob: "x"}); err != nil {
				t.Fatalf("Create after DropAll: %v", err)
			}
		})
	}
}

// A file store's deletions only reach the file when it compacts on close, so a wipe has to be
// visible to the next process — which is what a purge followed by a reload depends on.
func TestFileStoreDropAllTruncatesTheFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "docs.ndjson")
	ctx := context.Background()
	store, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("OpenFile: %v", err)
	}
	for _, id := range []string{"a", "b"} {
		if err := store.Create(ctx, Doc{ID: id, Blob: "x"}); err != nil {
			t.Fatalf("Create: %v", err)
		}
	}
	if err := store.DropAll(ctx); err != nil {
		t.Fatalf("DropAll: %v", err)
	}
	closeStore(t, store)

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read back: %v", err)
	}
	if len(strings.TrimSpace(string(raw))) != 0 {
		t.Errorf("file still holds documents after a wipe:\n%s", raw)
	}
	reopened, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer closeStore(t, reopened)
	if reopened.Count() != 0 {
		t.Errorf("reopened store holds %d documents, want 0", reopened.Count())
	}
	if err := reopened.Create(ctx, Doc{ID: "fresh", Blob: "x"}); err != nil {
		t.Fatalf("Create into a wiped store: %v", err)
	}
}

// Deleting from a file store must also drop the document from a later compaction, or a purge
// would look like it worked and the documents would come back on the next close.
func TestFileStoreDeleteSurvivesCompaction(t *testing.T) {
	path := filepath.Join(t.TempDir(), "docs.ndjson")
	ctx := context.Background()
	store, err := OpenFile(path, nil)
	if err != nil {
		t.Fatalf("OpenFile: %v", err)
	}
	for _, id := range []string{"keep", "gone"} {
		if err := store.Create(ctx, Doc{ID: id, Blob: "x"}); err != nil {
			t.Fatalf("Create: %v", err)
		}
	}
	if err := store.Delete(ctx, "gone"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	closeStore(t, store)

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read back: %v", err)
	}
	if strings.Contains(string(raw), "gone") {
		t.Errorf("compaction resurrected a deleted document:\n%s", raw)
	}
	if !strings.Contains(string(raw), "keep") {
		t.Errorf("compaction dropped a document it should have kept:\n%s", raw)
	}
}

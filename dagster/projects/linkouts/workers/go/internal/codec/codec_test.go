package codec

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func TestCanonicalSortsKeysAndKeepsNumberText(t *testing.T) {
	doc, err := Parse([]byte(`{"z":1,"a":{"d":4,"b":15.0},"m":[3,2],"big":9007199254740993}`))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	got, err := Marshal(doc)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	want := `{"a":{"b":15.0,"d":4},"big":9007199254740993,"m":[3,2],"z":1}`
	if string(got) != want {
		t.Fatalf("canonical bytes\n got %s\nwant %s", got, want)
	}
}

func TestCanonicalDoesNotEscapeHTML(t *testing.T) {
	// Elixir's JSON leaves <, >, & alone; escaping them here would break byte equality
	// with the web reader and inflate every document that cites a title.
	got, err := Marshal(Doc{"t": `<a href="?x=1&y=2">&</a>`})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if strings.Contains(string(got), `\u003c`) || strings.Contains(string(got), `\u0026`) {
		t.Fatalf("HTML was escaped: %s", got)
	}
	if !strings.Contains(string(got), `<a href=\"?x=1&y=2\">`) {
		t.Fatalf("literal markup missing: %s", got)
	}
}

func TestParseRejectsNullsAtAnyDepth(t *testing.T) {
	cases := []string{
		`{"a":null}`,
		`{"a":{"b":null}}`,
		`{"a":[1,null]}`,
		`null`,
	}
	for _, c := range cases {
		if _, err := Parse([]byte(c)); err == nil {
			t.Errorf("Parse(%s) accepted a null", c)
		}
	}
	// A JSON array is not an object; Parse must say so rather than panic.
	if _, err := Parse([]byte(`[1,2]`)); err == nil {
		t.Error("Parse accepted a top-level array")
	}
}

func TestPruneDropsNullishAndKeepsRealValues(t *testing.T) {
	in := Doc{
		"empty_str":    "",
		"none_str":     "None",
		"null_str":     "NULL",
		"nan":          "nan",
		"na":           "N/A",
		"empty_list":   []any{},
		"nullish_list": []any{"", nil},
		"empty_obj":    Doc{},
		"nil":          nil,
		"zero":         json.Number("0"),
		"false":        false,
		"keep_list":    []any{"a", "", "b"},
		"nested":       Doc{"drop": "none", "keep": "x"},
	}
	pruned, keep := Prune(in)
	if !keep {
		t.Fatal("Prune dropped the whole document")
	}
	got, ok := pruned.(Doc)
	if !ok {
		t.Fatalf("Prune returned %T, want an object", pruned)
	}
	for _, k := range []string{"empty_str", "none_str", "null_str", "nan", "na", "empty_list", "nullish_list", "empty_obj", "nil", "drop"} {
		if _, present := got[k]; present {
			t.Errorf("key %q survived pruning", k)
		}
	}
	// 0 and false are values, not absences. Losing them would silently change KGX semantics.
	if _, present := got["zero"]; !present {
		t.Error("pruned the number 0")
	}
	if v, present := got["false"]; !present || v != false {
		t.Error("pruned the boolean false")
	}
	list, _ := got["keep_list"].([]any)
	if len(list) != 2 || list[0] != "a" || list[1] != "b" {
		t.Errorf("keep_list = %#v, want [a b]", list)
	}
	nested, _ := got["nested"].(Doc)
	if len(nested) != 1 || nested["keep"] != "x" {
		t.Errorf("nested = %#v, want {keep:x}", nested)
	}
	if err := CheckNoNulls(got, "$"); err != nil {
		t.Errorf("pruned document still has nulls: %v", err)
	}
}

func TestDiffApplyRoundTrip(t *testing.T) {
	base := Doc{
		"id":        "abc",
		"subject":   "DRUG:X",
		"predicate": "biolink:contraindicated_for",
		"kept":      "same",
		"dropped":   "gone",
		"pubs":      []any{"p1", "p2"},
		"changed":   json.Number("1"),
	}
	next := Doc{
		"id":        "abc",
		"subject":   "DRUG:X",
		"predicate": "biolink:contraindicated_for",
		"kept":      "same",
		"added":     "new",
		"pubs":      []any{"p1", "p2", "p3"},
		"changed":   json.Number("2"),
	}
	entry, err := Diff("v1", base, next)
	if err != nil {
		t.Fatalf("Diff: %v", err)
	}
	if entry.IsFull() {
		t.Fatal("Diff chose a full store for a three-key change")
	}
	if entry.Base != "v1" {
		t.Errorf("delta targets %q, want v1", entry.Base)
	}
	// The publications list only grew, so it must be an $add, not a re-stored list.
	if add, ok := entry.Add["pubs"]; !ok || len(add) != 1 || add[0] != "p3" {
		t.Errorf("pubs = %#v, want an $add of [p3]", entry.Add)
	}
	if _, ok := entry.Set["pubs"]; ok {
		t.Error("pubs was re-stored in $set despite being an append")
	}
	if entry.Set["changed"] != json.Number("2") {
		t.Errorf("changed = %#v, want 2", entry.Set["changed"])
	}
	if len(entry.Del) != 1 || entry.Del[0] != "dropped" {
		t.Errorf("del = %#v, want [dropped]", entry.Del)
	}

	got, err := Apply(base, entry)
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if !Equal(got, next) {
		gb, _ := Marshal(got)
		nb, _ := Marshal(next)
		t.Fatalf("apply did not reproduce next\n got %s\nwant %s", gb, nb)
	}
}

func TestDiffPrefersFullWhenDeltaIsBigger(t *testing.T) {
	base := Doc{"a": "1"}
	next := Doc{"b": "2", "c": "3", "d": "4", "e": "5"}
	entry, err := Diff("v1", base, next)
	if err != nil {
		t.Fatalf("Diff: %v", err)
	}
	if !entry.IsFull() {
		wire, _ := Marshal(entry.Wire())
		full, _ := Marshal(next)
		t.Fatalf("delta chosen though it is larger: delta=%d full=%d", len(wire), len(full))
	}
}

func TestDiffIdenticalDocuments(t *testing.T) {
	doc := Doc{"a": "1", "b": []any{"x"}}
	entry, err := Diff("v1", doc, doc)
	if err != nil {
		t.Fatalf("Diff: %v", err)
	}
	// Identical versions still need a resolvable payload.
	got, err := Apply(doc, entry)
	if err != nil {
		t.Fatalf("Apply on an identical-version entry: %v", err)
	}
	if !Equal(got, doc) {
		t.Fatal("identical version did not resolve to itself")
	}
}

func TestEntryWireRoundTrip(t *testing.T) {
	entry := Entry{
		Base: "1.11.2",
		Set:  Doc{"k": "v"},
		Add:  map[string][]any{"pubs": {"p"}},
		Del:  []string{"gone"},
	}
	raw, err := Marshal(entry.Wire())
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if !strings.Contains(string(raw), `"$t":"1.11.2"`) {
		t.Fatalf("wire form lost the target: %s", raw)
	}
	back, err := EntryFromWire(mustParse(t, raw))
	if err != nil {
		t.Fatalf("EntryFromWire: %v", err)
	}
	if back.Base != entry.Base || back.Set["k"] != "v" || len(back.Add["pubs"]) != 1 || len(back.Del) != 1 {
		t.Fatalf("wire round trip lost data: %#v", back)
	}

	full := Entry{Full: Doc{"id": "x"}}
	rawFull, err := Marshal(full.Wire())
	if err != nil {
		t.Fatalf("marshal full: %v", err)
	}
	backFull, err := EntryFromWire(mustParse(t, rawFull))
	if err != nil {
		t.Fatalf("EntryFromWire full: %v", err)
	}
	if !backFull.IsFull() || backFull.Full["id"] != "x" {
		t.Fatalf("full payload round trip failed: %#v", backFull)
	}
}

func TestEntryFromWireRejectsGarbage(t *testing.T) {
	bad := []string{
		`{"$t":123}`,                 // target must be a string
		`{"$t":""}`,                  // empty target
		`{"$t":"v1"}`,                // delta with no operations
		`{"$t":"v1","$del":"x"}`,     // $del must be a list
		`{"$t":"v1","$del":[1]}`,     // $del entries must be strings
		`{"$t":"v1","$add":{"k":1}}`, // $add values must be lists
		`{"$t":"v1","$set":3}`,       // $set must be an object
		`[1,2]`,                      // payload must be an object
	}
	for _, b := range bad {
		if _, err := EntryFromWire(mustParseAny(t, b)); err == nil {
			t.Errorf("EntryFromWire(%s) accepted garbage", b)
		}
	}
}

func TestApplyRejectsUnknownAddTarget(t *testing.T) {
	_, err := Apply(Doc{"a": "1"}, Entry{Base: "v1", Add: map[string][]any{"missing": {"x"}}})
	if err == nil {
		t.Fatal("Apply accepted an $add against a non-list key")
	}
	_, err = Apply(nil, Entry{Base: "v1", Set: Doc{"a": "1"}})
	if err == nil {
		t.Fatal("Apply accepted a delta with no base document")
	}
}

func TestBlobEncodeDecodeWithoutDict(t *testing.T) {
	blob, err := NewBlob("1.11.2", Doc{"id": "abc", "subject": "DRUG:X"})
	if err != nil {
		t.Fatalf("NewBlob: %v", err)
	}
	if err := blob.AddVersion("1.16.0", "1.11.2",
		Doc{"id": "abc", "subject": "DRUG:X", "extra": "e"},
		Doc{"id": "abc", "subject": "DRUG:X"}); err != nil {
		t.Fatalf("AddVersion: %v", err)
	}
	encoded, err := blob.Encode(nil, 0)
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	back, err := DecodeBlob(encoded, nil)
	if err != nil {
		t.Fatalf("DecodeBlob: %v", err)
	}
	got, err := back.Resolve("1.16.0")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if got["extra"] != "e" {
		t.Fatalf("resolved doc = %#v", got)
	}
	if keys := back.VersionKeys(); len(keys) != 2 {
		t.Fatalf("versions = %v", keys)
	}
}

func TestBlobEncodeDecodeWithDict(t *testing.T) {
	samples := make([][]byte, 0, 64)
	for i := range 64 {
		samples = append(samples, []byte(`{"schema":"edgelinkouts.blob/1","versions":{"1.11.2":{"id":"aaaaaaaa-0000-0000-0000-`+
			fmt.Sprintf("%012d", i)+`","subject":"CHEMBL.COMPOUND:CHEMBL`+fmt.Sprint(1000+i)+
			`","object":"MONDO:000`+fmt.Sprint(4000+i)+`","predicate":"biolink:contraindicated_for",`+
			`"category":["biolink:Association"],"subject_name":"compound `+fmt.Sprint(i)+
			`","object_name":"disease `+fmt.Sprint(i)+`","knowledge_level":"prediction",`+
			`"publications":["PMID:`+fmt.Sprint(20000000+i)+`","PMID:`+fmt.Sprint(30000000+i)+`"]}}}`))
	}
	dict, err := BuildDict(samples, DefaultDictID)
	if err != nil {
		t.Fatalf("BuildDict: %v", err)
	}
	if id := DictID(dict); id != DefaultDictID {
		t.Fatalf("DictID = %#x, want %#x", id, DefaultDictID)
	}

	blob, err := NewBlob("1.11.2", Doc{
		"id":        "aaaaaaaa-0000-0000-0000-000000000001",
		"subject":   "DRUG:sample",
		"predicate": "biolink:contraindicated_for",
		"category":  []any{"biolink:Association"},
	})
	if err != nil {
		t.Fatalf("NewBlob: %v", err)
	}
	withDict, err := blob.Encode(dict, 0)
	if err != nil {
		t.Fatalf("Encode with dict: %v", err)
	}
	plain, err := blob.Encode(nil, 0)
	if err != nil {
		t.Fatalf("Encode without dict: %v", err)
	}
	if len(withDict) >= len(plain) {
		t.Errorf("dictionary made the blob bigger: dict=%d plain=%d", len(withDict), len(plain))
	}
	back, err := DecodeBlob(withDict, dict)
	if err != nil {
		t.Fatalf("DecodeBlob with dict: %v", err)
	}
	if _, err := back.Resolve("1.11.2"); err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	// A dict-compressed frame must not decode without the dictionary.
	if _, err := DecodeBlob(withDict, nil); err == nil {
		t.Error("decoded a dict-compressed blob with no dictionary")
	}
}

func TestBlobRejectsNullAndDuplicateVersion(t *testing.T) {
	blob, err := NewBlob("v1", Doc{"id": "x"})
	if err != nil {
		t.Fatalf("NewBlob: %v", err)
	}
	if err := blob.AddVersion("v1", "", Doc{"id": "y"}, nil); err == nil {
		t.Error("duplicate version accepted")
	}
	if err := blob.AddVersion("v2", "v1", Doc{"id": "y", "bad": nil}, nil); err == nil {
		t.Error("null in a version payload accepted")
	}
	if err := blob.AddVersion("", "", Doc{"id": "y"}, nil); err == nil {
		t.Error("empty version key accepted")
	}
	big := Doc{"id": "y", "subject": strings.Repeat("s", 200), "object": strings.Repeat("o", 200)}
	bigger := Doc{"id": "y", "subject": strings.Repeat("s", 200), "object": strings.Repeat("o", 200), "n": "1"}
	if err := blob.AddVersion("v9", "missing", bigger, big); err == nil {
		t.Error("accepted a delta whose base version is not stored in the blob")
	} else if !strings.Contains(err.Error(), "not stored in this blob") {
		t.Errorf("wrong error for a dangling delta base: %v", err)
	}
}

func TestBlobResolveDetectsCycle(t *testing.T) {
	blob := &Blob{Schema: SchemaVersion, Versions: map[string]Entry{
		"v1": {Base: "v2", Set: Doc{"a": "1"}},
		"v2": {Base: "v1", Set: Doc{"b": "2"}},
	}}
	if _, err := blob.Resolve("v1"); err == nil || !strings.Contains(err.Error(), "cycle") {
		t.Fatalf("Resolve on a cycle = %v, want a cycle error", err)
	}
	if _, err := blob.Resolve("nope"); err == nil {
		t.Error("Resolve accepted an unknown version")
	}
}

func TestDecodeBlobRejectsWrongSchema(t *testing.T) {
	raw, err := Marshal(map[string]any{"schema": "something/else", "versions": map[string]any{"v1": map[string]any{"id": "x"}}})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	comp, err := Compress(raw, nil, 0)
	if err != nil {
		t.Fatalf("compress: %v", err)
	}
	encoded := encode64(comp)
	if _, err := DecodeBlob(encoded, nil); err == nil || !strings.Contains(err.Error(), "schema") {
		t.Fatalf("DecodeBlob = %v, want a schema error", err)
	}
}

func TestSampleDocsSpacesEvenly(t *testing.T) {
	docs := make([][]byte, 100)
	for i := range docs {
		docs[i] = []byte{byte(i)}
	}
	got := SampleDocs(docs, 10)
	if len(got) != 10 {
		t.Fatalf("len = %d, want 10", len(got))
	}
	if got[0][0] != 0 || got[9][0] != 90 {
		t.Fatalf("first=%d last=%d, want 0 and 90", got[0][0], got[9][0])
	}
	if all := SampleDocs(docs, 0); len(all) != 100 {
		t.Fatalf("limit 0 returned %d docs, want all", len(all))
	}
}

func TestCompressDecompressRoundTrip(t *testing.T) {
	raw := []byte(`{"a":"` + strings.Repeat("payload ", 200) + `"}`)
	for _, level := range []int{1, 3, 9, 19} {
		comp, err := Compress(raw, nil, level)
		if err != nil {
			t.Fatalf("level %d compress: %v", level, err)
		}
		back, err := Decompress(comp, nil)
		if err != nil {
			t.Fatalf("level %d decompress: %v", level, err)
		}
		if string(back) != string(raw) {
			t.Fatalf("level %d round trip changed bytes", level)
		}
	}
	if _, err := Decompress([]byte("not zstd"), nil); err == nil {
		t.Error("Decompress accepted garbage")
	}
}

// --- helpers ---

func mustParse(t *testing.T, b []byte) any {
	t.Helper()
	v, err := Parse(b)
	if err != nil {
		t.Fatalf("parse %s: %v", b, err)
	}
	return v
}

func mustParseAny(t *testing.T, s string) any {
	t.Helper()
	v, err := ParseAny([]byte(s))
	if err != nil {
		t.Fatalf("parse %s: %v", s, err)
	}
	return v
}

func encode64(b []byte) string { return base64.StdEncoding.EncodeToString(b) }

// Reloading an older version must not create a cycle: the blob already holds a delta that
// points at the version being rewritten.
func TestAddVersionStoresFullWhenDependedOn(t *testing.T) {
	blob, err := NewBlob("v1", Doc{"id": "x", "a": strings.Repeat("s", 100)})
	if err != nil {
		t.Fatalf("NewBlob: %v", err)
	}
	base := Doc{"id": "x", "a": strings.Repeat("s", 100)}
	next := Doc{"id": "x", "a": strings.Repeat("s", 100), "b": "1"}
	if err := blob.AddVersion("v2", "v1", next, base); err != nil {
		t.Fatalf("AddVersion v2: %v", err)
	}
	if blob.Versions["v2"].IsFull() {
		t.Fatal("v2 should be a delta against v1")
	}

	// Now rewrite v1, the way the pipeline does: drop the stored version, then add it back.
	// A delta from v1 to v2 would cycle, so v1 must be stored whole.
	rewritten := Doc{"id": "x", "a": strings.Repeat("s", 100), "c": "2"}
	delete(blob.Versions, "v1")
	if err := blob.AddVersion("v1", "v2", rewritten, next); err != nil {
		t.Fatalf("AddVersion v1: %v", err)
	}
	if !blob.Versions["v1"].IsFull() {
		t.Error("v1 was stored as a delta though v2 depends on it; that is a cycle")
	}
	for _, v := range []string{"v1", "v2"} {
		if _, err := blob.Resolve(v); err != nil {
			t.Errorf("Resolve(%s) after the rewrite: %v", v, err)
		}
	}
	got, err := blob.Resolve("v2")
	if err != nil {
		t.Fatalf("Resolve v2: %v", err)
	}
	if got["b"] != "1" {
		t.Errorf("v2 resolved to %#v, want the delta applied to the rewritten v1", got)
	}
}

// A write that would leave the blob unresolvable must be rolled back, not half-applied.
func TestAddVersionRollsBackOnFailure(t *testing.T) {
	blob, err := NewBlob("v1", Doc{"id": "x"})
	if err != nil {
		t.Fatalf("NewBlob: %v", err)
	}
	before := len(blob.Versions)
	err = blob.AddVersion("v2", "v1", Doc{"id": "x", "list": []any{"a"}}, Doc{"id": "x", "list": "not-a-list"})
	if err == nil {
		// A list that stopped being a list is a legitimate $set, so this may succeed; assert
		// only that whatever happened left the blob resolvable.
		for _, v := range blob.VersionKeys() {
			if _, rerr := blob.Resolve(v); rerr != nil {
				t.Fatalf("blob left unresolvable after a successful write: %v", rerr)
			}
		}
		return
	}
	if len(blob.Versions) != before {
		t.Errorf("a failed AddVersion left %d versions behind, want %d", len(blob.Versions), before)
	}
}

// The index document is the one the Elixir reader must reproduce byte for byte, and it is nested
// three deep (kgs → versions → counts), so every level has to sort.
func TestPoolIndexCanonicalForm(t *testing.T) {
	index := PoolIndex{Schema: PoolIndexSchema}
	index.Set("zeta-kg", "1.0.0", 10, 4, "2026-01-01T00:00:00Z")
	index.Set("alpha-kg", "1.16.0", 130211, 1024, "2026-01-02T00:00:00Z")
	index.Set("alpha-kg", "1.11.2", 129807, 1024, "2026-01-01T00:00:00Z")

	raw, err := Marshal(index)
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	want := `{"kgs":{"alpha-kg":{"versions":{"1.11.2":{"edges":129807,"sampled":1024,` +
		`"sampled_at":"2026-01-01T00:00:00Z"},"1.16.0":{"edges":130211,"sampled":1024,` +
		`"sampled_at":"2026-01-02T00:00:00Z"}}},"zeta-kg":{"versions":{"1.0.0":{"edges":10,` +
		`"sampled":4,"sampled_at":"2026-01-01T00:00:00Z"}}}},"schema":"edgelinkouts.pool_index/1"}`
	if string(raw) != want {
		t.Errorf("canonical index =\n%s\nwant\n%s", raw, want)
	}

	encoded, err := EncodeJSON(index, nil, 0)
	if err != nil {
		t.Fatalf("EncodeJSON: %v", err)
	}
	var back PoolIndex
	if err := DecodeJSON(encoded, nil, &back); err != nil {
		t.Fatalf("DecodeJSON: %v", err)
	}
	if back.Schema != PoolIndexSchema {
		t.Errorf("schema = %q", back.Schema)
	}
	rel := back.KGs["alpha-kg"].Versions["1.16.0"]
	if rel.Edges != 130211 || rel.Sampled != 1024 || rel.SampledAt != "2026-01-02T00:00:00Z" {
		t.Errorf("round-tripped release = %+v", rel)
	}
	if len(back.KGs) != 2 || len(back.KGs["alpha-kg"].Versions) != 2 {
		t.Errorf("round-tripped index shape = %v", back.KGs)
	}
}

// Reloading a release must not shrink its weight, or random would drift away from it.
func TestPoolIndexSetKeepsTheLargerEdgeCount(t *testing.T) {
	index := PoolIndex{Schema: PoolIndexSchema}
	index.Set("kg", "1.0.0", 130211, 1024, "2026-01-01T00:00:00Z")
	index.Set("kg", "1.0.0", 7, 7, "2026-01-02T00:00:00Z")
	got := index.KGs["kg"].Versions["1.0.0"]
	if got.Edges != 130211 {
		t.Errorf("edges = %d, want the larger 130211", got.Edges)
	}
	// The fresher sample size and timestamp still describe the pool that is on disk now.
	if got.Sampled != 7 || got.SampledAt != "2026-01-02T00:00:00Z" {
		t.Errorf("entry = %+v, want sampled 7 at the later time", got)
	}
}

func TestPoolIndexRemoveDropsTheGraphWithItsLastRelease(t *testing.T) {
	index := PoolIndex{Schema: PoolIndexSchema}
	index.Set("kg", "1.0.0", 5, 5, "t1")
	index.Set("kg", "2.0.0", 6, 6, "t2")
	if !index.Remove("kg", "1.0.0") {
		t.Fatal("Remove reported nothing removed")
	}
	if len(index.KGs["kg"].Versions) != 1 {
		t.Errorf("versions = %v, want only 2.0.0 left", index.KGs["kg"].Versions)
	}
	if !index.Remove("kg", "2.0.0") {
		t.Fatal("Remove reported nothing removed")
	}
	if _, stillThere := index.KGs["kg"]; stillThere {
		t.Error("a graph with no releases left was kept in the index")
	}
	if index.Remove("kg", "1.0.0") {
		t.Error("removing an absent release reported success")
	}
	if index.Remove("other", "1.0.0") {
		t.Error("removing from an absent graph reported success")
	}
}

// Removing the version a delta points at would leave that delta unresolvable forever, so the
// dependent has to be materialized in full first — and must resolve to exactly what it did before.
func TestRemoveVersionMaterializesDependents(t *testing.T) {
	base := Doc{"id": "abc", "subject": "DRUG:X", "note": "old"}
	middle := Doc{"id": "abc", "subject": "DRUG:X", "note": "new"}
	latest := Doc{"id": "abc", "subject": "DRUG:Y", "note": "new"}

	blob, err := NewBlob("1.0.0", base)
	if err != nil {
		t.Fatalf("NewBlob: %v", err)
	}
	if err := blob.AddVersion("2.0.0", "1.0.0", middle, base); err != nil {
		t.Fatalf("AddVersion 2.0.0: %v", err)
	}
	if err := blob.AddVersion("3.0.0", "2.0.0", latest, middle); err != nil {
		t.Fatalf("AddVersion 3.0.0: %v", err)
	}
	if blob.Versions["2.0.0"].IsFull() || blob.Versions["3.0.0"].IsFull() {
		t.Fatal("the fixture should store both later versions as deltas")
	}

	removed, err := blob.RemoveVersion("1.0.0")
	if err != nil {
		t.Fatalf("RemoveVersion: %v", err)
	}
	if !removed {
		t.Fatal("RemoveVersion reported the version was absent")
	}
	if _, still := blob.Versions["1.0.0"]; still {
		t.Error("1.0.0 was not removed")
	}
	if !blob.Versions["2.0.0"].IsFull() {
		t.Error("2.0.0 was left as a delta pointing at a version that no longer exists")
	}
	got, err := blob.Resolve("2.0.0")
	if err != nil {
		t.Fatalf("Resolve 2.0.0 after removal: %v", err)
	}
	if got["note"] != "new" || got["subject"] != "DRUG:X" {
		t.Errorf("2.0.0 resolved to %#v, want the document it had before", got)
	}
	// 3.0.0 still deltas against 2.0.0, which is now full, so the chain still walks.
	got3, err := blob.Resolve("3.0.0")
	if err != nil {
		t.Fatalf("Resolve 3.0.0 after removal: %v", err)
	}
	if got3["subject"] != "DRUG:Y" {
		t.Errorf("3.0.0 resolved to %#v", got3)
	}
	// The result must still survive the wire, which is the only form it will ever be read in.
	encoded, err := blob.Encode(nil, 0)
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	back, err := DecodeBlob(encoded, nil)
	if err != nil {
		t.Fatalf("DecodeBlob: %v", err)
	}
	if keys := back.VersionKeys(); len(keys) != 2 {
		t.Errorf("re-encoded blob holds %v, want 2.0.0 and 3.0.0", keys)
	}
}

func TestRemoveVersionAbsentAndLast(t *testing.T) {
	blob, err := NewBlob("1.0.0", Doc{"id": "abc"})
	if err != nil {
		t.Fatalf("NewBlob: %v", err)
	}
	if removed, err := blob.RemoveVersion("9.9.9"); err != nil || removed {
		t.Errorf("removing an absent version = (%v, %v), want (false, nil)", removed, err)
	}
	if len(blob.Versions) != 1 {
		t.Errorf("an absent removal changed the blob: %v", blob.VersionKeys())
	}
	removed, err := blob.RemoveVersion("1.0.0")
	if err != nil || !removed {
		t.Fatalf("removing the only version = (%v, %v)", removed, err)
	}
	if len(blob.Versions) != 0 {
		t.Errorf("versions = %v, want none — the caller deletes the document", blob.VersionKeys())
	}
}

// --- storage format v2: derived dictionary ids, reused coders, CRC-less frames -------------

func TestDictIDForSamplesIsDeterministicAndCorpusBound(t *testing.T) {
	// The id is the only way a reader knows which dictionary a frame needs, so it must be a
	// pure function of the corpus: retraining the same KG keeps the id (and stored docs stay
	// decodable), and a different KG gets a different id (and the decode fails loudly).
	a := [][]byte{[]byte(`{"k":1}`), []byte(`{"k":2}`)}
	b := [][]byte{[]byte(`{"z":true}`), []byte(`{"z":false}`)}
	idA1, idA2 := DictIDForSamples(a), DictIDForSamples(a)
	if idA1 != idA2 {
		t.Fatal("same corpus derived two different ids")
	}
	if DictIDForSamples(a) == DictIDForSamples(b) {
		t.Fatal("different corpora derived the same id")
	}
	if id := DictIDForSamples(a); id < minCustomDictID {
		t.Fatalf("id %#x collides with the zstd-registered range (< 32768)", id)
	}
}

func TestCompressReusesTheEncoderAndOmitsCRC(t *testing.T) {
	samples := [][]byte{[]byte(`{"schema":"x","versions":{}}`), []byte(`{"schema":"x","versions":{"v":{}}}`)}
	dict, err := BuildDict(samples, 0)
	if err != nil {
		t.Fatalf("train: %v", err)
	}
	raw := []byte(`{"schema":"x","versions":{"v":{"a":1}}}`)

	// First call warms the cache; the second must hit it. Without reuse this is where the
	// 25 ms/doc dictionary digest cost hides.
	if _, err := Compress(raw, dict, 0); err != nil {
		t.Fatalf("compress: %v", err)
	}
	if _, ok := encoders.Load(encoderKey{level: DefaultZstdLevelWithDict, dictID: DictID(dict)}); !ok {
		t.Fatal("dict-compressed frame did not reuse a cached encoder")
	}
	// A level of 0 with a dictionary must resolve to the dict default (19), not the plain 3:
	// dict frames are written once per release, so the expensive level is the right default.
	comp, err := Compress(raw, dict, 0)
	if err != nil {
		t.Fatalf("compress again: %v", err)
	}
	// zstd frame header descriptor byte (index 4): bit 2 (0x04) is the content-checksum flag.
	// Frames carry no CRC; transport (TLS) and Cosmos's storage already checksum these bytes.
	if comp[4]&0x04 != 0 {
		t.Fatal("frame still carries a CRC (flag bit set)")
	}
	out, err := Decompress(comp, dict)
	if err != nil {
		t.Fatalf("decompress: %v", err)
	}
	if string(out) != string(raw) {
		t.Fatalf("round trip mismatch: %s", out)
	}
	if _, ok := decoders.Load(DictID(dict)); !ok {
		t.Fatal("decoder was not cached either")
	}
}

func TestCompressLevelZeroWithoutDictKeepsLevel3(t *testing.T) {
	// No dictionary: the fast default stays fast, and stays cacheable under dictID 0.
	raw := []byte(`{"a":1}`)
	if _, err := Compress(raw, nil, 0); err != nil {
		t.Fatalf("compress: %v", err)
	}
	if _, ok := encoders.Load(encoderKey{level: DefaultZstdLevel, dictID: 0}); !ok {
		t.Fatal("no-dict frame was not cached under the level-3 key")
	}
}

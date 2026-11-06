package codec

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"testing"
)

// FuzzCanonicalStable asserts the property the cross-language contract rests on: canonical
// bytes are a fixed point. Marshal(Parse(Marshal(x))) == Marshal(x), so the Elixir reader
// and the Go writer can hash the same document and agree, and a re-encode during a repack
// never changes stored bytes.
func FuzzCanonicalStable(f *testing.F) {
	f.Add([]byte(`{"a":1,"b":[1,2,3],"c":{"d":"x"}}`))
	f.Add([]byte(`{"z":1,"a":2,"m":{"q":[true,false]}}`))
	f.Add([]byte(`{"n":15.0,"big":9007199254740993,"s":"<a>&b"}`))
	f.Add([]byte(`{"unicode":"héllo → 世界","emoji":"🧬"}`))
	f.Add([]byte(`{}`))
	f.Fuzz(func(t *testing.T, data []byte) {
		first, err := Parse(data)
		if err != nil {
			t.Skip() // not a null-free JSON object: nothing to assert
		}
		once, err := Marshal(first)
		if err != nil {
			t.Fatalf("marshal: %v", err)
		}
		second, err := Parse(once)
		if err != nil {
			t.Fatalf("re-parse of our own output failed: %v (%s)", err, once)
		}
		twice, err := Marshal(second)
		if err != nil {
			t.Fatalf("re-marshal: %v", err)
		}
		if string(once) != string(twice) {
			t.Fatalf("canonical form is not a fixed point\nfirst  %s\nsecond %s", once, twice)
		}
		if !Equal(first, second) {
			t.Fatalf("round trip changed the value\n%s\n%s", once, twice)
		}
	})
}

// FuzzDeltaRoundTrip asserts Apply(base, Diff(base, next)) == next for arbitrary document
// pairs. This is the invariant that makes version deltas safe to store: if it ever breaks,
// the UI silently shows one version's evidence under another version's label.
func FuzzDeltaRoundTrip(f *testing.F) {
	f.Add([]byte{1, 2, 3, 4, 5, 6, 7, 8})
	f.Add([]byte{0xff, 0x00, 0x7f, 9, 9, 9, 2, 1})
	f.Fuzz(func(t *testing.T, seed []byte) {
		if len(seed) < 2 {
			t.Skip()
		}
		rng := rand.New(rand.NewSource(int64(seed[0])<<8 | int64(seed[1])))
		for i := range len(seed) - 2 {
			base := randomDoc(rng, seed[i])
			next := mutateDoc(rng, base, seed[i+1], seed[i+2])
			entry, err := Diff("v1", base, next)
			if err != nil {
				t.Fatalf("Diff: %v", err)
			}
			// The entry must survive the wire form, which is what gets stored.
			raw, err := Marshal(entry.Wire())
			if err != nil {
				t.Fatalf("marshal entry: %v", err)
			}
			parsed, err := Parse(raw)
			if err != nil {
				t.Fatalf("entry wire form contains a null or is not an object: %v (%s)", err, raw)
			}
			back, err := EntryFromWire(parsed)
			if err != nil {
				t.Fatalf("EntryFromWire: %v (%s)", err, raw)
			}
			got, err := Apply(base, back)
			if err != nil {
				t.Fatalf("Apply: %v (%s)", err, raw)
			}
			if !Equal(got, next) {
				gb, _ := Marshal(got)
				nb, _ := Marshal(next)
				bb, _ := Marshal(base)
				t.Fatalf("delta round trip mismatch\nbase %s\nnext %s\ngot  %s\nwire %s", bb, nb, gb, raw)
			}
		}
	})
}

// FuzzBlobRoundTrip asserts a multi-version blob survives encode/decode with and without a
// dictionary, and that every version still resolves to the document that went in.
func FuzzBlobRoundTrip(f *testing.F) {
	f.Add([]byte{3, 1, 4, 1, 5, 9})
	f.Add([]byte{2, 7, 1, 8, 2, 8})
	f.Fuzz(func(t *testing.T, seed []byte) {
		if len(seed) < 3 {
			t.Skip()
		}
		rng := rand.New(rand.NewSource(int64(seed[0])<<16 | int64(seed[1])<<8 | int64(seed[2])))
		docs := make([]Doc, 0, len(seed))
		cur := randomDoc(rng, seed[0])
		for i, b := range seed {
			cur = mutateDoc(rng, cur, b, byte(i))
			docs = append(docs, cur)
		}
		blob, err := NewBlob("v0", docs[0])
		if err != nil {
			t.Fatalf("NewBlob: %v", err)
		}
		for i, d := range docs[1:] {
			if err := blob.AddVersion(fmt.Sprintf("v%d", i+1), fmt.Sprintf("v%d", i), d, docs[i]); err != nil {
				t.Fatalf("AddVersion v%d: %v", i+1, err)
			}
		}
		for _, dict := range [][]byte{nil, testDict(t, docs)} {
			encoded, err := blob.Encode(dict, 0)
			if err != nil {
				t.Fatalf("Encode: %v", err)
			}
			back, err := DecodeBlob(encoded, dict)
			if err != nil {
				t.Fatalf("DecodeBlob: %v", err)
			}
			for i, want := range docs {
				got, err := back.Resolve(fmt.Sprintf("v%d", i))
				if err != nil {
					t.Fatalf("Resolve v%d: %v", i, err)
				}
				if !Equal(got, want) {
					gb, _ := Marshal(got)
					wb, _ := Marshal(want)
					t.Fatalf("version v%d changed through the blob\ngot  %s\nwant %s", i, gb, wb)
				}
			}
		}
	})
}

// --- generators ---

func testDict(t *testing.T, docs []Doc) []byte {
	t.Helper()
	samples := make([][]byte, 0, len(docs)*4)
	for i := range 4 {
		for _, d := range docs {
			b, err := Marshal(d)
			if err != nil {
				t.Fatalf("marshal sample: %v", err)
			}
			samples = append(samples, append([]byte(fmt.Sprintf(`{"schema":%q,"versions":{"v%d":`, SchemaVersion, i)), append(b, []byte("}}")...)...))
		}
	}
	dict, err := BuildDict(samples, DefaultDictID)
	if err != nil {
		t.Skipf("dictionary training needs more sample mass than this input: %v", err)
	}
	return dict
}

var fuzzKeys = []string{
	"id", "subject", "object", "predicate", "subject_name", "object_name",
	"category", "publications", "knowledge_level", "number_of_cases", "sources", "primary_knowledge_source",
}

func randomDoc(rng *rand.Rand, seed byte) Doc {
	d := Doc{"id": fmt.Sprintf("%08x-0000-3000-8000-%012d", seed, seed)}
	for range rng.Intn(6) + 2 {
		d[fuzzKeys[rng.Intn(len(fuzzKeys))]] = randomValue(rng, 2)
	}
	return d
}

func randomValue(rng *rand.Rand, depth int) any {
	switch n := rng.Intn(7); {
	case n == 0 && depth > 0:
		list := make([]any, rng.Intn(4))
		for i := range list {
			list[i] = randomValue(rng, depth-1)
		}
		return list
	case n == 1 && depth > 0:
		n := rng.Intn(3) + 1
		obj := make(Doc, n)
		for i := range n {
			obj[fmt.Sprintf("k%d", i)] = randomValue(rng, depth-1)
		}
		return obj
	case n == 2:
		return json.Number(fmt.Sprint(rng.Intn(1000)))
	case n == 3:
		// A decimal, because KGX carries floats and their text must survive verbatim.
		return json.Number(fmt.Sprintf("%d.%d", rng.Intn(100), rng.Intn(10)))
	case n == 4:
		return rng.Intn(2) == 0
	default:
		return fmt.Sprintf("v%d", rng.Intn(50))
	}
}

// mutateDoc derives a document that differs from base in the ways KGX releases actually
// differ: a changed scalar, a grown list, a dropped key, an added key.
func mutateDoc(rng *rand.Rand, base Doc, b1, b2 byte) Doc {
	out := Clone(base).(Doc)
	key := fuzzKeys[int(b1)%len(fuzzKeys)]
	switch int(b2) % 5 {
	case 0:
		delete(out, key)
	case 1:
		out[key] = randomValue(rng, 1)
	case 2:
		list, ok := out[key].([]any)
		if !ok {
			list = []any{"seed"}
		}
		out[key] = append(append([]any{}, list...), fmt.Sprintf("extra-%d", b2))
	case 3:
		out[key+"_new"] = randomValue(rng, 0)
	default:
		out[key] = json.Number(fmt.Sprint(int(b2)))
	}
	if len(out) == 0 {
		out["id"] = "fallback"
	}
	return out
}

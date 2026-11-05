package codec

import (
	"encoding/base64"
	"fmt"
	"sort"
	"sync"

	"github.com/klauspost/compress/zstd"
)

// SchemaVersion is stamped into every blob so a reader can refuse a format it does not
// implement instead of mis-decoding one.
const SchemaVersion = "edgelinkouts.blob/1"

// DefaultZstdLevel is zstd's level 3: near-LZ4 speed with a ratio that matters at this
// document size. Override with --zstd-level.
const DefaultZstdLevel = 3

// DefaultZstdLevelWithDict is the level the write path uses when a dictionary is in play:
// dict frames are written once per release and read forever, and level 19 with the trained
// tables is what pushed 91% of measured documents under the 1 KB RU step (median 474 B,
// down from 970 B without a dictionary). Compression cost only matters to the loader.
const DefaultZstdLevelWithDict = 19

// Blob is one Cosmos document: every version of one edge, keyed by KGX release.
// Cosmos indexes only `id`, so the whole document is read in one point read and every
// version toggle in the UI is free after that.
type Blob struct {
	Schema   string           `json:"schema"`
	Versions map[string]Entry `json:"versions"`
}

// MarshalJSON implements json.Marshaler so Entry's wire form ("$t"/"$set"/... or a full
// document) is what lands in the blob rather than Go field names.
func (e Entry) MarshalJSON() ([]byte, error) { return canon.Marshal(e.Wire()) }

// UnmarshalJSON implements json.Unmarshaler.
func (e *Entry) UnmarshalJSON(b []byte) error {
	v, err := ParseAny(b)
	if err != nil {
		return err
	}
	parsed, err := EntryFromWire(v)
	if err != nil {
		return err
	}
	*e = parsed
	return nil
}

// NewBlob builds a blob whose first version is stored in full.
func NewBlob(version string, doc Doc) (*Blob, error) {
	if err := CheckNoNulls(doc, "$"); err != nil {
		return nil, err
	}
	return &Blob{Schema: SchemaVersion, Versions: map[string]Entry{version: {Full: Clone(doc).(Doc)}}}, nil
}

// AddVersion stores doc under version, diffing against baseVersion/baseDoc (usually the
// previously stored version of this edge) and keeping whichever of full-or-delta is
// smaller. baseDoc may be nil, which stores this version in full.
func (b *Blob) AddVersion(version, baseVersion string, doc, baseDoc Doc) error {
	if version == "" {
		return fmt.Errorf("empty version key")
	}
	if _, dup := b.Versions[version]; dup {
		return fmt.Errorf("version %q already present", version)
	}
	if err := CheckNoNulls(doc, "$"); err != nil {
		return fmt.Errorf("version %s: %w", version, err)
	}
	entry, err := Diff(baseVersion, baseDoc, doc)
	if err != nil {
		return fmt.Errorf("version %s: %w", version, err)
	}
	if !entry.IsFull() {
		// A delta whose base is not in this blob can never be resolved, so refuse it at write
		// time instead of at read time in production.
		if _, ok := b.Versions[entry.Base]; !ok {
			return fmt.Errorf("version %s: delta targets %q, which is not stored in this blob", version, entry.Base)
		}
		// Storing a delta for a version that other deltas already point at would create a
		// cycle: reloading 1.11.2 into a blob whose 1.16.0 targets it means 1.11.2 would have
		// to target 1.16.0, which targets 1.11.2. Such a version is stored whole instead.
		if b.dependsOn(version) {
			entry = Entry{Full: Clone(doc).(Doc)}
		}
	}
	b.Versions[version] = entry
	// Every version must still resolve after the write. This is cheap relative to a network
	// round trip and turns a corrupt blob into a failed load rather than a broken page view.
	for _, v := range b.VersionKeys() {
		if _, err := b.Resolve(v); err != nil {
			delete(b.Versions, version)
			return fmt.Errorf("version %s: %w", version, err)
		}
	}
	return nil
}

// dependsOn reports whether any stored delta resolves against version.
func (b *Blob) dependsOn(version string) bool {
	for key, entry := range b.Versions {
		if key != version && !entry.IsFull() && entry.Base == version {
			return true
		}
	}
	return false
}

// VersionKeys returns the stored versions in insertion-stable sorted order. Sorted, not
// semver-sorted: KGX versions are opaque strings here, and the UI orders them itself.
func (b *Blob) VersionKeys() []string {
	out := make([]string, 0, len(b.Versions))
	for k := range b.Versions {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// Resolve expands version into a full document, walking the delta chain. It refuses cycles
// and unknown targets rather than looping forever on a corrupt blob.
func (b *Blob) Resolve(version string) (Doc, error) {
	return b.resolve(version, map[string]bool{})
}

// resolve carries the visited set through the recursion. A per-call set would let a cycle
// recurse until the goroutine stack dies, which is exactly what a corrupt blob from the
// network should not be able to do.
func (b *Blob) resolve(version string, seen map[string]bool) (Doc, error) {
	if seen[version] {
		return nil, fmt.Errorf("delta cycle at version %q", version)
	}
	// Mark for the duration of this path only: a blob where two versions share one base
	// is a diamond, not a cycle, and un-marking on the way out keeps those resolvable.
	seen[version] = true
	defer delete(seen, version)
	entry, ok := b.Versions[version]
	if !ok {
		return nil, fmt.Errorf("version %q not in blob (have %v)", version, b.VersionKeys())
	}
	if entry.IsFull() {
		out, ok := Clone(entry.Full).(Doc)
		if !ok {
			return nil, fmt.Errorf("version %q: full payload did not clone to an object", version)
		}
		return out, nil
	}
	base, err := b.resolve(entry.Base, seen)
	if err != nil {
		return nil, err
	}
	out, err := Apply(base, entry)
	if err != nil {
		return nil, fmt.Errorf("version %q: %w", version, err)
	}
	return out, nil
}

// Encode serializes the blob to the stored form: base64(zstd(canonical JSON)). The dict may
// be nil, in which case plain zstd is used; the reader must use the same choice, which is
// why the document row carries dict_id alongside the blob.
func (b *Blob) Encode(dict []byte, level int) (string, error) {
	if b.Schema == "" {
		b.Schema = SchemaVersion
	}
	return EncodeJSON(b, dict, level)
}

// DecodeBlob is the inverse of Encode.
func DecodeBlob(encoded string, dict []byte) (*Blob, error) {
	comp, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return nil, fmt.Errorf("blob is not valid base64: %w", err)
	}
	raw, err := Decompress(comp, dict)
	if err != nil {
		return nil, err
	}
	var b Blob
	if err := canon.Unmarshal(raw, &b); err != nil {
		return nil, fmt.Errorf("unmarshal blob: %w", err)
	}
	if b.Schema != SchemaVersion {
		return nil, fmt.Errorf("unsupported blob schema %q, want %q", b.Schema, SchemaVersion)
	}
	if len(b.Versions) == 0 {
		return nil, fmt.Errorf("blob carries no versions")
	}
	for _, v := range b.VersionKeys() {
		if _, err := b.Resolve(v); err != nil {
			return nil, err
		}
	}
	return &b, nil
}

// Encoders and decoders are cached per (level, dictionary): building one digests the whole
// dictionary (a 128 KiB dictionary took ~25 ms per document when the loader rebuilt the
// encoder for every frame, turning an hour's load into a day). EncodeAll and DecodeAll are
// both documented safe for concurrent use, so one instance per key serves every goroutine.
var (
	encoders sync.Map // map[encoderKey]*zstd.Encoder
	decoders sync.Map // map[uint32]*zstd.Decoder, key 0 = no dictionary
)

type encoderKey struct {
	level  int
	dictID uint32
}

// Compress zstd-compresses raw, optionally against a trained dictionary. Frames carry no CRC
// (WithEncoderCRC(false)): the bytes are already checksummed twice - TLS in transit and
// Cosmos's storage - so the 4 bytes per frame are pure payload overhead, and the decoder
// (klauspost, OTP's :zstd) does not need them. Magic bytes stay on: readers detect the
// frame by them.
func Compress(raw, dict []byte, level int) ([]byte, error) {
	if level == 0 {
		if len(dict) > 0 {
			level = DefaultZstdLevelWithDict
		} else {
			level = DefaultZstdLevel
		}
	}
	opts := []zstd.EOption{zstd.WithEncoderLevel(zstd.EncoderLevelFromZstd(level)), zstd.WithEncoderCRC(false)}
	var dictID uint32
	if len(dict) > 0 {
		opts = append(opts, zstd.WithEncoderDict(dict))
		dictID = DictID(dict)
	}
	key := encoderKey{level: level, dictID: dictID}
	// A raw-content dictionary (no magic header) has id 0, so two different ones would collide
	// in the cache: only dictionaries that carry their id are cached.
	cached := dictID != 0 || len(dict) == 0
	if cached {
		if enc, ok := encoders.Load(key); ok {
			return enc.(*zstd.Encoder).EncodeAll(raw, nil), nil
		}
	}
	enc, err := zstd.NewWriter(nil, opts...)
	if err != nil {
		return nil, fmt.Errorf("zstd encoder: %w", err)
	}
	if !cached {
		defer func() { _ = enc.Close() }()
		return enc.EncodeAll(raw, nil), nil
	}
	actual, _ := encoders.LoadOrStore(key, enc)
	return actual.(*zstd.Encoder).EncodeAll(raw, nil), nil
}

// Decompress is the inverse of Compress. dict must match what was used to compress; a
// dict-compressed frame decoded without it fails loudly rather than producing garbage.
func Decompress(comp, dict []byte) ([]byte, error) {
	var dictID uint32
	if len(dict) > 0 {
		dictID = DictID(dict)
	}
	cached := dictID != 0 || len(dict) == 0
	if cached {
		if dec, ok := decoders.Load(dictID); ok {
			return decodeAll(dec.(*zstd.Decoder), comp)
		}
	}
	var opts []zstd.DOption
	if len(dict) > 0 {
		opts = append(opts, zstd.WithDecoderDicts(dict))
	}
	dec, err := zstd.NewReader(nil, opts...)
	if err != nil {
		return nil, fmt.Errorf("zstd decoder: %w", err)
	}
	if !cached {
		defer dec.Close()
		return decodeAll(dec, comp)
	}
	actual, _ := decoders.LoadOrStore(dictID, dec)
	return decodeAll(actual.(*zstd.Decoder), comp)
}

func decodeAll(dec *zstd.Decoder, comp []byte) ([]byte, error) {
	out, err := dec.DecodeAll(comp, nil)
	if err != nil {
		return nil, fmt.Errorf("zstd decode: %w", err)
	}
	return out, nil
}

// PoolSchema identifies a random pool document, which holds reservoir-sampled edge ids for one
// release of one knowledge graph rather than a version map. Indexing is off on this container, so
// a random pick would otherwise be a full scan. One document per (kg, version), at
// cosmos.PoolDocID; the ids are plain UUID strings, which measured smaller under zstd than any
// packed encoding (see plans/kg-quickbar-scoped-random.md).
const PoolSchema = "edgelinkouts.pool/1"

// EncodeJSON is the generic form of Blob.Encode: base64(zstd(canonical JSON of v)). The pool
// document and any future reserved documents use it so every stored frame is built the same
// way and reads back with the same dictionary rules.
func EncodeJSON(v any, dict []byte, level int) (string, error) {
	raw, err := Marshal(v)
	if err != nil {
		return "", err
	}
	comp, err := Compress(raw, dict, level)
	if err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(comp), nil
}

// DecodeJSON is the inverse of EncodeJSON.
func DecodeJSON(encoded string, dict []byte, v any) error {
	comp, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return fmt.Errorf("payload is not valid base64: %w", err)
	}
	raw, err := Decompress(comp, dict)
	if err != nil {
		return err
	}
	if err := canon.Unmarshal(raw, v); err != nil {
		return fmt.Errorf("unmarshal payload: %w", err)
	}
	return nil
}

// Pool is the reserved document that backs /random.
type Pool struct {
	Schema    string   `json:"schema"`
	Key       string   `json:"key"`
	SampledAt string   `json:"sampled_at"`
	IDs       []string `json:"ids"`
}

// MarshalJSON encodes the envelope as a sorted map rather than relying on struct field order.
//
// sonic's SortMapKeys sorts map keys but emits struct fields in declaration order, so a struct
// envelope would silently break the canonical form the Elixir reader reproduces byte for byte.
// Routing every envelope through a Doc makes "sorted keys at every depth" true by construction
// instead of by coincidence of field ordering.
func (b Blob) MarshalJSON() ([]byte, error) {
	versions := make(Doc, len(b.Versions))
	for key, entry := range b.Versions {
		versions[key] = entry
	}
	return canon.Marshal(Doc{"schema": b.Schema, "versions": versions})
}

// MarshalJSON encodes the pool envelope as a sorted map, for the same reason as Blob.
func (p Pool) MarshalJSON() ([]byte, error) {
	return canon.Marshal(Doc{
		"schema":     p.Schema,
		"key":        p.Key,
		"sampled_at": p.SampledAt,
		"ids":        p.IDs,
	})
}

// PoolIndexSchema identifies the reserved __random_pool__ document, which since per-release
// pools holds no ids at all: it says which graphs and which releases have a pool, and how big
// each one is.
const PoolIndexSchema = "edgelinkouts.pool_index/1"

// PoolIndex is the reserved index document every random route reads first.
//
// It is deliberately counts-only. The ids live one document per release (PoolDocID), so a random
// edge in one release reads that release's sample and nothing else; Cosmos charges a point read
// by item size, so an index that carried ids would make the cheapest random as expensive as the
// priciest one and would grow with every release. Counts are what a weighted pick needs to stay
// uniform across releases of different sizes, and they cost bytes, not kilobytes.
type PoolIndex struct {
	Schema string `json:"schema"`
	// KGs maps a KG slug ("drugapprovals-kp") to that graph's releases.
	KGs map[string]KGPool `json:"kgs"`
}

// KGPool is one knowledge graph's releases.
type KGPool struct {
	// Versions maps a version label ("1.16.0") to what the load measured for it.
	Versions map[string]VersionPool `json:"versions"`
}

// VersionPool is one release's entry in the index.
type VersionPool struct {
	// Edges is how many distinct edges that release offered the sampler — its true size, which
	// is what a weighted random pick uses so a 130k-edge release is not treated as equal to a
	// 6-edge one.
	Edges int64 `json:"edges"`
	// Sampled is how many ids its pool document actually holds (min(Edges, --sample-size)).
	Sampled int `json:"sampled"`
	// SampledAt is when that pool was last written, RFC3339 UTC.
	SampledAt string `json:"sampled_at"`
}

// MarshalJSON encodes the index as sorted maps at every depth, for the same reason as Blob:
// sonic sorts map keys but emits struct fields in declaration order, and the Elixir reader
// reproduces these bytes exactly.
func (ix PoolIndex) MarshalJSON() ([]byte, error) {
	kgs := make(Doc, len(ix.KGs))
	for slug, kg := range ix.KGs {
		kgs[slug] = kg
	}
	return canon.Marshal(Doc{"schema": ix.Schema, "kgs": kgs})
}

// MarshalJSON encodes one graph's releases as a sorted map.
func (kg KGPool) MarshalJSON() ([]byte, error) {
	versions := make(Doc, len(kg.Versions))
	for label, vp := range kg.Versions {
		versions[label] = vp
	}
	return canon.Marshal(Doc{"versions": versions})
}

// MarshalJSON encodes one release's counts as a sorted map.
func (vp VersionPool) MarshalJSON() ([]byte, error) {
	return canon.Marshal(Doc{
		"edges":      vp.Edges,
		"sampled":    vp.Sampled,
		"sampled_at": vp.SampledAt,
	})
}

// Set records one release, keeping the larger edge count when a key is loaded twice: a partial
// re-run must not shrink a release's weight and so bias random away from it.
func (ix *PoolIndex) Set(slug, label string, edges int64, sampled int, at string) {
	if ix.KGs == nil {
		ix.KGs = map[string]KGPool{}
	}
	kg := ix.KGs[slug]
	if kg.Versions == nil {
		kg.Versions = map[string]VersionPool{}
	}
	entry := VersionPool{Edges: edges, Sampled: sampled, SampledAt: at}
	if prev, ok := kg.Versions[label]; ok && prev.Edges > entry.Edges {
		entry.Edges = prev.Edges
	}
	kg.Versions[label] = entry
	ix.KGs[slug] = kg
}

// Remove drops one release from the index, and the graph with it when that was its last release.
// It reports whether anything was there to drop.
func (ix *PoolIndex) Remove(slug, label string) bool {
	kg, ok := ix.KGs[slug]
	if !ok {
		return false
	}
	if _, ok := kg.Versions[label]; !ok {
		return false
	}
	delete(kg.Versions, label)
	if len(kg.Versions) == 0 {
		delete(ix.KGs, slug)
	} else {
		ix.KGs[slug] = kg
	}
	return true
}

// RemoveVersion drops one version from a blob so a bad release can be purged without losing the
// others. Any version whose delta targeted the removed one is first materialized in full — a
// delta whose base is gone can never resolve — and the blob is verified afterwards, so a failed
// removal leaves the caller with an error rather than a document that will not decode.
//
// It reports whether the version was present.
func (b *Blob) RemoveVersion(version string) (bool, error) {
	if _, ok := b.Versions[version]; !ok {
		return false, nil
	}
	// Materialize the dependents before deleting the base, while the base is still there to
	// resolve against. The keys are collected first because the loop replaces entries.
	dependents := make([]string, 0, len(b.Versions))
	for key, entry := range b.Versions {
		if key != version && !entry.IsFull() && entry.Base == version {
			dependents = append(dependents, key)
		}
	}
	sort.Strings(dependents)
	for _, key := range dependents {
		doc, err := b.Resolve(key)
		if err != nil {
			return false, fmt.Errorf("version %s depends on %s, which cannot be resolved: %w", key, version, err)
		}
		b.Versions[key] = Entry{Full: Clone(doc).(Doc)}
	}
	delete(b.Versions, version)
	for _, key := range b.VersionKeys() {
		if _, err := b.Resolve(key); err != nil {
			return false, fmt.Errorf("removing version %s left %s unresolvable: %w", version, key, err)
		}
	}
	return true, nil
}

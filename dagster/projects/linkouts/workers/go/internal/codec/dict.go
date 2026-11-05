package codec

import (
	"encoding/binary"
	"fmt"
	"hash/fnv"

	"github.com/klauspost/compress/zstd"
)

// dictMagic is the ZSTD dictionary magic number (little-endian on the wire).
const dictMagic = 0xEC30A437

// Dictionary training needs a History buffer to mine matches from and Contents samples to
// compress against it. If History covers the samples, every sample compresses to pure
// matches and the builder aborts with "0 literals found" — measured this session: 1 KB of
// history against 8 samples trains fine, 4 KB of history against the same 8 samples fails.
// So History is kept to a small fraction of the sample mass and capped outright.
const (
	maxDictHistory    = 128 << 10 // 128 KiB is plenty for JSON key names
	historySamplePart = 8         // History <= totalSampleBytes / 8
	minDictHistory    = 8         // the builder's own floor
)

// DefaultDictID is the historical fixed id from before ids were content-derived. Kept for
// tests and docs that pin the old behavior; new training should pass 0 and let
// DictIDForSamples derive one.
const DefaultDictID = 0x454C4F31 // "ELO1"

// zstd reserves dictionary ids below 32768 for registered dictionaries; user ids live in
// [32768, 2^31). https://github.com/facebook/zstd/blob/dev/doc/zstd_compression_format.md
const (
	minCustomDictID = 32768
	maxCustomDictID = 1<<31 - 1
)

// DictIDForSamples derives a dictionary id deterministically from the training corpus:
// retraining from the same samples yields the same id (so a retrained dictionary is a
// drop-in replacement), and a different corpus - another KG, or this KG after real change -
// yields a different id (so a document can never be decoded against the wrong dictionary
// silently). The hash runs over the sample bytes themselves, not over the trained output,
// because the id has to exist before BuildDict can embed it in the dictionary header.
func DictIDForSamples(samples [][]byte) uint32 {
	h := fnv.New32a()
	for _, s := range samples {
		_, _ = h.Write(s)
	}
	return minCustomDictID + h.Sum32()%(maxCustomDictID-minCustomDictID)
}

// BuildDict trains a zstd dictionary from sample documents. Small JSON documents compress
// badly on their own — most of the payload is key names that repeat across every edge — so
// a dictionary trained on the KG's own shape is what makes a point-read document small
// enough to keep the free-tier RU cost negligible.
//
// samples must be representative documents from the same KG; passing documents from a
// different schema produces a dictionary that still works but barely helps.
func BuildDict(samples [][]byte, id uint32) ([]byte, error) {
	if len(samples) == 0 {
		return nil, fmt.Errorf("cannot train a dictionary from zero samples")
	}
	if id == 0 {
		id = DictIDForSamples(samples)
	}
	// History is what BuildDict sizes the tables against (it rejects < 8 bytes) and
	// Contents are the samples it mines for offsets and repeated substrings; both are
	// required, so History is the concatenation of the samples, capped.
	total := 0
	for _, s := range samples {
		total += len(s)
	}
	want := total / historySamplePart
	if want > maxDictHistory {
		want = maxDictHistory
	}
	if want < minDictHistory {
		want = minDictHistory
	}
	if want > total {
		want = total
	}
	hist := make([]byte, 0, want)
	for _, s := range samples {
		if len(hist) >= want {
			break
		}
		room := want - len(hist)
		if room > len(s) {
			room = len(s)
		}
		hist = append(hist, s[:room]...)
	}
	dict, err := zstd.BuildDict(zstd.BuildDictOptions{
		ID:       id,
		History:  hist,
		Contents: samples,
		// Train at the highest level: it costs once, at load time, and tailors the
		// tables for the level the blobs are actually compressed at.
		Level: zstd.SpeedBestCompression,
	})
	if err != nil {
		return nil, fmt.Errorf("train zstd dictionary from %d samples: %w", len(samples), err)
	}
	return dict, nil
}

// DictID reads the dictionary ID embedded in a trained dictionary, or 0 for a raw-content
// dictionary (which has no header). The web reader uses it to select the right dictionary
// without decoding the blob first.
func DictID(dict []byte) uint32 {
	if len(dict) < 8 || binary.LittleEndian.Uint32(dict[:4]) != dictMagic {
		return 0
	}
	return binary.LittleEndian.Uint32(dict[4:8])
}

// SampleDocs picks up to limit documents, spaced evenly through docs, so the dictionary is
// trained on the whole shape of the KG rather than its first few edges. Spacing matters:
// KGX files are usually grouped by predicate, and the first N edges would teach the
// dictionary one predicate's slots.
func SampleDocs(docs [][]byte, limit int) [][]byte {
	if limit <= 0 || len(docs) <= limit {
		return docs
	}
	out := make([][]byte, 0, limit)
	step := float64(len(docs)) / float64(limit)
	for i := 0; i < limit; i++ {
		out = append(out, docs[int(float64(i)*step)])
	}
	return out
}

// Package version parses and orders the "<kg>-<version>" keys that identify one release of
// one knowledge graph. Both the worker (which version a delta is based on) and the web app
// (which display config to use, which order to list versions in) need the same answers, so
// the rules live in one place and the Elixir side mirrors them under a contract test.
package version

import (
	"fmt"
	"strconv"
	"strings"
)

// InforesPrefix is the registry prefix a Translator KG name carries: the drug approvals graph is
// "infores:drugapprovals-kp". Documents and URLs carry the name without it — the slug — because
// the prefix is eight identical bytes on every document and every reader can put it back. The
// canonical name (with the prefix) stays the one used in version keys and display configs, so
// nothing about key parsing changes.
const InforesPrefix = "infores:"

// Slug is the KG name as it is stored on documents and as it appears in URLs: the canonical name
// with the infores registry prefix dropped. A name that has no prefix is its own slug.
func Slug(kg string) string { return strings.TrimPrefix(kg, InforesPrefix) }

// Key is a parsed "<kg>-<version>" identifier, e.g. infores:drugapprovals-kp-1.11.2.
type Key struct {
	Raw string
	KG  string
	// Parts is the dotted version split into integers where possible. "1.11.2" becomes
	// [1 11 2]; a non-numeric part is kept as 0 with Suffix holding the text.
	Parts []int
	// Suffix carries any non-numeric trailing text ("1.0.0-rc1" -> "-rc1").
	Suffix string
}

// Parse splits a key at the last hyphen that begins a numeric segment. KG names contain
// hyphens ("drugapprovals-kp"), versions start with a digit, so that boundary is
// unambiguous — and splitting on the last hyphen alone would break on "1.0.0-rc1".
func Parse(key string) (Key, error) {
	if key == "" {
		return Key{}, fmt.Errorf("empty version key")
	}
	k := Key{Raw: key}
	cut := -1
	for i := len(key) - 1; i > 0; i-- {
		if key[i-1] == '-' && i < len(key) && key[i] >= '0' && key[i] <= '9' {
			cut = i
			break
		}
	}
	if cut < 0 {
		// No numeric segment: treat the whole thing as a KG name with an unparseable version.
		// Callers that need ordering still get a deterministic answer (compare as text).
		k.KG = key
		k.Suffix = key
		return k, nil
	}
	k.KG = key[:cut-1]
	ver := key[cut:]
	k.Parts, k.Suffix = parseVersion(ver)
	return k, nil
}

// parseVersion splits "1.11.2-rc1" into [1 11 2] and "-rc1".
func parseVersion(v string) ([]int, string) {
	var parts []int
	suffix := ""
	dotted := v
	if i := strings.IndexAny(v, "-+"); i >= 0 {
		dotted, suffix = v[:i], v[i:]
	}
	for _, seg := range strings.Split(dotted, ".") {
		n, err := strconv.Atoi(seg)
		if err != nil {
			// A non-numeric segment ends numeric comparison; keep the text so ordering is
			// still total and deterministic.
			if suffix == "" {
				suffix = seg
			}
			parts = append(parts, 0)
			continue
		}
		parts = append(parts, n)
	}
	return parts, suffix
}

// Slug is the KG part of this key without the infores prefix, which is what gets stored on the
// document and used to build the reserved pool document id.
func (k Key) Slug() string { return Slug(k.KG) }

// Version renders the version part alone ("1.11.2"), which is what the UI shows.
func (k Key) Version() string {
	if len(k.Parts) == 0 {
		return strings.TrimPrefix(k.Raw, k.KG)
	}
	nums := make([]string, len(k.Parts))
	for i, p := range k.Parts {
		nums[i] = strconv.Itoa(p)
	}
	return strings.Join(nums, ".") + k.Suffix
}

// Compare orders two keys: by KG name first, then by numeric version parts, then by suffix
// text. It is a total order, so sorting a key list is deterministic — which matters because
// the worker writes deltas against "the newest other version" and the UI lists versions in this
// order.
func Compare(a, b Key) int {
	if c := strings.Compare(a.KG, b.KG); c != 0 {
		return c
	}
	for i := range max(len(a.Parts), len(b.Parts)) {
		var av, bv int
		if i < len(a.Parts) {
			av = a.Parts[i]
		}
		if i < len(b.Parts) {
			bv = b.Parts[i]
		}
		if av != bv {
			if av < bv {
				return -1
			}
			return 1
		}
	}
	return compareSuffix(a.Suffix, b.Suffix)
}

// compareSuffix orders the text after the numeric parts. An absent suffix means a final
// release, which sorts AFTER any pre-release: 1.0.0-rc1 < 1.0.0. A plain string comparison
// gets this backwards, because "" sorts before "-rc1".
func compareSuffix(a, b string) int {
	switch {
	case a == b:
		return 0
	case a == "":
		return 1 // a is the release, b is a pre-release
	case b == "":
		return -1
	default:
		return strings.Compare(a, b)
	}
}

// CompareKeys parses and compares two raw keys. Unparseable keys fall back to text order, so
// a malformed key can never make a sort panic or loop.
func CompareKeys(a, b string) int {
	pa, erra := Parse(a)
	pb, errb := Parse(b)
	if erra != nil || errb != nil {
		return strings.Compare(a, b)
	}
	return Compare(pa, pb)
}

// CompareLabels orders two bare version labels ("1.16.0"), which is what the pool index stores
// per release. Text order would put 1.9.0 after 1.11.2, so the labels are compared as the version
// part of a key: a throwaway KG name in front of each makes Parse find the numeric segments, and
// Compare then does the numeric-then-suffix ordering both callers expect.
func CompareLabels(a, b string) int {
	return CompareKeys(labelPrefix+a, labelPrefix+b)
}

// labelPrefix is a KG name that Parse cannot mistake for part of a version: it ends in a hyphen
// that is not followed by a digit, so the split still lands on the label's own first segment.
const labelPrefix = "kg-"

// Newest returns the highest-ordering key, or "" when there are none.
func Newest(keys []string) string {
	best := ""
	for _, k := range keys {
		if best == "" || CompareKeys(k, best) > 0 {
			best = k
		}
	}
	return best
}

// Package codec owns the wire format: canonical JSON, the version delta encoding, the
// zstd blob, and the trained compression dictionary. It is the Go half of the contract
// that sites/linkouts/test/contract_test.exs checks against the Elixir half; the two must agree
// byte for byte, so nothing here may depend on Go map iteration order.
package codec

import (
	"fmt"
	"sort"
	"strings"

	"github.com/bytedance/sonic"
)

// canon is the one JSON configuration used for anything that gets hashed, stored, or
// compared. SortMapKeys gives deterministic byte output, UseNumber keeps numeric text
// verbatim (so 15.0 never becomes 15 and a big int never becomes a float), and
// EscapeHTML is off because Elixir's JSON does not escape <, >, & — turning it on would
// make the two languages disagree on bytes.
var canon = sonic.Config{
	SortMapKeys: true,
	UseNumber:   true,
	CopyString:  true,
	EscapeHTML:  false,
}.Froze()

// Doc is a decoded JSON object. Values are string, bool, json.Number, []any, Doc, or nil;
// nil is rejected everywhere by Parse because a stored null is exactly the failure mode
// this pipeline exists to prevent.
type Doc map[string]any

// Marshal encodes v canonically: sorted keys at every depth, no insignificant whitespace.
func Marshal(v any) ([]byte, error) {
	b, err := canon.Marshal(v)
	if err != nil {
		return nil, fmt.Errorf("canonical marshal: %w", err)
	}
	return b, nil
}

// Parse decodes one JSON object and rejects nulls at any depth.
func Parse(b []byte) (Doc, error) {
	var v any
	if err := canon.Unmarshal(b, &v); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	doc, ok := asDocAny(v)
	if !ok {
		return nil, fmt.Errorf("decode: expected a JSON object, got %T", v)
	}
	if err := CheckNoNulls(doc, "$"); err != nil {
		return nil, err
	}
	return doc, nil
}

// ParseLenient decodes a JSON object without the null check. It exists for transport
// envelopes — engine output rows, service responses — where a null means "this field was
// absent" and the caller must translate that into a real error message. Document payloads
// that get stored must go through Parse, which refuses nulls.
func ParseLenient(b []byte) (Doc, error) {
	var v any
	if err := canon.Unmarshal(b, &v); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	doc, ok := asDocAny(v)
	if !ok {
		return nil, fmt.Errorf("decode: expected a JSON object, got %T", v)
	}
	return doc, nil
}

// ParseAny decodes any JSON value (object, array, scalar) and rejects nulls.
func ParseAny(b []byte) (any, error) {
	var v any
	if err := canon.Unmarshal(b, &v); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	if err := CheckNoNulls(v, "$"); err != nil {
		return nil, err
	}
	return v, nil
}

// CheckNoNulls walks v and reports the JSON path of the first null it finds. path is used
// only for the error message.
func CheckNoNulls(v any, path string) error {
	switch t := v.(type) {
	case nil:
		return fmt.Errorf("null at %s: the contract forbids nulls, strip them upstream", path)
	case Doc:
		for _, k := range sortedKeys(t) {
			if err := CheckNoNulls(t[k], path+"."+k); err != nil {
				return err
			}
		}
	case map[string]any:
		for _, k := range sortedKeys(t) {
			if err := CheckNoNulls(t[k], path+"."+k); err != nil {
				return err
			}
		}
	case []any:
		for i, e := range t {
			if err := CheckNoNulls(e, fmt.Sprintf("%s[%d]", path, i)); err != nil {
				return err
			}
		}
	}
	return nil
}

// Nullish reports whether v should be dropped: a null, an empty string or container, or a
// string that only spells out the absence of a value. The join SQL applies the same rule;
// this is the Go-side guarantee so a regression in the SQL cannot ship a null.
func Nullish(v any) bool {
	switch t := v.(type) {
	case nil:
		return true
	case string:
		switch strings.ToLower(strings.TrimSpace(t)) {
		case "", "none", "null", "nan", "n/a", "na", "-", "unknown", "not provided", "not_provided":
			return true
		}
		return false
	case []any:
		if len(t) == 0 {
			return true
		}
		for _, e := range t {
			if !Nullish(e) {
				return false
			}
		}
		return true
	case Doc:
		return len(t) == 0
	case map[string]any:
		return len(t) == 0
	}
	return false
}

// Prune removes nullish values recursively and returns false when v itself is nullish,
// so the caller can drop the containing key. Order is irrelevant: the result is only ever
// marshalled by Marshal, which sorts.
func Prune(v any) (any, bool) {
	switch t := v.(type) {
	case Doc:
		out := make(Doc, len(t))
		for k, e := range t {
			if p, ok := Prune(e); ok {
				out[k] = p
			}
		}
		if len(out) == 0 {
			return nil, false
		}
		return out, true
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, e := range t {
			if p, ok := Prune(e); ok {
				out[k] = p
			}
		}
		if len(out) == 0 {
			return nil, false
		}
		return out, true
	case []any:
		out := make([]any, 0, len(t))
		for _, e := range t {
			if p, ok := Prune(e); ok {
				out = append(out, p)
			}
		}
		if len(out) == 0 {
			return nil, false
		}
		return out, true
	default:
		if Nullish(v) {
			return nil, false
		}
		return v, true
	}
}

// Equal compares two values by their canonical bytes. Deep-equality on []any holding
// json.Number would otherwise need type-aware comparison; bytes are already canonical.
func Equal(a, b any) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	ab, err := Marshal(a)
	if err != nil {
		return false
	}
	bb, err := Marshal(b)
	if err != nil {
		return false
	}
	return string(ab) == string(bb)
}

// Clone deep-copies v so a decoded document can be mutated without disturbing the caller's
// copy — Apply needs this to avoid aliasing base into the result.
func Clone(v any) any {
	switch t := v.(type) {
	case Doc:
		out := make(Doc, len(t))
		for k, e := range t {
			out[k] = Clone(e)
		}
		return out
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, e := range t {
			out[k] = Clone(e)
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, e := range t {
			out[i] = Clone(e)
		}
		return out
	default:
		return v // string, bool, json.Number are immutable
	}
}

// asDocAny accepts both Doc and the plain map a JSON decoder produces. sonic decodes into
// map[string]any, never into a named map type, so every entry point has to accept both.
func asDocAny(v any) (Doc, bool) {
	switch t := v.(type) {
	case Doc:
		return t, true
	case map[string]any:
		return Doc(t), true
	}
	return nil, false
}

func sortedKeys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

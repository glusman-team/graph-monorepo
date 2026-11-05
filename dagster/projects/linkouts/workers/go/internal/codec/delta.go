package codec

import (
	"fmt"
	"sort"
)

// Wire keys for a delta entry. A version payload is either a full document (no "$" keys)
// or a delta naming the version it applies to.
const (
	KeyTarget = "$t"
	KeySet    = "$set"
	KeyAdd    = "$add"
	KeyDel    = "$del"
)

// Entry is one version's payload inside a blob: either the whole document or a delta
// against another version of the same edge.
type Entry struct {
	// Base is the version key this delta applies to. Empty means Full is present.
	Base string
	// Set holds keys whose value changed or that are new in this version.
	Set Doc
	// Add holds list elements appended to a list that exists in Base, keyed by field name.
	// It exists because KGX `publications` lists grow between releases and re-storing a
	// 3000-element list to add one element is the single biggest waste in this workload.
	Add map[string][]any
	// Del holds keys present in Base and absent here.
	Del []string
	// Full is the whole document; set only when Base is empty.
	Full Doc
}

// IsFull reports whether the entry stores a whole document.
func (e Entry) IsFull() bool { return e.Base == "" }

// Wire renders the entry as the value stored under its version key.
func (e Entry) Wire() any {
	if e.IsFull() {
		return e.Full
	}
	w := Doc{KeyTarget: e.Base}
	// A non-nil but empty $set is meaningful: it says "identical to the base version". That
	// is the cheapest possible payload for an unchanged version, so nil (omit the key) and
	// empty (emit {}) have to stay distinguishable on the wire.
	if e.Set != nil {
		w[KeySet] = e.Set
	}
	if e.Add != nil {
		add := make(Doc, len(e.Add))
		for k, v := range e.Add {
			add[k] = v
		}
		w[KeyAdd] = add
	}
	if e.Del != nil {
		del := make([]any, len(e.Del))
		for i, k := range e.Del {
			del[i] = k
		}
		w[KeyDel] = del
	}
	return w
}

// EntryFromWire parses a version payload. A payload with no "$" keys is a full document.
func EntryFromWire(v any) (Entry, error) {
	switch t := v.(type) {
	case Doc:
		return entryFromDoc(t)
	case map[string]any:
		return entryFromDoc(Doc(t))
	default:
		return Entry{}, fmt.Errorf("version payload: expected an object, got %T", v)
	}
}

func entryFromDoc(d Doc) (Entry, error) {
	base, isDelta := d[KeyTarget]
	if !isDelta {
		return Entry{Full: d}, nil
	}
	target, ok := base.(string)
	if !ok || target == "" {
		return Entry{}, fmt.Errorf("%s must be a non-empty version key, got %#v", KeyTarget, base)
	}
	e := Entry{Base: target}
	if raw, ok := d[KeySet]; ok {
		set, err := asDoc(raw, KeySet)
		if err != nil {
			return Entry{}, err
		}
		e.Set = set
	}
	if raw, ok := d[KeyAdd]; ok {
		addDoc, err := asDoc(raw, KeyAdd)
		if err != nil {
			return Entry{}, err
		}
		e.Add = make(map[string][]any, len(addDoc))
		for k, v := range addDoc {
			list, ok := v.([]any)
			if !ok {
				return Entry{}, fmt.Errorf("%s.%s must be a list, got %T", KeyAdd, k, v)
			}
			e.Add[k] = list
		}
	}
	if raw, ok := d[KeyDel]; ok {
		list, ok := raw.([]any)
		if !ok {
			return Entry{}, fmt.Errorf("%s must be a list, got %T", KeyDel, raw)
		}
		e.Del = make([]string, 0, len(list))
		for _, item := range list {
			s, ok := item.(string)
			if !ok {
				return Entry{}, fmt.Errorf("%s entries must be strings, got %T", KeyDel, item)
			}
			e.Del = append(e.Del, s)
		}
	}
	// Reject a delta with no operation keys at all. An empty-but-present $set is legal and
	// means "unchanged from the base"; omitting all three is a corrupt payload.
	_, hasSet := d[KeySet]
	_, hasAdd := d[KeyAdd]
	_, hasDel := d[KeyDel]
	if !hasSet && !hasAdd && !hasDel {
		return Entry{}, fmt.Errorf("delta against %s carries no operations", target)
	}
	return e, nil
}

func asDoc(v any, key string) (Doc, error) {
	switch t := v.(type) {
	case Doc:
		return t, nil
	case map[string]any:
		return Doc(t), nil
	default:
		return nil, fmt.Errorf("%s must be an object, got %T", key, v)
	}
}

// Diff chooses the cheaper of "store next in full" and "delta from base". base may be nil,
// in which case the result is always full. Cheaper is measured in canonical bytes, which is
// what actually gets compressed and stored, not in key counts.
//
// baseVersion is the key the delta names in "$t"; it is required whenever base is non-nil,
// because a delta that cannot name its base cannot be resolved.
func Diff(baseVersion string, base, next Doc) (Entry, error) {
	full := Entry{Full: next}
	if base == nil {
		return full, nil
	}
	if baseVersion == "" {
		return Entry{}, fmt.Errorf("diffing against a base document requires its version key")
	}
	patch, err := diffPatch(baseVersion, base, next)
	if err != nil {
		return Entry{}, err
	}
	if patch.Set == nil && patch.Add == nil && patch.Del == nil {
		// Identical documents: store the cheapest legal payload, an explicit empty $set.
		// A delta that says "same as base" beats re-storing the document.
		return Entry{Base: baseVersion, Set: Doc{}}, nil
	}
	fullBytes, err := Marshal(full.Wire())
	if err != nil {
		return Entry{}, err
	}
	patchBytes, err := Marshal(patch.Wire())
	if err != nil {
		return Entry{}, err
	}
	if len(patchBytes) < len(fullBytes) {
		return patch, nil
	}
	return full, nil
}

func diffPatch(baseVersion string, base, next Doc) (Entry, error) {
	patch := Entry{Base: baseVersion}
	keys := make([]string, 0, len(base)+len(next))
	seen := make(map[string]bool, len(base)+len(next))
	for k := range base {
		if !seen[k] {
			seen[k] = true
			keys = append(keys, k)
		}
	}
	for k := range next {
		if !seen[k] {
			seen[k] = true
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)

	for _, k := range keys {
		old, inBase := base[k]
		nv, inNext := next[k]
		switch {
		case inBase && !inNext:
			patch.Del = append(patch.Del, k)
		case !inBase && inNext:
			if patch.Set == nil {
				patch.Set = Doc{}
			}
			patch.Set[k] = Clone(nv)
		case !Equal(old, nv):
			if appended, ok := listAppend(old, nv); ok {
				if patch.Add == nil {
					patch.Add = map[string][]any{}
				}
				patch.Add[k] = appended
				continue
			}
			if patch.Set == nil {
				patch.Set = Doc{}
			}
			patch.Set[k] = Clone(nv)
		}
	}
	return patch, nil
}

// listAppend reports the elements appended when next is old with extra items on the end.
// Prefix-only growth qualifies; reordering or shrinking does not, because then the whole
// list is the honest payload.
func listAppend(old, next any) ([]any, bool) {
	ol, ok := old.([]any)
	if !ok {
		return nil, false
	}
	nl, ok := next.([]any)
	if !ok || len(nl) <= len(ol) {
		return nil, false
	}
	for i := range ol {
		if !Equal(ol[i], nl[i]) {
			return nil, false
		}
	}
	return Clone(nl[len(ol):]).([]any), true
}

// Apply resolves an entry against the document stored for its base version.
func Apply(base Doc, e Entry) (Doc, error) {
	if e.IsFull() {
		out, ok := Clone(e.Full).(Doc)
		if !ok {
			return nil, fmt.Errorf("full payload did not clone to an object")
		}
		return out, nil
	}
	if base == nil {
		return nil, fmt.Errorf("delta targets version %q but that version was not resolved", e.Base)
	}
	out, ok := Clone(base).(Doc)
	if !ok {
		return nil, fmt.Errorf("base document did not clone to an object")
	}
	for _, k := range e.Del {
		delete(out, k)
	}
	for k, v := range e.Set {
		out[k] = Clone(v)
	}
	for k, extra := range e.Add {
		cur, ok := out[k].([]any)
		if !ok {
			return nil, fmt.Errorf("%s targets %q, which is not a list in version %s", KeyAdd, k, e.Base)
		}
		merged := make([]any, 0, len(cur)+len(extra))
		merged = append(merged, cur...)
		merged = append(merged, Clone(extra).([]any)...)
		out[k] = merged
	}
	if err := CheckNoNulls(out, "$"); err != nil {
		return nil, fmt.Errorf("applying delta against %s: %w", e.Base, err)
	}
	return out, nil
}

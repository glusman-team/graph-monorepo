package pipeline

import (
	"context"
	"errors"
	"fmt"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/version"
)

// Purge removes stored data: either the whole store, or one release of one knowledge graph.
//
// The two modes cost very differently, and that is a property of the container rather than of
// this code. Indexing is off — which is what keeps every read at 1 RU per KB and every write at
// its minimum — so there is no way to ask Cosmos "which documents hold version X". A whole-store
// wipe therefore drops the container, which is immediate and free, while removing one release has
// to read every document to find the ones that mention it, and the backend answers
// ErrScanUnsupported when the service will not serve that scan. Both modes are here because both
// are needed: the wipe for a schema change or a bad load, the targeted removal for one bad
// release among good ones.

// PurgeOptions configures a purge.
type PurgeOptions struct {
	Store     cosmos.Store
	Dict      []byte
	ZstdLevel int
	// Budget accounts the RU the purge spends, the same way a load does. Nil means unlimited.
	Budget ratelimit.Budget

	// All wipes every document, including the reserved pool and index documents.
	All bool
	// Slug is the knowledge graph to purge, in slug form ("drugapprovals-kp"). Required unless
	// All is set.
	Slug string
	// VersionLabel narrows the purge to one release ("1.16.0"). Empty means every release of
	// Slug that the index lists.
	VersionLabel string

	DryRun bool

	// OnScan, when set, is called every scanReportInterval documents with the running counts.
	// A scan has no total to compute a percentage from, so this reports raw progress.
	OnScan func(scanned, matched int64)
}

// scanReportInterval is how often a long scan says something. Every document would drown the
// terminal; never would leave a multi-minute operation looking hung. A var rather than a const so
// a test can exercise the reporting path without storing five thousand documents.
var scanReportInterval int64 = 5000

// PurgeStats reports what a purge did.
type PurgeStats struct {
	// Scanned is how many documents the scan read. Zero for a whole-store wipe, which does not
	// scan: dropping a container does not need to look at what is in it.
	Scanned int64
	// Deleted counts documents removed entirely, because the purged release was their last.
	Deleted int64
	// Rewritten counts documents that kept other releases and were rewritten without this one.
	Rewritten int64
	// Versions counts version entries removed from blobs.
	Versions int64
	// Pools counts pool documents deleted, and IndexEntries the index entries removed.
	Pools        int
	IndexEntries int
	// Dropped is true when the whole store was wiped rather than scanned.
	Dropped bool
	RU      float64
}

// Purge runs one purge. It is safe to re-run: a pool document that is already gone is not an
// error, and a document that no longer holds the release is simply not matched.
func Purge(ctx context.Context, o PurgeOptions) (*PurgeStats, error) {
	if o.Store == nil {
		return nil, errors.New("purge needs a store")
	}
	if o.ZstdLevel == 0 {
		o.ZstdLevel = codec.DefaultZstdLevel
	}
	if o.Budget == nil {
		o.Budget = ratelimit.NewUnlimited()
	}
	if !o.All && o.Slug == "" {
		return nil, errors.New("purge needs --all, or a knowledge graph to purge")
	}
	if o.All && (o.Slug != "" || o.VersionLabel != "") {
		return nil, errors.New("--all cannot be combined with a KG or version: it wipes everything")
	}

	st := &PurgeStats{}
	if o.All {
		if !o.DryRun {
			if err := o.Store.DropAll(ctx); err != nil {
				return nil, fmt.Errorf("wipe store: %w", err)
			}
		}
		st.Dropped = true
		st.RU = o.Budget.Consumed()
		return st, nil
	}

	index, err := ReadPoolIndex(ctx, o.Store, o.Dict)
	switch {
	case err == nil:
	case errors.Is(err, cosmos.ErrNotFound):
		// No index: nothing was ever loaded, or a pre-per-release pool document still sits at
		// that id. A named release can still be purged out of the blobs; a whole-graph purge
		// has nothing to enumerate and says so below.
		index = codec.PoolIndex{KGs: map[string]codec.KGPool{}}
	default:
		return nil, err
	}

	releases := o.targetReleases(index)
	if len(releases) == 0 {
		if o.VersionLabel != "" {
			// Not indexed, but the blobs may still hold it (a load that died before writing the
			// index, or an index that was wiped). The scan settles it.
			releases = []string{o.VersionLabel}
		} else {
			return nil, fmt.Errorf("the pool index lists no releases of %q; nothing to purge "+
				"(use --all to wipe the whole store)", o.Slug)
		}
	}

	if err := o.removePools(ctx, st, index, releases); err != nil {
		return nil, err
	}
	if err := o.scanAndPrune(ctx, st); err != nil {
		return nil, err
	}
	st.RU = o.Budget.Consumed()
	return st, nil
}

// targetReleases is the list of version labels to remove: the one named, or every release the
// index lists for this graph.
func (o PurgeOptions) targetReleases(index codec.PoolIndex) []string {
	if o.VersionLabel != "" {
		return nil // handled by the caller, which decides whether an unindexed release is fatal
	}
	labels := make([]string, 0, len(index.KGs[o.Slug].Versions))
	for _, rel := range Releases(index, o.Slug) {
		labels = append(labels, rel.Label)
	}
	return labels
}

// removePools deletes each target release's pool document and its index entry, then writes the
// index back. The pool goes first so a reader racing the purge finds no ids pointing at documents
// that are about to change, rather than the other way round.
func (o PurgeOptions) removePools(ctx context.Context, st *PurgeStats, index codec.PoolIndex, releases []string) error {
	indexChanged := false
	for _, label := range releases {
		id := cosmos.PoolDocID(o.Slug, label)
		if o.DryRun {
			if _, err := o.Store.Read(ctx, id); err == nil {
				st.Pools++
			}
			if _, ok := index.KGs[o.Slug].Versions[label]; ok {
				st.IndexEntries++
			}
			continue
		}
		err := o.Store.Delete(ctx, id)
		switch {
		case err == nil:
			st.Pools++
		case errors.Is(err, cosmos.ErrNotFound):
			// Already gone: a re-run of an interrupted purge lands here.
		default:
			return fmt.Errorf("delete %s: %w", id, err)
		}
		if index.Remove(o.Slug, label) {
			st.IndexEntries++
			indexChanged = true
		}
	}
	if o.DryRun || !indexChanged {
		return nil
	}
	encoded, err := codec.EncodeJSON(index, o.Dict, o.ZstdLevel)
	if err != nil {
		return fmt.Errorf("encode pool index: %w", err)
	}
	doc := cosmos.Doc{ID: cosmos.RandomPoolID, Blob: encoded, DictID: o.dictID()}
	if err := o.Store.Upsert(ctx, doc); err != nil {
		return fmt.Errorf("store pool index: %w", err)
	}
	return nil
}

// dictID mirrors the load path's rule: a dictionary is only claimed when one was actually used.
func (o PurgeOptions) dictID() uint32 {
	if len(o.Dict) == 0 {
		return 0
	}
	return codec.DefaultDictID
}

// scanAndPrune reads every document and removes the target releases from the blobs that hold
// them. A blob left with no versions is deleted; one that still holds other releases is rewritten
// under its etag, so a concurrent load either landed before the read or fails the precondition
// rather than being silently overwritten.
func (o PurgeOptions) scanAndPrune(ctx context.Context, st *PurgeStats) error {
	var matched int64
	err := o.Store.All(ctx, func(d cosmos.Doc) error {
		if cosmos.IsReservedID(d.ID) {
			return nil
		}
		st.Scanned++
		if o.OnScan != nil && st.Scanned%scanReportInterval == 0 {
			o.OnScan(st.Scanned, matched)
		}
		dict, err := DictionaryFor(d.DictID, o.Dict)
		if err != nil {
			return fmt.Errorf("%s: %w", d.ID, err)
		}
		blob, err := codec.DecodeBlob(d.Blob, dict)
		if err != nil {
			return fmt.Errorf("%s: %w", d.ID, err)
		}
		targets := o.matchingVersions(blob)
		if len(targets) == 0 {
			return nil
		}
		matched++
		if o.DryRun {
			st.Versions += int64(len(targets))
			return nil
		}
		for _, key := range targets {
			removed, err := blob.RemoveVersion(key)
			if err != nil {
				return fmt.Errorf("%s: %w", d.ID, err)
			}
			if removed {
				st.Versions++
			}
		}
		if len(blob.Versions) == 0 {
			if err := o.Store.Delete(ctx, d.ID); err != nil {
				return fmt.Errorf("delete %s: %w", d.ID, err)
			}
			st.Deleted++
			return nil
		}
		encoded, err := blob.Encode(dict, o.ZstdLevel)
		if err != nil {
			return fmt.Errorf("%s: re-encode: %w", d.ID, err)
		}
		out := cosmos.Doc{ID: d.ID, Blob: encoded, DictID: d.DictID, KG: d.KG}
		if err := o.Store.Replace(ctx, out, d.ETag); err != nil {
			return fmt.Errorf("%s: rewrite: %w", d.ID, err)
		}
		st.Rewritten++
		return nil
	})
	if err != nil {
		return err
	}
	// A final progress line only earns its place on a scan long enough to have printed others;
	// on a small store it would just repeat the summary the caller prints next.
	if o.OnScan != nil && st.Scanned >= scanReportInterval {
		o.OnScan(st.Scanned, matched)
	}
	return nil
}

// matchingVersions returns the stored version keys in scope, oldest first so a dry run reports
// them deterministically.
func (o PurgeOptions) matchingVersions(blob *codec.Blob) []string {
	var out []string
	for _, key := range blob.VersionKeys() {
		if o.matches(key) {
			out = append(out, key)
		}
	}
	return out
}

// matches reports whether a stored version key is in scope. The comparison is on the parsed key
// rather than on a string prefix, so a graph named "drug" never matches "drug-approvals".
func (o PurgeOptions) matches(key string) bool {
	parsed, err := version.Parse(key)
	if err != nil {
		return false
	}
	if version.Slug(parsed.KG) != o.Slug {
		return false
	}
	if o.VersionLabel == "" {
		return true
	}
	return parsed.Version() == o.VersionLabel
}

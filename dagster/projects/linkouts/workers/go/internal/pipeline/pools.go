package pipeline

import (
	"context"
	"errors"
	"fmt"
	"sort"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/version"
)

// This file is the read side of the random pools: the index document that says which releases
// have a pool, and the per-release pool documents that hold the sampled ids. The load path writes
// them (load.go); the worker's probe, status, and purge operations read them, and the web
// app reads the same two shapes from Elixir under a contract test.

// SchemaError reports a reserved document whose schema this worker cannot interpret: something an
// older format stored at an id this one now uses for another purpose. It is a distinct type so a
// command can tell an operator to reload and migrate, instead of printing a decode error that
// reads like corruption.
type SchemaError struct {
	ID   string // the reserved document id
	Got  string // the schema the document declares
	Want string // the schema this worker expected
}

func (e *SchemaError) Error() string {
	return fmt.Sprintf("%s holds schema %q, want %q: reload with this worker to rewrite it",
		e.ID, e.Got, e.Want)
}

// DictionaryFor returns the dictionary a stored frame needs. A mismatch is fatal rather than
// silently producing garbage: zstd cannot decode a dict-compressed frame without its dict.
func DictionaryFor(storedID uint32, current []byte) ([]byte, error) {
	currentID := codec.DictID(current)
	if storedID == currentID {
		return current, nil
	}
	return nil, fmt.Errorf("document was compressed with dictionary %#x but this run has %#x; "+
		"pass the matching --dict or repack with --no-repack=false", storedID, currentID)
}

// ReadPoolIndex reads the reserved index document. A missing index is cosmos.ErrNotFound, which
// callers turn into "nothing has been loaded yet" rather than an error.
func ReadPoolIndex(ctx context.Context, store cosmos.Store, dict []byte) (codec.PoolIndex, error) {
	var index codec.PoolIndex
	if err := readReserved(ctx, store, dict, cosmos.RandomPoolID, &index); err != nil {
		return index, err
	}
	if index.Schema != codec.PoolIndexSchema {
		// The pre-per-release format held a flat id list at this id. It carries no counts, so
		// there is nothing to salvage: the load that writes an index is the migration.
		return codec.PoolIndex{}, &SchemaError{ID: cosmos.RandomPoolID, Got: index.Schema, Want: codec.PoolIndexSchema}
	}
	if index.KGs == nil {
		index.KGs = map[string]codec.KGPool{}
	}
	return index, nil
}

// ReadPool reads one release's sampled ids.
func ReadPool(ctx context.Context, store cosmos.Store, dict []byte, slug, label string) (codec.Pool, error) {
	var pool codec.Pool
	id := cosmos.PoolDocID(slug, label)
	if err := readReserved(ctx, store, dict, id, &pool); err != nil {
		return pool, err
	}
	if pool.Schema != codec.PoolSchema {
		return codec.Pool{}, &SchemaError{ID: id, Got: pool.Schema, Want: codec.PoolSchema}
	}
	return pool, nil
}

// readReserved point-reads one reserved document and decodes it with the dictionary it was
// written with.
func readReserved(ctx context.Context, store cosmos.Store, dict []byte, id string, into any) error {
	doc, err := store.Read(ctx, id)
	if err != nil {
		if errors.Is(err, cosmos.ErrNotFound) {
			return fmt.Errorf("%s: %w", id, err)
		}
		return fmt.Errorf("read %s: %w", id, err)
	}
	stored, err := DictionaryFor(doc.DictID, dict)
	if err != nil {
		return fmt.Errorf("%s: %w", id, err)
	}
	if err := codec.DecodeJSON(doc.Blob, stored, into); err != nil {
		return fmt.Errorf("decode %s: %w", id, err)
	}
	return nil
}

// Releases is one graph's releases in ascending version order, so status and probe report them
// oldest-first the way the web app lists them.
type Release struct {
	Label string
	Pool  codec.VersionPool
}

// Releases lists a graph's releases from the index, oldest first.
func Releases(index codec.PoolIndex, slug string) []Release {
	kg, ok := index.KGs[slug]
	if !ok {
		return nil
	}
	labels := make([]string, 0, len(kg.Versions))
	for label := range kg.Versions {
		labels = append(labels, label)
	}
	sort.Slice(labels, func(i, j int) bool {
		return versionLess(labels[i], labels[j])
	})
	out := make([]Release, 0, len(labels))
	for _, label := range labels {
		out = append(out, Release{Label: label, Pool: kg.Versions[label]})
	}
	return out
}

// Slugs lists the indexed graphs in name order.
func Slugs(index codec.PoolIndex) []string {
	out := make([]string, 0, len(index.KGs))
	for slug := range index.KGs {
		out = append(out, slug)
	}
	sort.Strings(out)
	return out
}

// versionLess orders version labels ("1.16.0") using the same rules as the keys they came from,
// so 1.9.0 sorts before 1.11.2 instead of after it.
func versionLess(a, b string) bool { return version.CompareLabels(a, b) < 0 }

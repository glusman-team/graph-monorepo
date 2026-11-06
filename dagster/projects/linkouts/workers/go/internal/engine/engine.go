// Package engine resolves KGX edges against their subject and object nodes.
//
// The production implementation is an embedded ClickHouse (chdb) session: the nodes file is
// the lookup side of a hash join and the edges stream through it, so a 134 MB dump never has
// to fit in memory. The Fake implementation does the same join in pure Go so the pipeline can
// be tested without extracting a 540 MB engine.
package engine

import (
	"context"
	"errors"
	"fmt"
	"runtime"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
)

// ErrStop ends a join cleanly. Returning it from an emit callback is how a caller that only
// needs part of the stream says so; it is not an error and must not be reported as one.
var ErrStop = errors.New("stop iteration")

// Row is one joined edge: the edge document plus the node documents it points at. A node
// that does not exist arrives as an empty Doc, never as nil and never as a JSON null — the
// caller omits the field, which is how the "no nulls" contract is upheld (ADR 0001).
type Row struct {
	ID          string
	Edge        codec.Doc
	SubjectNode codec.Doc
	ObjectNode  codec.Doc
}

// Query names the two KGX inputs. Paths must be absolute: chdb's file() resolves relative
// paths against the process working directory, which would make results depend on where the
// worker was invoked from.
type Query struct {
	NodesPath string
	EdgesPath string
	// Threads caps ClickHouse parallelism. Zero means runtime.NumCPU().
	Threads int
}

func (q Query) validate() error {
	if q.NodesPath == "" || q.EdgesPath == "" {
		return fmt.Errorf("engine query needs both a nodes and an edges path")
	}
	return nil
}

func (q Query) threads() int {
	if q.Threads > 0 {
		return q.Threads
	}
	if n := runtime.NumCPU(); n > 0 {
		return n
	}
	return 1
}

// Engine joins KGX files. Implementations must stream: emit is called once per edge, in file
// order, and returning an error from it aborts the join.
type Engine interface {
	Join(ctx context.Context, q Query, emit func(Row) error) error
	Close() error
	// Name identifies the backend in logs and in the worker's probe report.
	Name() string
}

// Open returns the embedded ClickHouse engine, or the pure-Go Fake when backend is "fake".
// cacheDir is where chdb extracts libchdb.so; it must be private to the user and on
// persistent storage, because the extraction is ~540 MiB.
func Open(backend, cacheDir string, threads int) (Engine, error) {
	switch backend {
	case "", "chdb", "clickhouse":
		return NewChdb(cacheDir, threads)
	case "fake":
		return NewFake(), nil
	default:
		return nil, fmt.Errorf("unknown engine backend %q (want chdb or fake)", backend)
	}
}

// MergedDoc builds the document that gets stored for one joined row: the edge with
// subject_name / subject_category / object_name / object_category attached from the node
// side, then every nullish value stripped.
//
// Names are attached only when the node exists. KGX edges carry category on nodes, not on
// edges, and the display configs need both the human-readable name and the biolink category
// to pick a template — which is why the join exists at all.
func MergedDoc(r Row) (codec.Doc, error) {
	doc := codec.Clone(r.Edge).(codec.Doc)
	attach(doc, "subject", r.SubjectNode)
	attach(doc, "object", r.ObjectNode)

	pruned, keep := codec.Prune(doc)
	if !keep {
		return nil, fmt.Errorf("edge %s: every value was nullish, nothing to store", r.ID)
	}
	out, ok := pruned.(codec.Doc)
	if !ok {
		return nil, fmt.Errorf("edge %s: pruned document is not an object", r.ID)
	}
	// The id is the document identity and the partition key. Prune can only have dropped it
	// if the source row lacked one, which would silently corrupt the store.
	if _, ok := out["id"].(string); !ok {
		return nil, fmt.Errorf("edge %s: KGX record has no usable string id", r.ID)
	}
	if err := codec.CheckNoNulls(out, "$"); err != nil {
		return nil, fmt.Errorf("edge %s: %w", r.ID, err)
	}
	return out, nil
}

// attach copies name and category from a node document onto the edge under <side>_name and
// <side>_category. An empty node (no match) attaches nothing.
func attach(doc codec.Doc, side string, node codec.Doc) {
	if len(node) == 0 {
		return
	}
	if name, ok := node["name"].(string); ok && name != "" {
		doc[side+"_name"] = name
	}
	if cat, ok := node["category"]; ok && !codec.Nullish(cat) {
		doc[side+"_category"] = codec.Clone(cat)
	}
}

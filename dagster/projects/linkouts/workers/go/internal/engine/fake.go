package engine

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
)

// errStop is the internal signal that ends eachNDJSON without being reported as a failure.
var errStop = errors.New("stop")

// Fake joins in pure Go. It produces exactly the Rows the chdb backend produces — including
// an empty node document for an unmatched side — so the pipeline and every test above it can
// run without extracting a 540 MiB engine. It indexes the whole nodes file in memory, which
// is fine for fixtures and unacceptable for a real dump; that is the point of having both.
type Fake struct{}

var _ Engine = (*Fake)(nil)

// NewFake returns the pure-Go backend.
func NewFake() *Fake { return &Fake{} }

// Name identifies the backend in logs.
func (f *Fake) Name() string { return "fake (pure-Go join, fixtures only)" }

// Close is a no-op; there is no engine to release.
func (f *Fake) Close() error { return nil }

// Join indexes nodes by id, then streams edges and emits one Row each.
func (f *Fake) Join(ctx context.Context, q Query, emit func(Row) error) error {
	if err := q.validate(); err != nil {
		return err
	}
	nodes, err := indexNodes(q.NodesPath)
	if err != nil {
		return err
	}
	count := 0
	err = eachNDJSON(q.EdgesPath, func(line int, raw []byte) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		edge, err := codec.ParseLenient(raw)
		if err != nil {
			return fmt.Errorf("%s:%d: %w", q.EdgesPath, line, err)
		}
		id, _ := edge["id"].(string)
		if id == "" {
			return fmt.Errorf("%s:%d: edge has no id (KGX records must carry one)", q.EdgesPath, line)
		}
		count++
		emitErr := emit(Row{
			ID:          id,
			Edge:        edge,
			SubjectNode: lookupNode(nodes, edge["subject"]),
			ObjectNode:  lookupNode(nodes, edge["object"]),
		})
		if errors.Is(emitErr, ErrStop) {
			return errStop
		}
		return emitErr
	})
	if err != nil && !errors.Is(err, errStop) {
		return err
	}
	if count == 0 {
		return fmt.Errorf("%s contained no edge records", q.EdgesPath)
	}
	return nil
}

// indexNodes loads every node keyed by id. A node file with duplicate ids keeps the last,
// matching what a hash join on the right side would resolve to.
func indexNodes(path string) (map[string]codec.Doc, error) {
	nodes := map[string]codec.Doc{}
	err := eachNDJSON(path, func(line int, raw []byte) error {
		node, err := codec.ParseLenient(raw)
		if err != nil {
			return fmt.Errorf("%s:%d: %w", path, line, err)
		}
		id, _ := node["id"].(string)
		if id == "" {
			return fmt.Errorf("%s:%d: node has no id", path, line)
		}
		nodes[id] = node
		return nil
	})
	if err != nil {
		return nil, err
	}
	if len(nodes) == 0 {
		return nil, fmt.Errorf("%s contained no node records", path)
	}
	return nodes, nil
}

// lookupNode returns the node for a KGX cursor, or an empty Doc when the cursor is missing,
// not a string, or unknown. An empty Doc is what the chdb backend yields for an unmatched
// LEFT JOIN, so both backends agree and MergedDoc omits the name either way.
func lookupNode(nodes map[string]codec.Doc, cursor any) codec.Doc {
	id, ok := cursor.(string)
	if !ok || id == "" {
		return codec.Doc{}
	}
	node, ok := nodes[id]
	if !ok {
		return codec.Doc{}
	}
	return node
}

// eachNDJSON streams a file line by line. A 134 MB dump must not be slurped, and the buffer
// is raised well above bufio's default because KGX edges carry publications lists that run to
// tens of thousands of PMIDs on one line.
func eachNDJSON(path string, fn func(line int, raw []byte) error) error {
	fh, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("open %s: %w", path, err)
	}
	defer func() { _ = fh.Close() }()

	sc := bufio.NewScanner(fh)
	sc.Buffer(make([]byte, 0, 64*1024), 64*1024*1024)
	line := 0
	for sc.Scan() {
		raw := sc.Bytes()
		if len(raw) == 0 {
			continue
		}
		line++
		if err := fn(line, raw); err != nil {
			return err
		}
	}
	if err := sc.Err(); err != nil {
		if errors.Is(err, io.EOF) {
			return nil
		}
		return fmt.Errorf("read %s: %w", path, err)
	}
	return nil
}

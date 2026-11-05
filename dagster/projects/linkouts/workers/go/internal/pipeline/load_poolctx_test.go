package pipeline

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/engine"
)

// ctxStore fails every call whose context is done. The file and mem stores ignore their
// context, so only a store like this one catches the bug where writePool ran after
// errgroup.Wait had already canceled the errgroup's derived context: a fully successful load
// against cosmos exited 130 with "store random pool: context canceled".
type ctxStore struct {
	cosmos.Store
	canceled int
}

func (s *ctxStore) guard(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		s.canceled++
		return err
	}
	return nil
}

func (s *ctxStore) Read(ctx context.Context, id string) (cosmos.Doc, error) {
	if err := s.guard(ctx); err != nil {
		return cosmos.Doc{}, err
	}
	return cosmos.Doc{}, cosmos.ErrNotFound
}

func (s *ctxStore) Create(_ context.Context, _ cosmos.Doc) error { return nil }
func (s *ctxStore) Upsert(ctx context.Context, _ cosmos.Doc) error {
	return s.guard(ctx)
}

func TestPoolWriteUsesAContextThatSurvivesWait(t *testing.T) {
	dir := t.TempDir()
	nodes, edges := writeFixtures(t, dir)

	st := &ctxStore{}
	opts := Options{
		Key:        "kg-1.0.0",
		NodesPath:  nodes,
		EdgesPath:  edges,
		Engine:     engine.NewFake(),
		Store:      st,
		SampleSize: 10,
	}
	if _, err := Load(context.Background(), opts); err != nil {
		t.Fatalf("load: %v", err)
	}
	if st.canceled > 0 {
		t.Fatalf("%d store calls saw a canceled context; post-Wait work must use the parent ctx", st.canceled)
	}
}

func writeFixtures(t *testing.T, dir string) (nodes, edges string) {
	t.Helper()
	nodes = filepath.Join(dir, "nodes.ndjson")
	edges = filepath.Join(dir, "edges.ndjson")
	nl := "{\"id\":\"n1\",\"name\":\"Aspirin\",\"categories\":[\"biolink:Drug\"]}\n" +
		"{\"id\":\"n2\",\"name\":\"Headache\",\"categories\":[\"biolink:Disease\"]}\n"
	el := "{\"id\":\"e1\",\"subject\":\"n1\",\"object\":\"n2\",\"predicate\":\"biolink:treats\"}\n"
	if err := os.WriteFile(nodes, []byte(nl), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(edges, []byte(el), 0o644); err != nil {
		t.Fatal(err)
	}
	return nodes, edges
}

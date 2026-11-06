package cosmos

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

// FileStore is an append-only NDJSON backend. It exists so the web app, the contract tests,
// and every pipeline test run against real bytes on disk with no account, no network, and
// no RU spend. Append-only with last-write-wins matches Cosmos semantics closely enough for
// reads: the newest line for an id is the current document.
type FileStore struct {
	path   string
	budget ratelimit.Budget

	mu   sync.RWMutex
	docs map[string]Doc
	fh   *os.File
}

var _ Store = (*FileStore)(nil)

// OpenFile loads an existing NDJSON file if present and opens it for appending.
func OpenFile(path string, budget ratelimit.Budget) (*FileStore, error) {
	if path == "" {
		return nil, fmt.Errorf("file store needs a path")
	}
	if budget == nil {
		budget = ratelimit.NewUnlimited()
	}
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, fmt.Errorf("create %s: %w", dir, err)
		}
	}
	s := &FileStore{path: path, budget: budget, docs: map[string]Doc{}}
	if err := s.load(); err != nil {
		return nil, err
	}
	fh, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", path, err)
	}
	s.fh = fh
	return s, nil
}

func (s *FileStore) load() error {
	fh, err := os.Open(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // first run
		}
		return fmt.Errorf("read %s: %w", s.path, err)
	}
	// Read-only handle: nothing is buffered, so a Close failure cannot lose data.
	defer func() { _ = fh.Close() }()

	sc := bufio.NewScanner(fh)
	sc.Buffer(make([]byte, 0, 64*1024), 32*1024*1024) // blobs can be large
	line := 0
	for sc.Scan() {
		line++
		raw := sc.Bytes()
		if len(raw) == 0 {
			continue
		}
		var d Doc
		if err := json.Unmarshal(raw, &d); err != nil {
			return fmt.Errorf("%s:%d: %w", s.path, line, err)
		}
		if d.ID == "" {
			return fmt.Errorf("%s:%d: document has no id", s.path, line)
		}
		s.docs[d.ID] = d
	}
	return sc.Err()
}

// Read returns the newest stored document for id.
func (s *FileStore) Read(_ context.Context, id string) (Doc, error) {
	s.mu.RLock()
	d, ok := s.docs[id]
	s.mu.RUnlock()
	if !ok {
		return Doc{}, fmt.Errorf("%s: %w", id, ErrNotFound)
	}
	return d, nil
}

// Create fails with ErrConflict when the id is already stored, matching Cosmos.
func (s *FileStore) Create(ctx context.Context, d Doc) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.docs[d.ID]; exists {
		return fmt.Errorf("%s: %w", d.ID, ErrConflict)
	}
	return s.append(ctx, d)
}

// Upsert writes unconditionally.
func (s *FileStore) Upsert(ctx context.Context, d Doc) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.append(ctx, d)
}

// Replace honours the etag precondition. The file store's etags are generated per write, so
// a stale etag is detectable exactly as it is against the service.
func (s *FileStore) Replace(ctx context.Context, d Doc, etag string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	// ReplaceItem against a missing id is a 404 in Cosmos whether or not a precondition was
	// given, so the existence check is unconditional.
	cur, ok := s.docs[d.ID]
	if !ok {
		return fmt.Errorf("%s: %w", d.ID, ErrNotFound)
	}
	if etag != "" && cur.ETag != etag {
		return fmt.Errorf("%s: %w", d.ID, ErrPreconditionFailed)
	}
	return s.append(ctx, d)
}

// Provision creates the file, which is all provisioning a file backend needs.
func (s *FileStore) Provision(context.Context) error {
	_, err := os.Stat(s.path)
	if err == nil {
		return nil
	}
	if !os.IsNotExist(err) {
		return err
	}
	fh, err := os.OpenFile(s.path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	return fh.Close()
}

// Delete drops a document from the in-memory index. The file is append-only, so the line that
// stored it stays until Close compacts the file — which is also what makes a bulk purge cheap:
// one rewrite at the end rather than one per deletion.
func (s *FileStore) Delete(ctx context.Context, id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.docs[id]; !ok {
		return fmt.Errorf("%s: %w", id, ErrNotFound)
	}
	delete(s.docs, id)
	return s.budget.Take(ctx, 0)
}

// All streams every stored document in sorted id order. A file store can always do this, unlike
// Cosmos with indexing off, which is why purge is tested here.
func (s *FileStore) All(ctx context.Context, fn func(Doc) error) error {
	s.mu.RLock()
	ids := make([]string, 0, len(s.docs))
	for id := range s.docs {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	docs := make([]Doc, 0, len(ids))
	for _, id := range ids {
		docs = append(docs, s.docs[id])
	}
	s.mu.RUnlock()
	for _, d := range docs {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		if err := fn(d); err != nil {
			return err
		}
	}
	return s.budget.Take(ctx, 0)
}

// DropAll empties the store: the index is cleared and the file truncated in place. The append
// handle stays open — it was opened O_APPEND, so subsequent writes land at the new end of file.
func (s *FileStore) DropAll(ctx context.Context) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.docs = map[string]Doc{}
	if s.fh != nil {
		if err := s.fh.Truncate(0); err != nil {
			return fmt.Errorf("truncate %s: %w", s.path, err)
		}
	}
	if err := os.Truncate(s.path, 0); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("truncate %s: %w", s.path, err)
	}
	return s.budget.Take(ctx, 0)
}

// Stats reports the document count. A file store has no resource-usage headers, so Usage and
// Quota stay empty.
func (s *FileStore) Stats(context.Context) (StoreStats, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return StoreStats{Items: len(s.docs)}, nil
}

// Name reports the path, so logs show which file a run wrote to.
func (s *FileStore) Name() string { return "file:" + s.path }

// Close flushes the append handle and rewrites the file in compact form: one line per
// document, sorted by id.
//
// Compaction matters for two reasons. Append-only storage keeps every superseded state, so a
// file that has been loaded twice is twice as large and a reader that takes the first match
// instead of the last gets a stale document. And a deterministic order makes the committed
// contract fixtures diff-stable, so a golden-file change means the format changed rather than
// that the run order did.
//
// The rewrite goes to a temporary file and is renamed into place, so an interrupted close
// cannot leave a truncated store behind.
func (s *FileStore) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.fh == nil {
		return nil
	}
	if err := s.fh.Close(); err != nil {
		s.fh = nil
		return err
	}
	s.fh = nil
	return s.compact()
}

// compact rewrites the file with the current documents only. Caller holds the write lock.
func (s *FileStore) compact() error {
	ids := make([]string, 0, len(s.docs))
	for id := range s.docs {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	tmp := s.path + ".tmp"
	fh, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o644)
	if err != nil {
		return fmt.Errorf("create %s: %w", tmp, err)
	}
	w := bufio.NewWriter(fh)
	for _, id := range ids {
		line, err := json.Marshal(storedShape(s.docs[id]))
		if err != nil {
			_ = fh.Close()
			_ = os.Remove(tmp)
			return fmt.Errorf("%s: %w", id, err)
		}
		if _, err := w.Write(append(line, '\n')); err != nil {
			_ = fh.Close()
			_ = os.Remove(tmp)
			return fmt.Errorf("write %s: %w", tmp, err)
		}
	}
	if err := w.Flush(); err != nil {
		_ = fh.Close()
		_ = os.Remove(tmp)
		return fmt.Errorf("flush %s: %w", tmp, err)
	}
	if err := fh.Close(); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("close %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, s.path); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("replace %s: %w", s.path, err)
	}
	return nil
}

// Count is the number of distinct documents stored, for progress lines and tests.
func (s *FileStore) Count() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.docs)
}

// append writes one line and updates the in-memory index. Caller holds the write lock.
func (s *FileStore) append(ctx context.Context, d Doc) error {
	if d.ID == "" {
		return fmt.Errorf("document has no id")
	}
	if d.Blob == "" {
		return fmt.Errorf("%s: empty blob", d.ID)
	}
	d.ETag = newETag()
	line, err := json.Marshal(storedShape(d))
	if err != nil {
		return fmt.Errorf("%s: %w", d.ID, err)
	}
	line = append(line, '\n')
	if s.fh != nil {
		if _, err := s.fh.Write(line); err != nil {
			return fmt.Errorf("append to %s: %w", s.path, err)
		}
	}
	s.docs[d.ID] = d
	// A file write costs no RUs, but the budget still gets told so a mixed run (file store
	// for output, Cosmos for reads) accounts correctly.
	return s.budget.Take(ctx, 0)
}

// storedShape is the on-disk document: the stored fields in ADR 0001's order, and nothing else.
// Both the append and the compaction path marshal through it, so the two cannot drift and write
// different shapes for the same document.
func storedShape(d Doc) any {
	return struct {
		ID     string `json:"id"`
		Blob   string `json:"b"`
		DictID uint32 `json:"d,omitempty"`
		KG     string `json:"k,omitempty"`
	}{d.ID, d.Blob, d.DictID, d.KG}
}

func newETag() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "0" // deterministic fallback; only affects a test that asserts etag churn
	}
	return `"` + hex.EncodeToString(b[:]) + `"`
}

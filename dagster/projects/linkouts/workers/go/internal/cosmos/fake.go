package cosmos

import (
	"context"
	"fmt"
	"sort"
	"sync"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

// Fake is an in-memory Store for tests. It records every call and can be told to fail, so
// the pipeline's 409-merge, etag-retry, and throttle paths can be exercised without an
// account and without spending a single RU.
type Fake struct {
	budget ratelimit.Budget

	mu   sync.Mutex
	docs map[string]Doc
	// Ops counts calls by kind, in the order they happened.
	Ops []string
	// Charges is the total RU this fake reported to the budget, so a test can assert the
	// pipeline accounts for what the service would have billed.
	Charges float64

	// FailNext, when non-empty, maps an op name ("read", "create", "upsert", "replace") to
	// the error the next call of that kind returns. One-shot: it is cleared after use.
	FailNext map[string]error
	// ChargeFor, when non-nil, overrides the RU reported for each op kind.
	ChargeFor map[string]float64
}

var _ Store = (*Fake)(nil)

// NewFake returns an empty Fake. budget may be nil.
func NewFake(budget ratelimit.Budget) *Fake {
	if budget == nil {
		budget = ratelimit.NewUnlimited()
	}
	return &Fake{
		budget:    budget,
		docs:      map[string]Doc{},
		FailNext:  map[string]error{},
		ChargeFor: map[string]float64{},
	}
}

// Seed preloads a document, bypassing the call counters.
func (f *Fake) Seed(d Doc) *Fake {
	f.mu.Lock()
	defer f.mu.Unlock()
	if d.ETag == "" {
		d.ETag = fmt.Sprintf(`"seed-%d"`, len(f.docs))
	}
	f.docs[d.ID] = d
	return f
}

// Docs returns a copy of the stored documents.
func (f *Fake) Docs() map[string]Doc {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make(map[string]Doc, len(f.docs))
	for k, v := range f.docs {
		out[k] = v
	}
	return out
}

// OpCounts tallies Ops by kind.
func (f *Fake) OpCounts() map[string]int {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := map[string]int{}
	for _, op := range f.Ops {
		out[op]++
	}
	return out
}

// Fail arms the next call of op to return err.
func (f *Fake) Fail(op string, err error) *Fake {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.FailNext[op] = err
	return f
}

func (f *Fake) record(op string) error {
	f.mu.Lock()
	f.Ops = append(f.Ops, op)
	err := f.FailNext[op]
	if err != nil {
		delete(f.FailNext, op)
	}
	charge := f.ChargeFor[op]
	f.Charges += charge
	f.mu.Unlock()
	return err
}

func (f *Fake) Read(_ context.Context, id string) (Doc, error) {
	if err := f.record("read"); err != nil {
		return Doc{}, err
	}
	f.mu.Lock()
	d, ok := f.docs[id]
	f.mu.Unlock()
	if !ok {
		return Doc{}, fmt.Errorf("%s: %w", id, ErrNotFound)
	}
	return d, nil
}

func (f *Fake) Create(ctx context.Context, d Doc) error {
	if err := f.record("create"); err != nil {
		return err
	}
	f.mu.Lock()
	if _, exists := f.docs[d.ID]; exists {
		f.mu.Unlock()
		return fmt.Errorf("%s: %w", d.ID, ErrConflict)
	}
	d.ETag = fmt.Sprintf(`"c-%d"`, len(f.docs))
	f.docs[d.ID] = d
	f.mu.Unlock()
	return f.budget.Take(ctx, f.chargeFor("create"))
}

func (f *Fake) Upsert(ctx context.Context, d Doc) error {
	if err := f.record("upsert"); err != nil {
		return err
	}
	f.mu.Lock()
	d.ETag = fmt.Sprintf(`"u-%d"`, len(f.docs))
	f.docs[d.ID] = d
	f.mu.Unlock()
	return f.budget.Take(ctx, f.chargeFor("upsert"))
}

func (f *Fake) Replace(ctx context.Context, d Doc, etag string) error {
	if err := f.record("replace"); err != nil {
		return err
	}
	f.mu.Lock()
	cur, ok := f.docs[d.ID]
	if !ok {
		f.mu.Unlock()
		return fmt.Errorf("%s: %w", d.ID, ErrNotFound)
	}
	if etag != "" && cur.ETag != etag {
		f.mu.Unlock()
		return fmt.Errorf("%s: %w", d.ID, ErrPreconditionFailed)
	}
	d.ETag = cur.ETag + "-r"
	f.docs[d.ID] = d
	f.mu.Unlock()
	return f.budget.Take(ctx, f.chargeFor("replace"))
}

func (f *Fake) Provision(context.Context) error { return f.record("provision") }
func (f *Fake) Name() string                    { return "mem://fake" }
func (f *Fake) Close() error                    { return nil }

// Delete removes a document. A missing id is ErrNotFound, matching Cosmos, so a purge can tell
// "already gone" from "deleted now".
func (f *Fake) Delete(ctx context.Context, id string) error {
	if err := f.record("delete"); err != nil {
		return err
	}
	f.mu.Lock()
	_, exists := f.docs[id]
	if !exists {
		f.mu.Unlock()
		return fmt.Errorf("%s: %w", id, ErrNotFound)
	}
	delete(f.docs, id)
	f.mu.Unlock()
	return f.budget.Take(ctx, f.chargeFor("delete"))
}

// All streams the stored documents in sorted id order, so a purge over a fake store is
// deterministic and a test can assert exactly which documents were visited.
func (f *Fake) All(ctx context.Context, fn func(Doc) error) error {
	if err := f.record("all"); err != nil {
		return err
	}
	f.mu.Lock()
	ids := make([]string, 0, len(f.docs))
	for id := range f.docs {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	docs := make([]Doc, 0, len(ids))
	for _, id := range ids {
		docs = append(docs, f.docs[id])
	}
	f.mu.Unlock()
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
	return f.budget.Take(ctx, f.chargeFor("all"))
}

// DropAll clears the store.
func (f *Fake) DropAll(ctx context.Context) error {
	if err := f.record("dropall"); err != nil {
		return err
	}
	f.mu.Lock()
	f.docs = map[string]Doc{}
	f.mu.Unlock()
	return f.budget.Take(ctx, f.chargeFor("dropall"))
}

// Stats reports the document count, which the fake knows for free.
func (f *Fake) Stats(context.Context) (StoreStats, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return StoreStats{Items: len(f.docs)}, nil
}

func (f *Fake) chargeFor(op string) float64 {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.ChargeFor[op]
}

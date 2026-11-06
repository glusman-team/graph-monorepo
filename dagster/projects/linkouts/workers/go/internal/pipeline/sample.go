package pipeline

import (
	"math/rand"
	"sort"
	"sync"
)

// reservoir is a thread-safe Algorithm R sample with deduplication.
//
// It exists because indexing is off on this container: picking a random edge with a query
// would be a full cross-partition scan, so the worker samples ids while it streams (free, it is
// already touching every document) and the app point-reads the result. Uniformity matters —
// /random is how a reviewer stumbles onto an edge they were not looking for, and a sample
// biased toward the front of the file would only ever show one predicate.
type reservoir struct {
	k int

	mu   sync.Mutex
	ids  []string
	seen map[string]struct{}
	n    int64
	rng  *rand.Rand
}

// newReservoir returns an empty sample of at most k ids. A zero seed draws a random one, which is
// what production wants. A fixed seed makes the sample reproducible, so regenerating the committed
// contract fixtures gives the same bytes and CI can diff them.
func newReservoir(k int, seed int64) *reservoir {
	if k < 1 {
		k = 1
	}
	if seed == 0 {
		seed = rand.Int63()
	}
	return &reservoir{
		k:    k,
		ids:  make([]string, 0, k),
		seen: make(map[string]struct{}, k),
		rng:  rand.New(rand.NewSource(seed)),
	}
}

// offer adds id to the sample. Every distinct id has an equal chance of being held at the end,
// regardless of how many were offered.
func (r *reservoir) offer(id string) {
	if id == "" {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, dup := r.seen[id]; dup {
		return
	}
	if len(r.ids) < r.k {
		r.ids = append(r.ids, id)
		r.seen[id] = struct{}{}
		r.n++
		return
	}
	// Replace a random slot with probability k/n, which is what makes the sample uniform
	// over a stream of unknown length.
	r.n++
	j := r.rng.Int63n(r.n)
	if j < int64(r.k) {
		delete(r.seen, r.ids[j])
		r.ids[j] = id
		r.seen[id] = struct{}{}
	}
}

// snapshot returns a copy of the sample in arbitrary order.
// snapshot returns the sample sorted. Ids are offered from concurrent workers, so the order they
// reach the reservoir varies run to run. Sorting removes that, which is what makes a seeded run
// reproducible whenever the sample holds every id offered (the contract fixtures hold 6 edges and
// the cap is 4096). /random picks uniformly from the list, so order carries no meaning.
//
// When there are more ids than the cap, which ids survive still depends on offer order. A seed
// cannot fix that without serializing the load, and nothing needs it to.
func (r *reservoir) snapshot() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]string, len(r.ids))
	copy(out, r.ids)
	sort.Strings(out)
	return out
}

func (r *reservoir) len() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.ids)
}

// offered is how many distinct ids were seen, for the summary line: a 4096-id pool drawn from
// 130k edges should say so.
func (r *reservoir) offered() int64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.n
}

// Package ratelimit keeps a run inside a Cosmos DB request-unit budget.
//
// Cosmos reports the charge *after* the operation, so this is post-paid: each response's
// actual charge is debited from a token bucket refilled at the configured RU/s. That is
// self-correcting — an operation that costs more than expected delays the next one — and it
// avoids guessing document sizes, which Cosmos does not price linearly.
//
// The SDK already retries 429s and honours x-ms-retry-after-ms (ADR 0002), so this package
// deliberately adds no retry logic of its own: two overlapping retry loops turn a throttle
// into a storm.
package ratelimit

import (
	"context"
	"fmt"
	"math"
	"sync"
	"time"

	"golang.org/x/time/rate"
)

// Budget is the spend control every Cosmos-touching command holds.
type Budget interface {
	// Take blocks until charge RUs are available, or ctx is done. A charge of zero or less
	// is a no-op.
	Take(ctx context.Context, charge float64) error
	// Consumed is the total RU debited so far.
	Consumed() float64
	// Rate is the configured RU/s ceiling.
	Rate() float64
	// Waits is how many Take calls actually blocked, for the end-of-run report.
	Waits() int64
}

// Unlimited spends freely. It is what --dry-run, the file store, and tests use, so no code
// path has to special-case "no budget".
type Unlimited struct{ consumed atomicFloat }

// NewUnlimited returns a budget that never blocks.
func NewUnlimited() *Unlimited { return &Unlimited{} }

func (u *Unlimited) Take(_ context.Context, charge float64) error {
	if charge > 0 {
		u.consumed.add(charge)
	}
	return nil
}
func (u *Unlimited) Consumed() float64 { return u.consumed.load() }
func (u *Unlimited) Rate() float64     { return math.Inf(1) }
func (u *Unlimited) Waits() int64      { return 0 }

// TokenBucket paces spend at ruPerSec. burst is the largest single charge it can absorb at
// once; charges above it are debited in burst-sized slices so one huge write cannot wedge
// the limiter (rate.Limiter.WaitN errors when n exceeds the burst).
type TokenBucket struct {
	lim      *rate.Limiter
	ruPerSec float64
	burst    int

	mu       sync.Mutex
	consumed float64
	waits    int64
	start    time.Time
}

// New returns a budget spending at most ruPerSec. A non-positive rate means unlimited,
// which keeps "RU_BUDGET_CLI=0" a way to turn pacing off rather than a way to hang.
func New(ruPerSec float64) Budget {
	if ruPerSec <= 0 {
		return NewUnlimited()
	}
	burst := int(ruPerSec)
	if burst < 1 {
		burst = 1
	}
	tb := &TokenBucket{
		lim:      rate.NewLimiter(rate.Limit(ruPerSec), burst),
		ruPerSec: ruPerSec,
		burst:    burst,
		start:    time.Now(),
	}
	// A new limiter carries a full burst, which would dump a whole second of RU into the
	// first requests of a run; on the shared 1000 RU/s free tier that cold-start burst is
	// what 429s and aborts the run (observed at 900 RU/s: the burst drains before the first
	// charge reports back, Cosmos throttles, and five SDK retries land inside the same hot
	// second). Starting empty paces spend at the refill rate from the very first request.
	tb.lim.AllowN(time.Now(), burst)
	return tb
}

func (b *TokenBucket) Take(ctx context.Context, charge float64) error {
	if charge <= 0 {
		return nil
	}
	total := int(math.Ceil(charge))
	for remaining := total; remaining > 0; {
		n := min(remaining, b.burst)
		remaining -= n
		if !b.lim.AllowN(time.Now(), n) {
			b.mu.Lock()
			b.waits++
			b.mu.Unlock()
			if err := b.lim.WaitN(ctx, n); err != nil {
				// WaitN fails early when the wait cannot fit before the deadline, so the cause
				// is usually the caller's context, not a spent budget. Say which.
				return fmt.Errorf("cannot wait for %d RU at %g RU/s within this context: %w", n, b.ruPerSec, err)
			}
		}
	}
	b.mu.Lock()
	b.consumed += charge
	b.mu.Unlock()
	return nil
}

func (b *TokenBucket) Consumed() float64 {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.consumed
}

func (b *TokenBucket) Rate() float64 { return b.ruPerSec }

func (b *TokenBucket) Waits() int64 {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.waits
}

// Elapsed is the time since the budget was created, for the summary line.
func (b *TokenBucket) Elapsed() time.Duration { return time.Since(b.start) }

// EffectiveRUps is the realised spend rate, which is what the worker's probe report shows so a
// budget can be sized from measurement instead of guesswork.
func (b *TokenBucket) EffectiveRUps() float64 {
	secs := b.Elapsed().Seconds()
	if secs <= 0 {
		return 0
	}
	return b.Consumed() / secs
}

// atomicFloat is a mutex-free-ish float accumulator; sync/atomic has no float Add before
// Go 1.23-style generics helpers, and this keeps Unlimited allocation-free.
type atomicFloat struct {
	mu sync.Mutex
	v  float64
}

func (a *atomicFloat) add(f float64) {
	a.mu.Lock()
	a.v += f
	a.mu.Unlock()
}

func (a *atomicFloat) load() float64 {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.v
}

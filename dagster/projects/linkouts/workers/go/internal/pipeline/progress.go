package pipeline

import (
	"context"
	"sync"
	"time"
)

// Progress receives run statistics. It exists so the worker's load operation can show a rate and
// an ETA while tests capture the same numbers without parsing text.
type Progress interface {
	// Report is called periodically during a run and once at the end with final=true.
	Report(s Snapshot, final bool)
}

// NopProgress discards everything. It is the default so no call site has to nil-check.
type NopProgress struct{}

// Report implements Progress.
func (NopProgress) Report(Snapshot, bool) {}

// reporter throttles progress updates so a fast local run does not spend more time formatting
// lines than writing documents.
type reporter struct {
	sink Progress
	st   *Stats
	// ru is the live request-charge total, read from the run's budget at report time. Without
	// it a progress line would say ru=0.0 for a whole hour against Cosmos and the real number
	// would appear only in the final summary.
	ru    func() float64
	every time.Duration

	mu   sync.Mutex
	last time.Time
}

func newReporter(sink Progress, st *Stats, ru func() float64) *reporter {
	return &reporter{sink: sink, st: st, ru: ru, every: time.Second, last: time.Now()}
}

// maybe reports if the interval has elapsed. It is called per document, so it must be cheap:
// the lock and the clock read are the whole cost on the common path.
func (r *reporter) maybe(ctx context.Context) {
	if ctx.Err() != nil {
		return
	}
	r.mu.Lock()
	due := time.Since(r.last) >= r.every
	if due {
		r.last = time.Now()
	}
	r.mu.Unlock()
	if due {
		r.st.bump(func() { r.st.RU = r.ru() })
		r.sink.Report(r.st.Snapshot(), false)
	}
}

func (r *reporter) final() {
	r.st.bump(func() { r.st.RU = r.ru() })
	r.sink.Report(r.st.Snapshot(), true)
}

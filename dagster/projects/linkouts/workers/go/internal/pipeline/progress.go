package pipeline

import (
	"context"
	"fmt"
	"io"
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

// TextProgress writes one line per interval. It is deliberately line-based rather than
// cursor-redrawing: the output goes into CI logs and a redirected file as often as a terminal.
type TextProgress struct {
	W io.Writer
	// Every is the minimum gap between lines. Zero means 2s.
	Every time.Duration
	// Total, when non-zero, enables a percentage and an ETA.
	Total int

	mu   sync.Mutex
	last time.Time
}

// NewTextProgress returns a progress sink writing to w.
func NewTextProgress(w io.Writer, total int) *TextProgress {
	return &TextProgress{W: w, Every: 2 * time.Second, Total: total, last: time.Now()}
}

// Report implements Progress.
func (p *TextProgress) Report(s Snapshot, final bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.W == nil {
		return
	}
	if !final && time.Since(p.last) < p.Every {
		return
	}
	p.last = time.Now()
	rate := s.Rate()
	line := fmt.Sprintf("%s %s: %d edges (%.0f/s) created=%d merged=%d skipped=%d failed=%d ru=%.1f",
		time.Now().Format("15:04:05"), s.Key, s.Edges, rate, s.Created, s.Merged, s.Skipped, s.Failed, s.RU)
	// On an --ids tail load the filter swallows the already-stored prefix, so filtered is
	// the only number that moves while the join walks to the gap; without it the run looks
	// wedged for tens of minutes.
	if s.Filtered > 0 {
		line += fmt.Sprintf(" filtered=%d", s.Filtered)
	}
	if s.Throttled > 0 {
		line += fmt.Sprintf(" throttled=%d", s.Throttled)
	}
	if p.Total > 0 {
		pct := 100 * float64(s.Edges) / float64(p.Total)
		line += fmt.Sprintf(" %.1f%%", pct)
		if rate > 0 && s.Edges < p.Total {
			eta := time.Duration(float64(p.Total-s.Edges)/rate) * time.Second
			line += fmt.Sprintf(" eta %s", eta.Round(time.Second))
		}
	}
	if s.RawBytes > 0 {
		line += fmt.Sprintf(" ratio=%.3f", s.Ratio())
	}
	if final {
		line += " (done)"
	}
	// A closed stderr (piped to a short-lived reader) must not fail the load.
	_, _ = fmt.Fprintln(p.W, line)
}

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

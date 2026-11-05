package ratelimit

import (
	"context"
	"math"
	"strings"
	"testing"
	"time"
)

func TestUnlimitedNeverBlocks(t *testing.T) {
	b := NewUnlimited()
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	if err := b.Take(ctx, 1e9); err != nil {
		t.Fatalf("Take: %v", err)
	}
	if b.Consumed() != 1e9 {
		t.Errorf("Consumed = %v", b.Consumed())
	}
	if !math.IsInf(b.Rate(), 1) {
		t.Errorf("Rate = %v, want +Inf", b.Rate())
	}
	if b.Waits() != 0 {
		t.Errorf("Waits = %d, want 0", b.Waits())
	}
	// A zero or negative charge is a no-op, not a panic or a hang.
	if err := b.Take(ctx, 0); err != nil {
		t.Errorf("Take(0): %v", err)
	}
	if err := b.Take(ctx, -5); err != nil {
		t.Errorf("Take(-5): %v", err)
	}
}

func TestNewWithNonPositiveRateIsUnlimited(t *testing.T) {
	// RU_BUDGET_CLI=0 must turn pacing off rather than wedge every write forever.
	for _, rate := range []float64{0, -1} {
		b := New(rate)
		if _, ok := b.(*Unlimited); !ok {
			t.Errorf("New(%v) = %T, want *Unlimited", rate, b)
		}
	}
}

func TestTokenBucketPacesSpend(t *testing.T) {
	// 200 RU/s with a burst of 200, starting empty: the first 200 RU must refill (~1s),
	// and the next 100 RU half of that.
	b := New(200)
	ctx := context.Background()
	start := time.Now()
	if err := b.Take(ctx, 200); err != nil {
		t.Fatalf("first Take: %v", err)
	}
	if elapsed := time.Since(start); elapsed < 500*time.Millisecond {
		t.Errorf("the bucket starts empty, so the first 200 RU should wait ~1s, took %v", elapsed)
	}
	start = time.Now()
	if err := b.Take(ctx, 100); err != nil {
		t.Fatalf("second Take: %v", err)
	}
	elapsed := time.Since(start)
	if elapsed < 300*time.Millisecond {
		t.Errorf("100 RU at 200 RU/s should take ~500ms, took %v", elapsed)
	}
	if elapsed > 2*time.Second {
		t.Errorf("pacing overshot badly: %v", elapsed)
	}
	if b.Consumed() != 300 {
		t.Errorf("Consumed = %v, want 300", b.Consumed())
	}
	if b.Waits() == 0 {
		t.Error("no wait was recorded though the bucket was drained")
	}
}

func TestTokenBucketChunksChargesAboveBurst(t *testing.T) {
	// A single write of a 30 KB document can cost more RU than the burst allows. rate.Limiter
	// errors when n exceeds the burst, so Take must slice the charge instead of failing.
	tb, ok := New(1000).(*TokenBucket)
	if !ok {
		t.Fatal("New(1000) did not return a token bucket")
	}
	b := tb
	if b.burst != 1000 {
		t.Fatalf("burst = %d, want 1000", b.burst)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := b.Take(ctx, 2500); err != nil {
		t.Fatalf("Take above the burst: %v", err)
	}
	if b.Consumed() != 2500 {
		t.Errorf("Consumed = %v, want 2500", b.Consumed())
	}
}

func TestTakeHonoursContextCancellation(t *testing.T) {
	// One RU per second, starting empty: even the first charge has to wait ~1s, far past
	// the deadline.
	b := New(1)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Millisecond)
	defer cancel()
	err := b.Take(ctx, 1)
	if err == nil {
		t.Fatal("Take did not return when the wait could not fit the context deadline")
	}
	// WaitN refuses early rather than sleeping past the deadline, so ctx may not be done yet.
	// What matters is that Take returned instead of blocking, and that the error names the
	// real cause instead of blaming the budget.
	if !strings.Contains(err.Error(), "within this context") {
		t.Errorf("error does not explain the deadline: %v", err)
	}
	if strings.Contains(err.Error(), "exhausted") {
		t.Errorf("error blames the budget for a context deadline: %v", err)
	}
}

func TestConcurrentTakesAreSerialised(t *testing.T) {
	b := New(100000) // fast enough that this stays a concurrency test, not a timing test
	const workers, charges = 16, 25
	errs := make(chan error, workers*charges)
	for range workers {
		go func() {
			for range charges {
				errs <- b.Take(context.Background(), 1)
			}
		}()
	}
	for range workers * charges {
		if err := <-errs; err != nil {
			t.Fatalf("Take: %v", err)
		}
	}
	if got, want := b.Consumed(), float64(workers*charges); got != want {
		t.Errorf("Consumed = %v, want %v (lost or double-counted charges)", got, want)
	}
}

func TestEffectiveRUps(t *testing.T) {
	b := New(500).(*TokenBucket)
	if err := b.Take(context.Background(), 50); err != nil {
		t.Fatalf("Take: %v", err)
	}
	if got := b.EffectiveRUps(); got <= 0 {
		t.Errorf("EffectiveRUps = %v, want a positive realised rate", got)
	}
	fresh := &TokenBucket{start: time.Now()}
	if got := fresh.EffectiveRUps(); got != 0 {
		t.Errorf("EffectiveRUps with no spend = %v, want 0", got)
	}
}

func TestBudgetImplementationsAreSubstitutable(t *testing.T) {
	// The store takes a Budget and must not need to know which one it holds, so both are
	// driven through the interface here.
	for name, b := range map[string]Budget{"paced": New(100000), "free": NewUnlimited()} {
		if err := b.Take(context.Background(), 5); err != nil {
			t.Errorf("%s: Take: %v", name, err)
		}
		if b.Consumed() != 5 {
			t.Errorf("%s: Consumed = %v, want 5", name, b.Consumed())
		}
		if b.Rate() <= 0 {
			t.Errorf("%s: Rate = %v, want a positive ceiling", name, b.Rate())
		}
	}
}

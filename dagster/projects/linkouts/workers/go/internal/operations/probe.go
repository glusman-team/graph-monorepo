package operations

import (
	"context"
	"errors"
	"fmt"
	"math"
	"math/rand"
	"time"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/pipeline"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

type ProbeOptions struct {
	Count  int
	Seed   *int64
	Budget ratelimit.Budget
}

type ProbeReport struct {
	Store                string        `json:"store"`
	Count                int           `json:"count"`
	Elapsed              time.Duration `json:"elapsed_ns"`
	TotalRU              float64       `json:"total_ru"`
	MeanRU               float64       `json:"mean_ru"`
	WorstRU              float64       `json:"worst_ru"`
	WorstID              string        `json:"worst_id"`
	ReadsPerSecond       float64       `json:"reads_per_second"`
	BudgetReadsPerSecond float64       `json:"budget_reads_per_second"`
	StoredBytes          int64         `json:"stored_bytes"`
	ResolvedBytes        int64         `json:"resolved_bytes"`
	MaxStoredBytes       int64         `json:"max_stored_bytes"`
	MissingPools         int           `json:"missing_pools"`
}

func (r ProbeReport) ValidateIntegrity() error {
	if r.MissingPools != 0 {
		return fmt.Errorf("pool index references %d missing pool documents", r.MissingPools)
	}
	if r.TotalRU < 0 || r.MeanRU < 0 || r.WorstRU < 0 {
		return fmt.Errorf("read-path probe produced a negative RU charge")
	}
	return nil
}

func Probe(
	ctx context.Context,
	store cosmos.Store,
	dict []byte,
	opts ProbeOptions,
) (ProbeReport, error) {
	if opts.Count <= 0 {
		return ProbeReport{}, fmt.Errorf("probe count must be positive")
	}
	if opts.Budget == nil {
		opts.Budget = ratelimit.NewUnlimited()
	}
	ids, missing, err := probeIDs(ctx, store, dict, opts.Count, opts.Seed)
	if err != nil {
		return ProbeReport{}, err
	}
	if len(ids) == 0 {
		return ProbeReport{}, fmt.Errorf("no ids to probe: load a release before probing")
	}
	report := ProbeReport{Store: store.Name(), Count: len(ids), MissingPools: missing}
	start := time.Now()
	for _, id := range ids {
		before := opts.Budget.Consumed()
		doc, err := store.Read(ctx, id)
		if err != nil {
			return ProbeReport{}, fmt.Errorf("read %s: %w", id, err)
		}
		charge := opts.Budget.Consumed() - before
		report.TotalRU += charge
		if charge > report.WorstRU {
			report.WorstRU, report.WorstID = charge, id
		}
		storedBytes := int64(len(doc.Blob))
		report.StoredBytes += storedBytes
		if storedBytes > report.MaxStoredBytes {
			report.MaxStoredBytes = storedBytes
		}
		stored, err := pipeline.DictionaryFor(doc.DictID, dict)
		if err != nil {
			return ProbeReport{}, err
		}
		blob, err := codec.DecodeBlob(doc.Blob, stored)
		if err != nil {
			return ProbeReport{}, fmt.Errorf("decode %s: %w", id, err)
		}
		for _, key := range blob.VersionKeys() {
			resolved, err := blob.Resolve(key)
			if err != nil {
				return ProbeReport{}, fmt.Errorf("resolve %s version %s: %w", id, key, err)
			}
			raw, err := codec.Marshal(resolved)
			if err != nil {
				return ProbeReport{}, err
			}
			report.ResolvedBytes += int64(len(raw))
		}
	}
	report.Elapsed = time.Since(start)
	report.MeanRU = report.TotalRU / float64(len(ids))
	if err := report.ValidateIntegrity(); err != nil {
		return ProbeReport{}, err
	}
	if report.Elapsed > 0 {
		report.ReadsPerSecond = float64(len(ids)) / report.Elapsed.Seconds()
	}
	if report.MeanRU > 0 {
		report.BudgetReadsPerSecond = opts.Budget.Rate() / report.MeanRU
		if math.IsInf(report.BudgetReadsPerSecond, 0) || math.IsNaN(report.BudgetReadsPerSecond) {
			report.BudgetReadsPerSecond = 0
		}
	}
	return report, nil
}

func probeIDs(
	ctx context.Context,
	store cosmos.Store,
	dict []byte,
	count int,
	seed *int64,
) ([]string, int, error) {
	index, err := pipeline.ReadPoolIndex(ctx, store, dict)
	if err != nil {
		if errors.Is(err, cosmos.ErrNotFound) {
			return nil, 0, nil
		}
		return nil, 0, err
	}
	var ids []string
	missing := 0
	for _, slug := range pipeline.Slugs(index) {
		for _, release := range pipeline.Releases(index, slug) {
			pool, err := pipeline.ReadPool(ctx, store, dict, slug, release.Label)
			if errors.Is(err, cosmos.ErrNotFound) {
				missing++
				continue
			}
			if err != nil {
				return nil, missing, err
			}
			ids = append(ids, pool.IDs...)
		}
	}
	if len(ids) > count {
		var rng *rand.Rand
		if seed == nil {
			rng = rand.New(rand.NewSource(time.Now().UnixNano()))
		} else {
			rng = rand.New(rand.NewSource(*seed))
		}
		rng.Shuffle(len(ids), func(i, j int) { ids[i], ids[j] = ids[j], ids[i] })
		ids = ids[:count]
	}
	return ids, missing, nil
}

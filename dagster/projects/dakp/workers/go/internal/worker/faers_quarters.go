package worker

import (
	"context"
	"encoding/gob"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"sort"
	"time"

	"github.com/glusman-team/dakp/go/internal/blake3store"
	"github.com/glusman-team/dakp/go/internal/faers"
	"golang.org/x/sync/errgroup"
)

// Bump for changes to source selection, parsing, joins or shard encoding.
const faersQuarterOperation = "parse_faers_quarter_v1"
const faersQuarterMediaType = "application/vnd.dakp.faers-quarter+gob"

type faersQuarter struct {
	Quarter  string
	Cases    []faers.Case
	Deletes  []faers.DeleteAuditRow
	Warnings []faers.Warning
}

// ParseFAERSQuarters builds independent, reusable shards before cross-quarter dedup.
const faersParseTaskOperation = "parse_faers_quarters_task_v1"

func ParseFAERSQuarters(ctx context.Context, cfg Config, inputs []ArtifactRef) ([]ArtifactRef, error) {
	store := Store{Workdir: cfg.Workdir}
	upstream := upstreamInputIDs(inputs)
	if !cfg.Force {
		if cached := store.FindByOperation(faersParseTaskOperation, upstream); cached != nil {
			Stat(ctx, "parse_faers_quarters", "skipped_already_done", len(cached))
			return cached, nil
		}
	}
	refs, err := parseFAERSQuartersUnscoped(ctx, cfg, inputs)
	if err == nil {
		store.RecordOperation(faersParseTaskOperation, upstream, refs)
	}
	return refs, err
}

func parseFAERSQuartersUnscoped(ctx context.Context, cfg Config, inputs []ArtifactRef) ([]ArtifactRef, error) {
	if cfg.MemoryBudgetGB > 0 {
		defer debug.SetMemoryLimit(debug.SetMemoryLimit(int64(cfg.MemoryBudgetGB) << 30))
		Stat(ctx, "parse_faers_quarters", "memory_budget_gb", cfg.MemoryBudgetGB)
	}
	store := Store{Workdir: cfg.Workdir}
	// Use the workdir filesystem so staging can hardlink large acquisition artifacts.
	if err := os.MkdirAll(cfg.Workdir, 0o755); err != nil {
		return nil, err
	}
	scratch, err := os.MkdirTemp(cfg.Workdir, "faers-parse-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(scratch)
	inDir := filepath.Join(scratch, "in")
	staged, err := stageInputPaths(inputs, inDir)
	if err != nil {
		return nil, err
	}
	quarters, closeAll, err := inventoryFAERSQuarters(inDir, staged)
	if err != nil {
		return nil, err
	}
	defer closeAll()
	if len(quarters) == 0 {
		return nil, fmt.Errorf("parse_faers_quarters: no FAERS ASCII .txt sources staged in %s", inDir)
	}

	order := make([]string, 0, len(quarters))
	for q := range quarters {
		order = append(order, q)
	}
	sort.Sort(sort.Reverse(sort.StringSlice(order)))
	refs := make([]ArtifactRef, len(order))
	hits := make([]bool, len(order))
	threads := runtime.GOMAXPROCS(0)
	if cfg.Threads > 0 {
		threads = min(threads, cfg.Threads)
	}
	// A quarter retains all its normalized family tables during the join. Keep a
	// separate memory-oriented bound even on hosts with many available CPU cores.
	parallelQuarters := min(4, threads)
	if cfg.MemoryBudgetGB > 0 {
		parallelQuarters = min(parallelQuarters, max(1, cfg.MemoryBudgetGB/4))
	}
	slots := make(chan struct{}, threads)
	group, work := errgroup.WithContext(ctx)
	group.SetLimit(parallelQuarters)
	for i, q := range order {
		group.Go(func() error {
			ref, hit, err := loadOrParseFAERSQuarter(work, store, cfg.Force, q, quarters[q], slots, scratch)
			if err != nil {
				return fmt.Errorf("FAERS quarter %s: %w", q, err)
			}
			refs[i], hits[i] = ref, hit
			return nil
		})
	}
	if err := group.Wait(); err != nil {
		return nil, err
	}
	count := 0
	for _, hit := range hits {
		if hit {
			count++
		}
	}
	Stat(ctx, "parse_faers_quarters", "quarter_cache_hits", count)
	Stat(ctx, "parse_faers_quarters", "quarters_parsed", len(refs)-count)
	return refs, nil
}

func loadOrParseFAERSQuarter(ctx context.Context, store Store, force bool, quarter string, handles map[string]faersSourceHandle, slots chan struct{}, scratch string) (ArtifactRef, bool, error) {
	start := time.Now()
	key := []string{quarter}
	inputs := make([]string, 0, len(handles))
	for family, h := range handles {
		if h.InputID == "" {
			return ArtifactRef{}, false, fmt.Errorf("missing artifact content hash for %s", h.Name)
		}
		key = append(key, blake3store.HashBytes(fmt.Appendf(nil, "%s:%s:%s:%s", family, h.InputID, h.Name, h.Identity)))
		inputs = append(inputs, h.InputID)
	}
	if !force {
		if cached := store.FindByOperation(faersQuarterOperation, key); len(cached) == 1 {
			return cached[0], true, nil
		}
	}
	warn := &faers.Warnings{}
	families, err := parseFAERSQuarter(ctx, quarter, handles, warn, slots)
	if err != nil {
		return ArtifactRef{}, false, err
	}
	select {
	case slots <- struct{}{}:
		defer func() { <-slots }()
	case <-ctx.Done():
		return ArtifactRef{}, false, ctx.Err()
	}
	cases := faers.BuildQuarterCases(families, quarter, faers.DeletedPrimaryIDs(families["DELETE"]), warn)
	var deletes []faers.DeleteAuditRow
	if del := families["DELETE"]; del != nil {
		deletes = faers.BuildDeleteAudit([]*faers.Table{del})
	}
	warnings := warn.Items()
	sort.Slice(warnings, func(i, j int) bool {
		a, b := warnings[i], warnings[j]
		if a.Family != b.Family {
			return a.Family < b.Family
		}
		if a.Code != b.Code {
			return a.Code < b.Code
		}
		if a.Message != b.Message {
			return a.Message < b.Message
		}
		return a.Count < b.Count
	})
	shard := faersQuarter{Quarter: quarter, Cases: cases, Deletes: deletes, Warnings: warnings}
	dir := filepath.Join(store.InterimDir(), "faers", "quarters", OpIndexKey(faersQuarterOperation, key))
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return ArtifactRef{}, false, err
	}
	name := quarter + ".gob"
	path := filepath.Join(dir, name)
	f, err := os.Create(filepath.Join(scratch, name))
	if err != nil {
		return ArtifactRef{}, false, err
	}
	encodeErr := gob.NewEncoder(f).Encode(shard)
	closeErr := f.Close()
	if encodeErr != nil {
		return ArtifactRef{}, false, encodeErr
	}
	if closeErr != nil {
		return ArtifactRef{}, false, closeErr
	}
	if err := os.Rename(f.Name(), path); err != nil {
		return ArtifactRef{}, false, err
	}
	sort.Strings(inputs)
	ref, err := store.Register(RegisterInput{Path: path, MediaType: faersQuarterMediaType, Rows: int64(len(cases)), Inputs: inputs, Warnings: int64(warn.Total()), Operation: faersQuarterOperation})
	if err != nil {
		return ArtifactRef{}, false, err
	}
	store.RecordOperation(faersQuarterOperation, key, []ArtifactRef{ref})
	Stat(ctx, "parse_faers_quarters", quarter+".elapsed_s", fmt.Sprintf("%.3f", time.Since(start).Seconds()))
	return ref, false, nil
}

func readFAERSQuarter(path string) (faersQuarter, error) {
	var shard faersQuarter
	f, err := os.Open(path)
	if err != nil {
		return shard, err
	}
	defer f.Close()
	if err := gob.NewDecoder(f).Decode(&shard); err != nil {
		return shard, fmt.Errorf("decode FAERS quarter %s: %w", path, err)
	}
	return shard, nil
}

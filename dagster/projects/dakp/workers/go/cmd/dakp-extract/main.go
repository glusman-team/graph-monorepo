// Command dakp-extract is the Dagster Pipes extraction worker for the DAKP
// stages whose heavy parsing runs in Go (DailyMed SPL, FAERS ASCII quarters,
// Drugs@FDA).
//
// The launching asset (dakp_dag) passes everything through pipes extras:
//
//	stage           "dailymed" | "faers" | "drugsfda"     (required)
//	input_refs      path to the input ArtifactRefs JSON   (required)
//	output_refs     path to write the output refs JSON    (required)
//	workdir         the run workdir root                  (required)
//	force           bool, bypass the op-index skip        (optional)
//	memory_budget_gb, threads, quarter_limit, ...         (optional knobs)
//
// A nested "config" object (or JSON string) is accepted as an alternative to
// the flat keys. The worker reports a materialization with metadata
// {stage, refs, rows_total, cache_hit_skipped} plus a data version derived
// from the output artifact ids, and exits non-zero WITHOUT writing
// output_refs when extraction fails (the asset reads it only on success).
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"

	dagsterpipes "github.com/hupe1980/dagster-pipes-go"

	"github.com/glusman-team/dakp/go/internal/pipesutil"
	"github.com/glusman-team/dakp/go/internal/worker"
)

// extractArgs is the resolved per-run configuration (see the package comment
// for the extras it is derived from).
type extractArgs struct {
	Stage      string
	InputRefs  string
	OutputRefs string
	Config     worker.Config
}

func main() {
	session, err := dagsterpipes.New[map[string]any]()
	if err != nil {
		log.Fatalf("dagster pipes session: %v", err)
	}
	defer func() {
		if err := session.Close(); err != nil {
			log.Printf("close session: %v", err)
		}
	}()

	run := func(ctx *dagsterpipes.Context[map[string]any]) error {
		args, err := parseArgs(ctx.Extras())
		if err != nil {
			return err
		}
		return run(context.Background(), args, ctx)
	}
	if err := session.Run(run); err != nil {
		log.Fatalf("dakp-extract: %v", err)
	}
}

// parseArgs resolves the extras payload into extractArgs. Flat keys win over
// a nested "config" object; JSON numbers decode as float64, so integer knobs
// accept both int and float64.
func parseArgs(extras map[string]any) (extractArgs, error) {
	if extras == nil {
		return extractArgs{}, fmt.Errorf("no pipes extras: stage/input_refs/output_refs/workdir are required")
	}
	cfg, err := configFromExtras(extras)
	if err != nil {
		return extractArgs{}, err
	}
	args := extractArgs{Config: cfg}
	var ok bool
	if args.Stage, ok = extras["stage"].(string); !ok || args.Stage == "" {
		return extractArgs{}, fmt.Errorf("extras.stage is required (dailymed|faers|drugsfda)")
	}
	if args.Stage != "dailymed" && args.Stage != "faers" && args.Stage != "drugsfda" {
		return extractArgs{}, fmt.Errorf("extras.stage %q is not one of dailymed|faers|drugsfda", args.Stage)
	}
	if args.InputRefs, ok = extras["input_refs"].(string); !ok || args.InputRefs == "" {
		return extractArgs{}, fmt.Errorf("extras.input_refs (path to input refs JSON) is required")
	}
	if args.OutputRefs, ok = extras["output_refs"].(string); !ok || args.OutputRefs == "" {
		return extractArgs{}, fmt.Errorf("extras.output_refs (path to write output refs JSON) is required")
	}
	return args, nil
}

// configFromExtras builds worker.Config from the flat extras keys, layered
// over an optional nested "config" object (or JSON string).
func configFromExtras(extras map[string]any) (worker.Config, error) {
	cfg := worker.Config{}
	if raw, present := extras["config"]; present && raw != nil {
		data, err := json.Marshal(raw)
		if err != nil {
			return cfg, fmt.Errorf("extras.config: %w", err)
		}
		if s, isString := raw.(string); isString {
			data = []byte(s)
		}
		if err := json.Unmarshal(data, &cfg); err != nil {
			return cfg, fmt.Errorf("extras.config is not a valid worker config: %w", err)
		}
	}
	if s, ok := extras["workdir"].(string); ok && s != "" {
		cfg.Workdir = s
	}
	if s, ok := extras["fixture_root"].(string); ok {
		cfg.FixtureRoot = s
	}
	if s, ok := extras["profile"].(string); ok {
		cfg.Profile = s
	}
	cfg.Force = cfg.Force || boolValue(extras["force"])
	cfg.Threads = firstNonZeroInt(cfg.Threads, intValue(extras["threads"]))
	cfg.QuarterLimit = firstNonZeroInt(cfg.QuarterLimit, intValue(extras["quarter_limit"]))
	cfg.ReleaseLimit = firstNonZeroInt(cfg.ReleaseLimit, intValue(extras["release_limit"]))
	cfg.MemoryBudgetGB = firstNonZeroInt(cfg.MemoryBudgetGB, intValue(extras["memory_budget_gb"]))
	if cfg.Workdir == "" {
		return cfg, fmt.Errorf("workdir is required (extras.workdir or extras.config.workdir)")
	}
	return cfg, nil
}

func boolValue(v any) bool {
	b, _ := v.(bool)
	return b
}

// intValue accepts the int or float64 form of a JSON number (pipes extras
// decode numbers as float64).
func intValue(v any) int {
	switch n := v.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	default:
		return 0
	}
}

func firstNonZeroInt(a, b int) int {
	if a != 0 {
		return a
	}
	return b
}

// run executes one extraction stage end to end: probe the op-index skip BEFORE
// dispatching (post-run the extractor itself records the op-index entry, so a
// probe after it always reports a hit), dispatch, write the output refs file,
// report the materialization.
func run(ctx context.Context, args extractArgs, pipes *dagsterpipes.Context[map[string]any]) error {
	inputs, err := worker.ReadRefsFile(args.InputRefs)
	if err != nil {
		return fmt.Errorf("read input refs: %w", err)
	}
	cacheHit := !args.Config.Force && cacheSkipProbe(ctx, args, inputs)

	extract := worker.ExtractFor(args.Stage)
	if extract == nil {
		return fmt.Errorf("unknown stage %q", args.Stage) // parseArgs already gates this
	}
	outputs, err := extract(ctx, args.Config, inputs)
	if err != nil {
		return err
	}
	if err := worker.WriteRefsFile(args.OutputRefs, outputs); err != nil {
		return fmt.Errorf("write output refs: %w", err)
	}

	rows := totalRows(outputs)
	_ = pipes.LogInfo(fmt.Sprintf("%s: refs = %d rows = %d cache_hit = %t", args.Stage, len(outputs), rows, cacheHit))
	return pipesutil.ReportMaterialization(pipes, dataVersion(outputs), pipesutil.CacheInfo{
		Key:          dataVersion(outputs),
		ArtifactPath: args.OutputRefs,
		Hit:          cacheHit,
		InputBytes:   int64(len(inputs)),
	}, rows)
}

// cacheSkipProbe reports whether the op-index already holds this stage's
// outputs for exactly these upstream ids (the extractor re-checks internally;
// this probe exists so the materialization metadata can say cache_hit=true).
func cacheSkipProbe(ctx context.Context, args extractArgs, inputs []worker.ArtifactRef) bool {
	store := worker.Store{Workdir: args.Config.Workdir}
	ids := make([]string, len(inputs))
	for i, ref := range inputs {
		ids[i] = ref.Blake3
	}
	return store.FindByOperation(worker.TaskOperation(args.Stage), ids) != nil
}

// totalRows sums the refs' row counts where present (0 when unknown).
func totalRows(refs []worker.ArtifactRef) int64 {
	var rows int64
	for _, ref := range refs {
		if ref.Rows != nil {
			rows += *ref.Rows
		}
	}
	return rows
}

// dataVersion derives the materialization data version from the output ids:
// BLAKE3 of the newline-joined blake3 ids and the worker version.
func dataVersion(refs []worker.ArtifactRef) string {
	parts := make([]string, len(refs)+1)
	parts[0] = pipesutil.WorkerVersion
	for i, ref := range refs {
		parts[i+1] = ref.Blake3
	}
	return pipesutil.HashInput([]byte(strings.Join(parts, "\n")))
}

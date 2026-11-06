package worker

import (
	"bytes"
	"context"
	"fmt"
	"log/slog"
	"path/filepath"
	"time"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/config"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/engine"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/operations"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/pipeline"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/version"
)

func runStatus(ctx context.Context, cfg *StatusConfig, settings config.Config) (result map[string]any, err error) {
	dict, err := operations.ReadDictionary(cfg.DictionaryPath)
	if err != nil {
		return nil, err
	}
	budget := ratelimit.New(settings.RUps)
	store, err := operations.OpenStore(ctx, cfg.Store, settings, budget)
	if err != nil {
		return nil, err
	}
	defer operations.CloseWithError(store.Close, &err)
	report, err := operations.Status(ctx, store, dict, budget, cfg.CheckPools)
	if err != nil {
		return nil, err
	}
	if err := writeJSONAtomic(cfg.OutputPath, report); err != nil {
		return nil, err
	}
	return map[string]any{
		"operation": "status", "report_path": cfg.OutputPath, "rows": max(report.Stats.Items, 0),
		"knowledge_graphs": len(report.KnowledgeGraphs), "pool_index_present": report.PoolIndexPresent,
		"ru": report.RU, "worker_version": WorkerVersion,
	}, nil
}

func runProbe(ctx context.Context, cfg *ProbeConfig, settings config.Config) (result map[string]any, err error) {
	dict, err := operations.ReadDictionary(cfg.DictionaryPath)
	if err != nil {
		return nil, err
	}
	budget := ratelimit.New(settings.RUps)
	store, err := operations.OpenStore(ctx, cfg.Store, settings, budget)
	if err != nil {
		return nil, err
	}
	defer operations.CloseWithError(store.Close, &err)
	report, err := operations.Probe(ctx, store, dict, operations.ProbeOptions{
		Count: cfg.Count, Seed: cfg.Seed, Budget: budget,
	})
	if err != nil {
		return nil, err
	}
	if err := writeJSONAtomic(cfg.OutputPath, report); err != nil {
		return nil, err
	}
	if cfg.MaxMeanRU != nil && report.MeanRU > *cfg.MaxMeanRU {
		return nil, fmt.Errorf("mean read cost %.2f RU exceeds threshold %.2f", report.MeanRU, *cfg.MaxMeanRU)
	}
	if cfg.MaxWorstRU != nil && report.WorstRU > *cfg.MaxWorstRU {
		return nil, fmt.Errorf("worst read cost %.2f RU exceeds threshold %.2f", report.WorstRU, *cfg.MaxWorstRU)
	}
	return map[string]any{
		"operation": "probe", "report_path": cfg.OutputPath, "rows": report.Count,
		"mean_ru": report.MeanRU, "worst_ru": report.WorstRU,
		"missing_pools": report.MissingPools, "worker_version": WorkerVersion,
	}, nil
}

func runGet(ctx context.Context, cfg *GetConfig, settings config.Config) (result map[string]any, err error) {
	dict, err := operations.ReadDictionary(cfg.DictionaryPath)
	if err != nil {
		return nil, err
	}
	store, err := operations.OpenStore(ctx, cfg.Store, settings, ratelimit.NewUnlimited())
	if err != nil {
		return nil, err
	}
	defer operations.CloseWithError(store.Close, &err)
	report, err := operations.Get(ctx, store, dict, operations.GetOptions{
		EdgeID: cfg.EdgeID, VersionKey: cfg.VersionKey, List: cfg.ListVersions, Raw: cfg.Raw,
	})
	if err != nil {
		return nil, err
	}
	if err := writeJSONAtomic(cfg.OutputPath, report); err != nil {
		return nil, err
	}
	return map[string]any{
		"operation": "get", "report_path": cfg.OutputPath, "rows": 1,
		"edge_id": cfg.EdgeID, "version_key": report.VersionKey,
		"stored_bytes": report.StoredBytes, "dictionary_id": report.DictionaryID,
		"worker_version": WorkerVersion,
	}, nil
}

func runRig(cfg *RigConfig) (map[string]any, error) {
	var output bytes.Buffer
	report, err := operations.InspectKGX(cfg.EdgesPath, operations.RigOptions{
		Limit: cfg.Limit, Predicate: cfg.Predicate, EmitConfig: cfg.EmitConfig,
	}, &output)
	if err != nil {
		return nil, err
	}
	if err := writeBytesAtomic(cfg.OutputPath, output.Bytes()); err != nil {
		return nil, err
	}
	return map[string]any{
		"operation": "rig", "report_path": cfg.OutputPath, "rows": report.Seen,
		"malformed": report.Malformed, "predicates": len(report.Predicates),
		"emit_config": cfg.EmitConfig, "worker_version": WorkerVersion,
	}, nil
}

func runTrainDict(ctx context.Context, cfg *TrainDictConfig, settings config.Config) (map[string]any, error) {
	nodesPath, err := filepath.Abs(cfg.NodesPath)
	if err != nil {
		return nil, fmt.Errorf("resolve nodes path: %w", err)
	}
	edgesPath, err := filepath.Abs(cfg.EdgesPath)
	if err != nil {
		return nil, fmt.Errorf("resolve edges path: %w", err)
	}
	eng, err := engine.Open(cfg.Engine, settings.ChdbCacheDir, cfg.Threads)
	if err != nil {
		return nil, fmt.Errorf("open join engine: %w", err)
	}
	result, trainErr := operations.TrainDictionary(ctx, operations.TrainDictionaryOptions{
		NodesPath: nodesPath, EdgesPath: edgesPath, Samples: cfg.Samples,
		Threads: cfg.Threads, Engine: eng,
	})
	if closeErr := eng.Close(); closeErr != nil && trainErr == nil {
		trainErr = closeErr
	}
	if trainErr != nil {
		return nil, trainErr
	}
	if err := writeBytesAtomic(cfg.OutputPath, result.Bytes); err != nil {
		return nil, err
	}
	details := map[string]any{
		"dictionary_id": result.ID, "samples_seen": result.SamplesSeen,
		"samples_used": result.SamplesUsed, "output_path": cfg.OutputPath,
		"bytes": len(result.Bytes),
	}
	if err := writeOperationReport(cfg.ReportPath, "train_dict", "success", details); err != nil {
		return nil, err
	}
	return map[string]any{
		"operation": "train_dict", "output_path": cfg.OutputPath, "report_path": cfg.ReportPath,
		"rows": result.SamplesUsed, "dictionary_id": result.ID, "bytes": len(result.Bytes),
		"worker_version": WorkerVersion,
	}, nil
}

func runInit(ctx context.Context, cfg *InitConfig, settings config.Config) (result map[string]any, err error) {
	if cfg.Store == "cosmos" && !settings.Cosmos.Ready() {
		return nil, fmt.Errorf("no Cosmos account configured in the worker environment")
	}
	store, err := operations.OpenStore(ctx, cfg.Store, settings, ratelimit.NewUnlimited())
	if err != nil {
		return nil, err
	}
	defer operations.CloseWithError(store.Close, &err)
	if err := store.Provision(ctx); err != nil {
		return nil, err
	}
	details := map[string]any{
		"store": store.Name(), "database": settings.Cosmos.Database,
		"container": settings.Cosmos.Container, "partition_key": config.PartitionKeyPath,
		"indexing": "none", "throughput": settings.Cosmos.Throughput,
		"credential_configured": settings.Cosmos.Ready(),
	}
	if err := writeOperationReport(cfg.OutputPath, "init", "success", details); err != nil {
		return nil, err
	}
	return map[string]any{
		"operation": "init", "report_path": cfg.OutputPath, "rows": 0,
		"store": store.Name(), "worker_version": WorkerVersion,
	}, nil
}

func runPurge(ctx context.Context, cfg *PurgeConfig, settings config.Config) (result map[string]any, err error) {
	dict, err := operations.ReadDictionary(cfg.DictionaryPath)
	if err != nil {
		return nil, err
	}
	budget := ratelimit.New(settings.RUps)
	if cfg.DryRun {
		budget = ratelimit.NewUnlimited()
	}
	store, err := operations.OpenStore(ctx, cfg.Store, settings, budget)
	if err != nil {
		return nil, err
	}
	defer operations.CloseWithError(store.Close, &err)
	expected := "DELETE " + store.Name() + " " + cfg.Scope
	if cfg.Scope == "kg" {
		expected += " " + cfg.KG
	}
	if cfg.Scope == "release" {
		expected += " " + cfg.Key
	}
	if cfg.Scope == "container" {
		expected = "DROP " + store.Name()
	}
	if !cfg.DryRun && cfg.Confirmation != expected {
		return nil, fmt.Errorf("refusing destructive operation without exact confirmation %q", expected)
	}
	status := "success"
	if cfg.DryRun {
		status = "dry_run"
	}
	var details map[string]any
	if cfg.Scope == "container" {
		dropper, ok := store.(interface{ DropOnly(context.Context) error })
		if !ok {
			return nil, fmt.Errorf("scope=container requires Cosmos; this backend is %s", store.Name())
		}
		if err := dropper.DropOnly(ctx); err != nil {
			return nil, err
		}
		details = map[string]any{"store": store.Name(), "dropped": true, "recreated": false}
	} else {
		opts := pipeline.PurgeOptions{
			Store: store, Dict: dict, Budget: budget, All: cfg.Scope == "all", DryRun: cfg.DryRun,
			OnScan: func(scanned, matched int64) {
				slog.Info("linkouts purge progress", "scanned", scanned, "matched", matched)
			},
		}
		if cfg.Scope == "kg" {
			opts.Slug = version.Slug(cfg.KG)
		}
		if cfg.Scope == "release" {
			parsed, parseErr := version.Parse(cfg.Key)
			if parseErr != nil {
				return nil, parseErr
			}
			opts.Slug, opts.VersionLabel = parsed.Slug(), parsed.Version()
		}
		stats, err := pipeline.Purge(ctx, opts)
		if err != nil {
			return nil, err
		}
		details = map[string]any{"store": store.Name(), "scope": cfg.Scope, "stats": stats}
	}
	if err := writeOperationReport(cfg.OutputPath, "purge", status, details); err != nil {
		return nil, err
	}
	return map[string]any{
		"operation": "purge", "report_path": cfg.OutputPath, "rows": 0,
		"dry_run": cfg.DryRun, "scope": cfg.Scope, "worker_version": WorkerVersion,
	}, nil
}

func writeOperationReport(path, operation, status string, details map[string]any) error {
	return writeJSONAtomic(path, OperationReport{
		SchemaVersion: ReportSchema,
		Operation:     operation,
		Status:        status,
		CreatedAt:     time.Now().UTC(),
		Details:       details,
	})
}

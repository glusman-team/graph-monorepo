package worker

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/config"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/engine"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/operations"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/pipeline"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

// runLoad performs a direct merge/load into a persistent target store.
//
// Unlike stage, this operation is not content-addressed or resumable: it is intended for a
// narrow Friday-night release path where the target already holds earlier merged releases and
// the caller needs the existing merge semantics without blue/green publication.
func runLoad(ctx context.Context, cfg *LoadConfig, settings config.Config) (map[string]any, error) {
	nodes, err := RefForFile(cfg.NodesPath, NDJSONMediaType)
	if err != nil {
		return nil, err
	}
	edges, err := RefForFile(cfg.EdgesPath, NDJSONMediaType)
	if err != nil {
		return nil, err
	}
	inputs := []ArtifactRef{nodes, edges}
	var dictionary *ArtifactRef
	if cfg.DictionaryPath != "" {
		ref, err := RefForFile(cfg.DictionaryPath, DictionaryMediaType)
		if err != nil {
			return nil, err
		}
		dictionary = &ref
	}
	identity := map[string]any{
		"worker_version": WorkerVersion,
		"key":            cfg.Key,
		"base_key":       cfg.BaseKey,
		"store":          cfg.Store,
		"inputs":         inputs,
		"dictionary":     dictionary,
		"zstd_level":     cfg.ZstdLevel,
		"no_repack":      cfg.NoRepack,
		"sample_size":    cfg.SampleSize,
		"sample_seed":    cfg.SampleSeed,
		"sampled_at":     cfg.SampledAt,
		"engine":         cfg.Engine,
	}
	cacheKey, err := fingerprintDigest(identity)
	if err != nil {
		return nil, err
	}
	metadata := map[string]any{
		"operation":      "load",
		"worker_version": WorkerVersion,
		"cache_key":      cacheKey,
		"report_path":    cfg.ReportPath,
		"store":          cfg.Store,
		"key":            cfg.Key,
	}

	dict, err := operations.ReadDictionary(cfg.DictionaryPath)
	if err != nil {
		return nil, err
	}
	eng, err := engine.Open(cfg.Engine, settings.ChdbCacheDir, cfg.Threads)
	if err != nil {
		return nil, fmt.Errorf("open join engine: %w", err)
	}
	budget := ratelimit.New(settings.RUps)
	store, err := operations.OpenStore(ctx, cfg.Store, settings, budget)
	if err != nil {
		_ = eng.Close()
		return nil, err
	}
	progress := pipeline.Progress(pipeline.NopProgress{})
	if cfg.Progress {
		progress = logProgress{operation: "load"}
	}
	options := pipeline.Options{
		Key:         cfg.Key,
		BaseKey:     cfg.BaseKey,
		NodesPath:   nodes.URI,
		EdgesPath:   edges.URI,
		Store:       store,
		Engine:      eng,
		Budget:      budget,
		Dict:        dict,
		ZstdLevel:   cfg.ZstdLevel,
		NoRepack:    cfg.NoRepack,
		Concurrency: cfg.Concurrency,
		SampleSize:  cfg.SampleSize,
		Progress:    progress,
		Threads:     cfg.Threads,
	}
	if cfg.SampledAt != nil {
		options.Now = func() time.Time { return *cfg.SampledAt }
	}
	stats, loadErr := pipeline.Load(ctx, options)
	closeStoreErr := store.Close()
	closeEngineErr := eng.Close()
	if closeStoreErr != nil && loadErr == nil {
		loadErr = fmt.Errorf("close load target: %w", closeStoreErr)
	}
	if closeEngineErr != nil && loadErr == nil {
		loadErr = fmt.Errorf("close join engine: %w", closeEngineErr)
	}
	if stats == nil {
		return nil, loadErr
	}
	snapshot := stats.Snapshot()
	report := LoadReport{
		SchemaVersion: LoadSchema,
		Key:           cfg.Key,
		Store:         cfg.Store,
		CompletedAt:   time.Now().UTC(),
		Edges:         int64(snapshot.Edges),
		Created:       int64(snapshot.Created),
		Merged:        int64(snapshot.Merged),
		Skipped:       int64(snapshot.Skipped),
		Failed:        int64(snapshot.Failed),
		Filtered:      int64(snapshot.Filtered),
		Versions:      int64(snapshot.Versions),
		Sampled:       int64(snapshot.Sampled),
		Offered:       snapshot.Offered,
		Throttled:     int64(snapshot.Throttled),
		RawBytes:      snapshot.RawBytes,
		BlobBytes:     snapshot.BlobBytes,
		RU:            snapshot.RU,
		Waits:         snapshot.Waits,
	}
	if writeErr := writeJSONAtomic(cfg.ReportPath, report); writeErr != nil {
		loadErr = errors.Join(loadErr, writeErr)
	}
	if validateErr := report.ValidateComplete(); validateErr != nil {
		loadErr = errors.Join(loadErr, validateErr)
	}
	metadata["rows"] = report.Edges
	metadata["created"] = report.Created
	metadata["merged"] = report.Merged
	metadata["skipped"] = report.Skipped
	metadata["versions"] = report.Versions
	metadata["sampled"] = report.Sampled
	if loadErr != nil {
		return nil, loadErr
	}
	return metadata, nil
}

package worker

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/config"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/operations"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/pipeline"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

func runPush(ctx context.Context, cfg *PushConfig, settings config.Config) (map[string]any, error) {
	var manifest StageManifest
	if err := readJSONStrict(cfg.ManifestPath, &manifest); err != nil {
		return nil, err
	}
	if err := manifest.ValidateForPublication(); err != nil {
		return nil, err
	}
	if cfg.ExpectedKey != "" && manifest.Key != cfg.ExpectedKey {
		return nil, fmt.Errorf("staged release is %q, expected %q", manifest.Key, cfg.ExpectedKey)
	}
	if cfg.TargetStore == "cosmos" {
		expected := fmt.Sprintf("PUSH %s/%s/%s", settings.Cosmos.Endpoint, settings.Cosmos.Database, settings.Cosmos.Container)
		if cfg.DryRun {
			if cfg.Confirmation != "" && cfg.Confirmation != expected {
				return nil, fmt.Errorf("confirmation must be %q when provided", expected)
			}
		} else if cfg.Confirmation != expected {
			return nil, fmt.Errorf("refusing Cosmos publication without exact confirmation %q", expected)
		}
	}
	if sourcePath, ok := storeFilePath(manifest.Store); ok {
		if targetPath, ok := storeFilePath(cfg.TargetStore); ok {
			sourceAbs, _ := filepath.Abs(sourcePath)
			targetAbs, _ := filepath.Abs(targetPath)
			if sourceAbs == targetAbs {
				return nil, fmt.Errorf("publication source and target must differ: %s", sourceAbs)
			}
		}
	}

	budget := ratelimit.New(settings.RUps)
	if cfg.DryRun {
		budget = ratelimit.NewUnlimited()
	}
	source, err := operations.OpenStore(ctx, manifest.Store, settings, ratelimit.NewUnlimited())
	if err != nil {
		return nil, err
	}
	targetSpec := cfg.TargetStore
	if cfg.DryRun {
		targetSpec = "mem://"
	}
	target, err := operations.OpenStore(ctx, targetSpec, settings, budget)
	if err != nil {
		_ = source.Close()
		return nil, err
	}
	stats, pushErr := pipeline.Push(ctx, pipeline.PushOptions{
		Source: source, Target: target, Budget: budget, Concurrency: cfg.Concurrency,
	})
	if closeErr := source.Close(); closeErr != nil && pushErr == nil {
		pushErr = fmt.Errorf("close publication source: %w", closeErr)
	}
	if closeErr := target.Close(); closeErr != nil && pushErr == nil {
		pushErr = fmt.Errorf("close publication target: %w", closeErr)
	}
	report := PublicationReport{
		SchemaVersion: PublicationSchema,
		SourceDigest:  manifest.Digest,
		Key:           manifest.Key,
		TargetStore:   cfg.TargetStore,
		DryRun:        cfg.DryRun,
		CompletedAt:   time.Now().UTC(),
		Read:          stats.Read,
		Created:       stats.Created,
		Skipped:       stats.Skipped,
		Replaced:      stats.Replaced,
		Failed:        stats.Failed,
		BlobBytes:     stats.BlobBytes,
	}
	if outputPath, ok := storeFilePath(cfg.TargetStore); ok && !cfg.DryRun {
		report.OutputPath = outputPath
		if info, statErr := os.Stat(outputPath); statErr != nil || !info.Mode().IsRegular() {
			pushErr = errors.Join(pushErr, fmt.Errorf("publication output missing: %s", outputPath))
		}
	}
	if err := writeJSONAtomic(cfg.ReportPath, report); err != nil {
		pushErr = errors.Join(pushErr, err)
	}
	if pushErr != nil {
		return nil, pushErr
	}
	return map[string]any{
		"operation":      "push",
		"worker_version": WorkerVersion,
		"report_path":    cfg.ReportPath,
		"cache_key":      manifest.Digest,
		"cache_hit":      stats.Skipped == stats.Read && stats.Read > 0,
		"rows":           stats.Read,
		"created":        stats.Created,
		"skipped":        stats.Skipped,
		"replaced":       stats.Replaced,
		"blob_bytes":     stats.BlobBytes,
		"dry_run":        cfg.DryRun,
		"target_store":   cfg.TargetStore,
	}, nil
}

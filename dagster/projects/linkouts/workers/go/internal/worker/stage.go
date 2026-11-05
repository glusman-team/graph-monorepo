package worker

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/zeebo/blake3"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/config"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/engine"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/operations"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/pipeline"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

type logProgress struct {
	operation string
}

func (p logProgress) Report(s pipeline.Snapshot, final bool) {
	slog.Info("linkouts operation progress",
		"operation", p.operation,
		"key", s.Key,
		"final", final,
		"edges", s.Edges,
		"created", s.Created,
		"merged", s.Merged,
		"skipped", s.Skipped,
		"failed", s.Failed,
		"filtered", s.Filtered,
		"versions", s.Versions,
		"ru", s.RU,
		"sampled", s.Sampled,
	)
}

func runStage(ctx context.Context, cfg *StageConfig, settings config.Config) (map[string]any, error) {
	nodes, err := RefForFile(cfg.NodesPath, NDJSONMediaType)
	if err != nil {
		return nil, err
	}
	edges, err := RefForFile(cfg.EdgesPath, NDJSONMediaType)
	if err != nil {
		return nil, err
	}
	inputs := []ArtifactRef{nodes, edges}
	var filterInput *ArtifactRef
	if cfg.IDsPath != "" {
		ref, err := RefForFile(cfg.IDsPath, "text/plain")
		if err != nil {
			return nil, err
		}
		filterInput = &ref
	}
	var dictionary *ArtifactRef
	if cfg.DictionaryPath != "" {
		ref, err := RefForFile(cfg.DictionaryPath, DictionaryMediaType)
		if err != nil {
			return nil, err
		}
		dictionary = &ref
	}
	fingerprint := map[string]any{
		"worker_version":      WorkerVersion,
		"key":                 cfg.Key,
		"base_key":            cfg.BaseKey,
		"inputs":              inputs,
		"dictionary":          dictionary,
		"filter_input":        filterInput,
		"zstd_level":          cfg.ZstdLevel,
		"no_repack":           cfg.NoRepack,
		"sample_full_release": cfg.SampleFullRelease,
		"dry_run":             cfg.DryRun,
		"sample_size":         cfg.SampleSize,
		"sample_seed":         cfg.SampleSeed,
		"sampled_at":          cfg.SampledAt,
		"engine":              cfg.Engine,
	}
	digest, err := fingerprintDigest(fingerprint)
	if err != nil {
		return nil, err
	}
	dir, err := StagingDir(cfg.Workdir, digest)
	if err != nil {
		return nil, err
	}
	storePath := filepath.Join(dir, "documents.ndjson")
	manifestPath := filepath.Join(dir, "stage-manifest.json")
	filtered := cfg.DryRun || cfg.IDsPath != "" || cfg.SampleFullRelease || cfg.SampleSize == 0
	metadata := func(manifest StageManifest, cacheHit bool) map[string]any {
		return map[string]any{
			"operation":      "stage",
			"worker_version": WorkerVersion,
			"manifest_path":  manifestPath,
			"staging_digest": digest,
			"cache_key":      digest,
			"cache_hit":      cacheHit,
			"rows":           manifest.Edges,
			"created":        manifest.Created,
			"merged":         manifest.Merged,
			"skipped":        manifest.Skipped,
			"versions":       manifest.Versions,
			"sampled":        manifest.Sampled,
			"filtered":       manifest.Filtered,
			"dry_run":        cfg.DryRun,
		}
	}

	if cfg.Restart {
		if err := os.RemoveAll(dir); err != nil {
			return nil, fmt.Errorf("restart digest-scoped staging directory %s: %w", dir, err)
		}
	}
	resume := false
	if _, err := os.Stat(manifestPath); err == nil {
		var existing StageManifest
		if err := readJSONStrict(manifestPath, &existing); err != nil {
			return nil, err
		}
		if existing.Digest != digest || existing.Key != cfg.Key {
			return nil, fmt.Errorf("staging manifest identity mismatch at %s", manifestPath)
		}
		if existing.Complete {
			if existing.Filtered {
				if _, err := os.Stat(storePath); err != nil {
					return nil, fmt.Errorf("cached diagnostic store missing: %w", err)
				}
			} else if err := existing.ValidateForPublication(); err != nil {
				return nil, err
			}
			return metadata(existing, true), nil
		}
		resume = true
	} else if !os.IsNotExist(err) {
		return nil, fmt.Errorf("inspect staging manifest %s: %w", manifestPath, err)
	} else if _, statErr := os.Stat(dir); statErr == nil {
		return nil, fmt.Errorf("staging directory exists without a manifest: %s (restart this exact operation to rebuild)", dir)
	} else if !os.IsNotExist(statErr) {
		return nil, fmt.Errorf("inspect staging directory %s: %w", dir, statErr)
	}

	manifest := StageManifest{
		SchemaVersion:     StageSchema,
		Digest:            digest,
		Key:               cfg.Key,
		Store:             "file:" + storePath,
		Inputs:            inputs,
		Dictionary:        dictionary,
		FilterInput:       filterInput,
		ConfigFingerprint: fingerprint,
		Filtered:          filtered,
		Complete:          false,
		CreatedAt:         time.Now().UTC(),
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, fmt.Errorf("create staging directory: %w", err)
	}
	if err := writeJSONAtomic(manifestPath, manifest); err != nil {
		return nil, err
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
	if cfg.DryRun {
		budget = ratelimit.NewUnlimited()
	}
	store, err := operations.OpenStore(ctx, "file:"+storePath, settings, budget)
	if err != nil {
		_ = eng.Close()
		return nil, err
	}
	progress := pipeline.Progress(pipeline.NopProgress{})
	if cfg.Progress {
		progress = logProgress{operation: "stage"}
	}
	options := pipeline.Options{
		Key:               cfg.Key,
		BaseKey:           cfg.BaseKey,
		NodesPath:         nodes.URI,
		EdgesPath:         edges.URI,
		Store:             store,
		Engine:            eng,
		Budget:            budget,
		Dict:              dict,
		ZstdLevel:         cfg.ZstdLevel,
		DryRun:            cfg.DryRun,
		NoRepack:          cfg.NoRepack || resume,
		IDsPath:           cfg.IDsPath,
		SampleFullRelease: cfg.SampleFullRelease,
		Concurrency:       cfg.Concurrency,
		SampleSize:        cfg.SampleSize,
		DisablePool:       cfg.SampleSize == 0,
		SampleSeed:        cfg.SampleSeed,
		Progress:          progress,
		Threads:           cfg.Threads,
	}
	if cfg.SampledAt != nil {
		options.Now = func() time.Time { return *cfg.SampledAt }
	}
	stats, loadErr := pipeline.Load(ctx, options)
	if closeErr := store.Close(); closeErr != nil && loadErr == nil {
		loadErr = fmt.Errorf("close staged store: %w", closeErr)
	}
	if closeErr := eng.Close(); closeErr != nil && loadErr == nil {
		loadErr = fmt.Errorf("close join engine: %w", closeErr)
	}
	if stats != nil {
		snapshot := stats.Snapshot()
		manifest.Edges = snapshot.Edges
		manifest.Created = snapshot.Created
		manifest.Merged = snapshot.Merged
		manifest.Skipped = snapshot.Skipped
		manifest.Failed = snapshot.Failed
		manifest.Versions = snapshot.Versions
		manifest.Sampled = snapshot.Sampled
		manifest.Offered = snapshot.Offered
	}
	manifest.CreatedAt = time.Now().UTC()
	manifest.Complete = loadErr == nil && manifest.Failed == 0
	if err := writeJSONAtomic(manifestPath, manifest); err != nil {
		loadErr = errors.Join(loadErr, err)
	}
	if loadErr != nil {
		return nil, fmt.Errorf("stage release %s (resume with the same config): %w", cfg.Key, loadErr)
	}
	return metadata(manifest, false), nil
}

func fingerprintDigest(value any) (string, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return "", fmt.Errorf("encode staging fingerprint: %w", err)
	}
	digest := blake3.Sum256(raw)
	return "b3:" + hex.EncodeToString(digest[:]), nil
}

func storeFilePath(spec string) (string, bool) {
	if !strings.HasPrefix(spec, "file:") || len(spec) == len("file:") {
		return "", false
	}
	return strings.TrimPrefix(spec, "file:"), true
}

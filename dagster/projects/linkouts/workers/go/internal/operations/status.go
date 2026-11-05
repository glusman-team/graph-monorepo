package operations

import (
	"context"
	"errors"
	"fmt"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/pipeline"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/version"
)

type PoolStatus struct {
	Found        bool    `json:"found"`
	StoredBytes  int     `json:"stored_bytes"`
	IDs          int     `json:"ids"`
	IndexSampled int     `json:"index_sampled"`
	EstimatedRU  float64 `json:"estimated_ru"`
}

type ReleaseStatus struct {
	Label     string      `json:"label"`
	Edges     int         `json:"edges"`
	Sampled   int         `json:"sampled"`
	SampledAt string      `json:"sampled_at"`
	Pool      *PoolStatus `json:"pool,omitempty"`
}

type KnowledgeGraphStatus struct {
	Slug     string          `json:"slug"`
	Infores  string          `json:"infores"`
	Releases []ReleaseStatus `json:"releases"`
}

type StatusReport struct {
	Store             string                 `json:"store"`
	Stats             cosmos.StoreStats      `json:"stats"`
	PoolIndexPresent  bool                   `json:"pool_index_present"`
	PoolIndexSchema   string                 `json:"pool_index_schema"`
	KnowledgeGraphs   []KnowledgeGraphStatus `json:"knowledge_graphs"`
	EdgeDocuments     int                    `json:"edge_documents"`
	ReservedDocuments int                    `json:"reserved_documents"`
	RU                float64                `json:"ru"`
}

func Status(
	ctx context.Context,
	store cosmos.Store,
	dict []byte,
	budget ratelimit.Budget,
	checkPools bool,
) (StatusReport, error) {
	stats, err := store.Stats(ctx)
	if err != nil {
		return StatusReport{}, fmt.Errorf("read container metadata: %w", err)
	}
	report := StatusReport{Store: store.Name(), Stats: stats, PoolIndexSchema: "absent"}
	index, err := pipeline.ReadPoolIndex(ctx, store, dict)
	switch {
	case errors.Is(err, cosmos.ErrNotFound):
		report.RU = budget.Consumed()
		return report, nil
	case err != nil:
		var schemaErr *pipeline.SchemaError
		if errors.As(err, &schemaErr) {
			report.PoolIndexSchema = "pre-per-release"
			report.RU = budget.Consumed()
			return report, nil
		}
		return StatusReport{}, err
	}
	report.PoolIndexPresent = true
	report.PoolIndexSchema = codec.SchemaVersion
	reserved := 1
	for _, slug := range pipeline.Slugs(index) {
		kg := KnowledgeGraphStatus{Slug: slug, Infores: version.InforesPrefix + slug}
		for _, release := range pipeline.Releases(index, slug) {
			entry := ReleaseStatus{
				Label:     release.Label,
				Edges:     int(release.Pool.Edges),
				Sampled:   release.Pool.Sampled,
				SampledAt: release.Pool.SampledAt,
			}
			reserved++
			if checkPools {
				pool, err := inspectPool(ctx, store, dict, slug, release.Label, release.Pool.Sampled)
				if err != nil {
					return StatusReport{}, err
				}
				entry.Pool = &pool
			}
			kg.Releases = append(kg.Releases, entry)
		}
		report.KnowledgeGraphs = append(report.KnowledgeGraphs, kg)
	}
	report.ReservedDocuments = reserved
	if stats.Items >= 0 {
		report.EdgeDocuments = stats.Items - reserved
	}
	report.RU = budget.Consumed()
	if err := report.ValidateIntegrity(); err != nil {
		return StatusReport{}, err
	}
	return report, nil
}

func (r StatusReport) ValidateIntegrity() error {
	if r.PoolIndexPresent && r.PoolIndexSchema != codec.SchemaVersion {
		return fmt.Errorf("unsupported pool index schema %q", r.PoolIndexSchema)
	}
	if r.Stats.Items >= 0 && r.EdgeDocuments < 0 {
		return fmt.Errorf("store has fewer documents than the reserved pool documents")
	}
	for _, kg := range r.KnowledgeGraphs {
		for _, release := range kg.Releases {
			if release.Pool == nil {
				continue
			}
			if !release.Pool.Found {
				return fmt.Errorf("pool document missing for %s %s", kg.Slug, release.Label)
			}
			if release.Pool.IDs != release.Sampled {
				return fmt.Errorf(
					"pool document for %s %s holds %d ids; index says %d",
					kg.Slug, release.Label, release.Pool.IDs, release.Sampled,
				)
			}
			if release.Pool.IDs > release.Edges {
				return fmt.Errorf("pool document for %s %s has more ids than edges", kg.Slug, release.Label)
			}
		}
	}
	return nil
}

func inspectPool(
	ctx context.Context,
	store cosmos.Store,
	dict []byte,
	slug, label string,
	want int,
) (PoolStatus, error) {
	id := cosmos.PoolDocID(slug, label)
	doc, err := store.Read(ctx, id)
	if errors.Is(err, cosmos.ErrNotFound) {
		return PoolStatus{Found: false, IndexSampled: want}, nil
	}
	if err != nil {
		return PoolStatus{}, fmt.Errorf("read pool %s: %w", id, err)
	}
	stored, err := pipeline.DictionaryFor(doc.DictID, dict)
	if err != nil {
		return PoolStatus{}, fmt.Errorf("%s: %w", id, err)
	}
	var pool codec.Pool
	if err := codec.DecodeJSON(doc.Blob, stored, &pool); err != nil {
		return PoolStatus{}, fmt.Errorf("decode %s: %w", id, err)
	}
	return PoolStatus{
		Found:        true,
		StoredBytes:  len(doc.Blob),
		IDs:          len(pool.IDs),
		IndexSampled: want,
		EstimatedRU:  float64(len(doc.Blob)) / 1024,
	}, nil
}

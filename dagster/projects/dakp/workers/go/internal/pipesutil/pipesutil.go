// Package pipesutil wraps the Dagster Pipes protocol for DAKP-pattern
// extraction workers: session open/close, structured logging, materialization
// reporting, and BLAKE3 content-addressed cache metadata.
//
// Dependency pin: github.com/hupe1980/dagster-pipes-go (protocol version 0.1).
// Swap point: if the upstream lib stalls, reimplement the protocol here
// (context file in $DAGSTER_PIPES_CONTEXT, JSON messages to the file in
// $DAGSTER_PIPES_MESSAGES) without touching callers.
package pipesutil

import (
	"encoding/hex"

	dagsterpipes "github.com/hupe1980/dagster-pipes-go"
	"github.com/zeebo/blake3"
)

// WorkerVersion participates in every cache key. Bump when extraction
// behavior changes so content-addressed artifacts invalidate correctly.
// It tracks the bytes written to the output artifact only: reporting or
// logging changes do not invalidate anything, so they do not bump it.
const WorkerVersion = "0.1.0"

// CacheInfo describes a content-addressed artifact decision.
type CacheInfo struct {
	Key          string // BLAKE3(inputs || worker version), hex
	ArtifactPath string // where the output artifact lives
	Hit          bool   // true when the artifact was already present
	InputBytes   int64  // size of the input consumed
}

// HashInput derives a cache key from input bytes and the worker version.
// Keys are content-based, so they survive re-runs and redeploys.
func HashInput(input []byte) string {
	h := blake3.New()
	_, _ = h.Write([]byte(WorkerVersion)) // hash.Hash writes cannot fail
	_, _ = h.Write(input)
	return hex.EncodeToString(h.Sum(nil))
}

// ReportMaterialization reports the worker's asset materialization with the
// standard cache metadata trio: cache_key, cache_hit, rows.
//
// The asset key is intentionally not passed: dagster-pipes-go resolves it
// from the pipes context, which keeps the reported key equal to the asset the
// caller is materializing (a hardcoded key is silently rejected by the
// protocol and the cache metadata never lands).
func ReportMaterialization(
	ctx *dagsterpipes.Context[map[string]any],
	dataVersion string,
	cache CacheInfo,
	rows int64,
) error {
	return ctx.ReportAssetMaterialization(&dagsterpipes.AssetMaterialization{
		DataVersion: dataVersion,
		Metadata: map[string]any{
			"cache_key":      cache.Key,
			"cache_hit":      cache.Hit,
			"rows":           rows,
			"worker_version": WorkerVersion,
		},
	})
}

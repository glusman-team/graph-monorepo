package operations

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/pipeline"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/version"
)

type VersionSummary struct {
	Key           string `json:"key"`
	Kind          string `json:"kind"`
	Fields        int    `json:"fields"`
	ResolvedBytes int    `json:"resolved_bytes"`
}

type GetOptions struct {
	EdgeID     string
	VersionKey string
	List       bool
	Raw        bool
}

type GetResult struct {
	ID           string           `json:"id"`
	StoredBytes  int              `json:"stored_bytes"`
	DictionaryID uint32           `json:"dictionary_id"`
	VersionKey   string           `json:"version_key,omitempty"`
	RawFrame     string           `json:"raw_frame,omitempty"`
	Document     json.RawMessage  `json:"document,omitempty"`
	Versions     []VersionSummary `json:"versions,omitempty"`
}

func Get(
	ctx context.Context,
	store cosmos.Store,
	dict []byte,
	opts GetOptions,
) (GetResult, error) {
	doc, err := store.Read(ctx, opts.EdgeID)
	if err != nil {
		return GetResult{}, err
	}
	result := GetResult{ID: opts.EdgeID, StoredBytes: len(doc.Blob), DictionaryID: doc.DictID}
	stored, err := pipeline.DictionaryFor(doc.DictID, dict)
	if err != nil {
		return GetResult{}, err
	}
	blob, err := codec.DecodeBlob(doc.Blob, stored)
	if err != nil {
		return GetResult{}, err
	}
	if opts.List {
		result.Versions, err = versionSummaries(blob)
		return result, err
	}
	key := opts.VersionKey
	if key == "" {
		key = version.Newest(blob.VersionKeys())
	}
	resolved, err := blob.Resolve(key)
	if err != nil {
		return GetResult{}, err
	}
	result.VersionKey = key
	if opts.Raw {
		result.RawFrame = doc.Blob
		return result, nil
	}
	raw, err := codec.Marshal(resolved)
	if err != nil {
		return GetResult{}, err
	}
	result.Document = json.RawMessage(raw)
	return result, nil
}

func versionSummaries(blob *codec.Blob) ([]VersionSummary, error) {
	keys := blob.VersionKeys()
	sort.Slice(keys, func(i, j int) bool { return version.CompareKeys(keys[i], keys[j]) > 0 })
	out := make([]VersionSummary, 0, len(keys))
	for _, key := range keys {
		entry := blob.Versions[key]
		kind := "full"
		if !entry.IsFull() {
			kind = fmt.Sprintf("delta <- %s", entry.Base)
		}
		doc, err := blob.Resolve(key)
		if err != nil {
			return nil, err
		}
		raw, err := codec.Marshal(doc)
		if err != nil {
			return nil, err
		}
		out = append(out, VersionSummary{
			Key: key, Kind: kind, Fields: len(doc), ResolvedBytes: len(raw),
		})
	}
	return out, nil
}

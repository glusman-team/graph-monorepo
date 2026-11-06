// Package worker implements the argument-free Dagster Pipes worker and its wire contracts.
package worker

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"time"

	"github.com/zeebo/blake3"
)

const (
	WorkerVersion       = "0.1.0"
	StageSchema         = "linkouts.stage/1"
	PublicationSchema   = "linkouts.publication/1"
	ReportSchema        = "linkouts.report/1"
	StageMediaType      = "application/vnd.linkouts.stage-manifest+json"
	ReportMediaType     = "application/vnd.linkouts.report+json"
	NDJSONMediaType     = "application/x-ndjson"
	DictionaryMediaType = "application/zstd-dictionary"
)

var digestPattern = regexp.MustCompile(`^b3:[0-9a-f]{64}$`)

type ArtifactRef struct {
	URI               string `json:"uri"`
	Blake3            string `json:"blake3"`
	MediaType         string `json:"media_type"`
	Rows              *int64 `json:"rows,omitempty"`
	SchemaFingerprint string `json:"schema_fingerprint,omitempty"`
	Manifest          string `json:"manifest,omitempty"`
}

type StageConfig struct {
	Operation         string     `json:"operation"`
	Key               string     `json:"key"`
	NodesPath         string     `json:"nodes_path"`
	EdgesPath         string     `json:"edges_path"`
	Workdir           string     `json:"workdir"`
	BaseKey           string     `json:"base_key"`
	DictionaryPath    string     `json:"dictionary_path"`
	ZstdLevel         int        `json:"zstd_level"`
	NoRepack          bool       `json:"no_repack"`
	Restart           bool       `json:"restart"`
	IDsPath           string     `json:"ids_path"`
	SampleFullRelease bool       `json:"sample_full_release"`
	DryRun            bool       `json:"dry_run"`
	SampleSize        int        `json:"sample_size"`
	SampleSeed        int64      `json:"sample_seed"`
	SampledAt         *time.Time `json:"sampled_at"`
	Concurrency       int        `json:"concurrency"`
	Threads           int        `json:"threads"`
	Engine            string     `json:"engine"`
	Progress          bool       `json:"progress"`
}

type PushConfig struct {
	Operation    string `json:"operation"`
	ManifestPath string `json:"manifest_path"`
	ReportPath   string `json:"report_path"`
	ExpectedKey  string `json:"expected_key"`
	TargetStore  string `json:"target_store"`
	DryRun       bool   `json:"dry_run"`
	Concurrency  int    `json:"concurrency"`
	Confirmation string `json:"confirmation"`
}

type TrainDictConfig struct {
	Operation  string `json:"operation"`
	NodesPath  string `json:"nodes_path"`
	EdgesPath  string `json:"edges_path"`
	OutputPath string `json:"output_path"`
	ReportPath string `json:"report_path"`
	Samples    int    `json:"samples"`
	Threads    int    `json:"threads"`
	Engine     string `json:"engine"`
}

type StatusConfig struct {
	Operation      string `json:"operation"`
	Store          string `json:"store"`
	OutputPath     string `json:"output_path"`
	DictionaryPath string `json:"dictionary_path"`
	CheckPools     bool   `json:"check_pools"`
}

type ProbeConfig struct {
	Operation      string   `json:"operation"`
	Store          string   `json:"store"`
	DictionaryPath string   `json:"dictionary_path"`
	OutputPath     string   `json:"output_path"`
	Count          int      `json:"count"`
	Seed           *int64   `json:"seed"`
	MaxMeanRU      *float64 `json:"max_mean_ru"`
	MaxWorstRU     *float64 `json:"max_worst_ru"`
}

type GetConfig struct {
	Operation      string `json:"operation"`
	Store          string `json:"store"`
	DictionaryPath string `json:"dictionary_path"`
	EdgeID         string `json:"edge_id"`
	OutputPath     string `json:"output_path"`
	VersionKey     string `json:"version_key"`
	ListVersions   bool   `json:"list_versions"`
	Raw            bool   `json:"raw"`
}

type RigConfig struct {
	Operation  string `json:"operation"`
	EdgesPath  string `json:"edges_path"`
	OutputPath string `json:"output_path"`
	Limit      int    `json:"limit"`
	Predicate  string `json:"predicate"`
	EmitConfig bool   `json:"emit_config"`
}

type InitConfig struct {
	Operation  string `json:"operation"`
	Store      string `json:"store"`
	OutputPath string `json:"output_path"`
}

type PurgeConfig struct {
	Operation      string `json:"operation"`
	Store          string `json:"store"`
	DictionaryPath string `json:"dictionary_path"`
	OutputPath     string `json:"output_path"`
	Scope          string `json:"scope"`
	KG             string `json:"kg"`
	Key            string `json:"key"`
	DryRun         bool   `json:"dry_run"`
	Confirmation   string `json:"confirmation"`
}

type Request struct {
	Operation string
	Config    any
}

type StageManifest struct {
	SchemaVersion     string         `json:"schema_version"`
	Digest            string         `json:"digest"`
	Key               string         `json:"key"`
	Store             string         `json:"store"`
	Inputs            []ArtifactRef  `json:"inputs"`
	Dictionary        *ArtifactRef   `json:"dictionary,omitempty"`
	FilterInput       *ArtifactRef   `json:"filter_input,omitempty"`
	ConfigFingerprint map[string]any `json:"config_fingerprint"`
	Filtered          bool           `json:"filtered"`
	Complete          bool           `json:"complete"`
	CreatedAt         time.Time      `json:"created_at"`
	Edges             int            `json:"edges"`
	Created           int            `json:"created"`
	Merged            int            `json:"merged"`
	Skipped           int            `json:"skipped"`
	Failed            int            `json:"failed"`
	Versions          int            `json:"versions"`
	Sampled           int            `json:"sampled"`
	Offered           int64          `json:"offered"`
}

func (m StageManifest) ValidateForPublication() error {
	if m.SchemaVersion != StageSchema {
		return fmt.Errorf("unsupported staging schema %q", m.SchemaVersion)
	}
	if !digestPattern.MatchString(m.Digest) {
		return fmt.Errorf("invalid staging digest %q", m.Digest)
	}
	if !m.Complete {
		return fmt.Errorf("staged release is incomplete")
	}
	if m.Filtered {
		return fmt.Errorf("filtered or diagnostic staging cannot be published")
	}
	if m.Failed != 0 {
		return fmt.Errorf("staged release has %d failed edges", m.Failed)
	}
	if m.Edges == 0 {
		return fmt.Errorf("staged release contains no edges")
	}
	if m.Sampled == 0 {
		return fmt.Errorf("staged release has no random-pool sample")
	}
	if len(m.Store) <= len("file:") || m.Store[:len("file:")] != "file:" {
		return fmt.Errorf("staged manifest requires file:<path>, got %q", m.Store)
	}
	info, err := os.Stat(m.Store[len("file:"):])
	if err != nil {
		return fmt.Errorf("staged store missing: %w", err)
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("staged store is not a regular file: %s", m.Store)
	}
	return nil
}

type PublicationReport struct {
	SchemaVersion string    `json:"schema_version"`
	SourceDigest  string    `json:"source_digest"`
	Key           string    `json:"key"`
	TargetStore   string    `json:"target_store"`
	DryRun        bool      `json:"dry_run"`
	CompletedAt   time.Time `json:"completed_at"`
	OutputPath    string    `json:"output_path,omitempty"`
	Read          int       `json:"read"`
	Created       int       `json:"created"`
	Skipped       int       `json:"skipped"`
	Replaced      int       `json:"replaced"`
	Failed        int       `json:"failed"`
	BlobBytes     int64     `json:"blob_bytes"`
}

type OperationReport struct {
	SchemaVersion string         `json:"schema_version"`
	Operation     string         `json:"operation"`
	Status        string         `json:"status"`
	CreatedAt     time.Time      `json:"created_at"`
	Details       map[string]any `json:"details"`
}

func RefForFile(path, mediaType string) (ArtifactRef, error) {
	if path == "" {
		return ArtifactRef{}, fmt.Errorf("artifact path is required")
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return ArtifactRef{}, fmt.Errorf("resolve %s: %w", path, err)
	}
	fh, err := os.Open(absolute)
	if err != nil {
		return ArtifactRef{}, fmt.Errorf("open artifact %s: %w", absolute, err)
	}
	h := blake3.New()
	_, copyErr := io.Copy(h, fh)
	closeErr := fh.Close()
	if copyErr != nil {
		return ArtifactRef{}, fmt.Errorf("hash %s: %w", absolute, copyErr)
	}
	if closeErr != nil {
		return ArtifactRef{}, fmt.Errorf("close %s after hashing: %w", absolute, closeErr)
	}
	return ArtifactRef{
		URI: absolute, Blake3: "b3:" + hex.EncodeToString(h.Sum(nil)), MediaType: mediaType,
	}, nil
}

func StagingDir(workdir, digest string) (string, error) {
	if !digestPattern.MatchString(digest) {
		return "", fmt.Errorf("unsafe staging digest %q", digest)
	}
	return filepath.Join(workdir, "stores", digest[3:]), nil
}

func writeJSONAtomic(path string, value any) error {
	if path == "" {
		return fmt.Errorf("JSON output path is required")
	}
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return fmt.Errorf("encode %s: %w", path, err)
	}
	raw = append(raw, '\n')
	return writeBytesAtomic(path, raw)
}

func writeBytesAtomic(path string, raw []byte) error {
	if path == "" {
		return fmt.Errorf("output path is required")
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("create output directory %s: %w", dir, err)
	}
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".*")
	if err != nil {
		return fmt.Errorf("create temporary output for %s: %w", path, err)
	}
	tmpPath := tmp.Name()
	defer func() { _ = os.Remove(tmpPath) }()
	if _, err := tmp.Write(raw); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("write %s: %w", tmpPath, err)
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("sync %s: %w", tmpPath, err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close %s: %w", tmpPath, err)
	}
	if err := os.Rename(tmpPath, path); err != nil {
		return fmt.Errorf("publish %s: %w", path, err)
	}
	return nil
}

func readJSONStrict(path string, target any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read %s: %w", path, err)
	}
	if err := decodeStrict(raw, target); err != nil {
		return fmt.Errorf("decode %s: %w", path, err)
	}
	return nil
}

func decodeStrict(raw []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return fmt.Errorf("multiple JSON values are not allowed")
		}
		return err
	}
	return nil
}

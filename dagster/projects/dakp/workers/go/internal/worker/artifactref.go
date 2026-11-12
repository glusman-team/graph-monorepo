package worker

import (
	"encoding/json"
	"fmt"
	"os"
)

// ArtifactRef is the Go mirror of Python io/contracts.ArtifactRef. Paths are strings (ref
// payloads carry JSON, not Path objects). Nullable fields (rows / schema_fingerprint /
// manifest) are pointers so absent/null round-trips correctly. The JSON tags match the
// snake_case keys the Python side produces/consumes, so refs flow unchanged in both directions.
type ArtifactRef struct {
	URI               string  `json:"uri"`
	Blake3            string  `json:"blake3"`
	MediaType         string  `json:"media_type"`
	Rows              *int64  `json:"rows"`
	SchemaFingerprint *string `json:"schema_fingerprint"`
	Manifest          *string `json:"manifest"`
}

// Config is the per-run config the worker reads from the stage's JSON config (pipes extras
// under Dagster). It mirrors the subset of the Python TaskContext/Profile the extract
// tasks need: where to write interim/manifest artifacts (Workdir) and the run knobs.
type Config struct {
	Workdir        string `json:"workdir"`
	Profile        string `json:"profile"`
	FixtureRoot    string `json:"fixture_root"`
	QuarterLimit   int    `json:"quarter_limit"`
	ReleaseLimit   int    `json:"release_limit"`
	Force          bool   `json:"force"`
	Threads        int    `json:"threads"`
	MemoryBudgetGB int    `json:"memory_budget_gb"`
}

// decodeRefsFile reads the refs JSON file of the single-file handoff (the exact shape the Python
// refs_to_dicts producer wrote) and decodes it into typed ArtifactRefs.
func decodeRefsFile(path string) ([]ArtifactRef, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read refs file %s: %w", path, err)
	}
	var refs []ArtifactRef
	if err := json.Unmarshal(data, &refs); err != nil {
		return nil, fmt.Errorf("decode refs file %s: %w", path, err)
	}
	return refs, nil
}

// ReadRefsFile reads a refs JSON file (the exact shape the Python refs_to_dicts
// producer writes, and what WriteRefsFile below writes) into typed ArtifactRefs.
// This is the Python<->Go stage-boundary contract under Dagster Pipes: the python
// asset writes input refs to a file whose path arrives via pipes extras, and the
// worker writes its output refs to a second file the asset reads back.
func ReadRefsFile(path string) ([]ArtifactRef, error) {
	return decodeRefsFile(path)
}

// WriteRefsFile writes refs as a JSON array to path (atomic enough for the stage
// boundary: the file is written once, after the extraction fully succeeded, and a
// nil slice marshals as [] rather than null). The Python side reads it with
// io.refs.refs_from_file.
func WriteRefsFile(path string, refs []ArtifactRef) error {
	if refs == nil {
		refs = []ArtifactRef{}
	}
	data, err := json.Marshal(refs)
	if err != nil {
		return fmt.Errorf("encode refs file %s: %w", path, err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return fmt.Errorf("write refs file %s: %w", path, err)
	}
	return nil
}

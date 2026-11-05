package operations

import (
	"strings"
	"testing"
)

func TestStatusIntegrityRejectsMissingAndMismatchedPools(t *testing.T) {
	base := StatusReport{
		PoolIndexPresent: true,
		PoolIndexSchema:  "edgelinkouts.blob/1",
		KnowledgeGraphs: []KnowledgeGraphStatus{{
			Slug:     "test-kp",
			Releases: []ReleaseStatus{{Label: "1.0.0", Edges: 4, Sampled: 2, Pool: &PoolStatus{Found: true, IDs: 2}}},
		}},
	}
	if err := base.ValidateIntegrity(); err != nil {
		t.Fatalf("valid status: %v", err)
	}
	missing := base
	if err := missing.ValidateIntegrity(); err != nil {
		t.Fatalf("deep-copy-free valid status: %v", err)
	}
	missing.KnowledgeGraphs = append([]KnowledgeGraphStatus(nil), base.KnowledgeGraphs...)
	missing.KnowledgeGraphs[0].Releases = append([]ReleaseStatus(nil), base.KnowledgeGraphs[0].Releases...)
	missing.KnowledgeGraphs[0].Releases[0].Pool = &PoolStatus{Found: false}
	if err := missing.ValidateIntegrity(); err == nil || !strings.Contains(err.Error(), "missing") {
		t.Fatalf("missing pool: %v", err)
	}
	mismatch := base
	mismatch.KnowledgeGraphs = append([]KnowledgeGraphStatus(nil), base.KnowledgeGraphs...)
	mismatch.KnowledgeGraphs[0].Releases = append([]ReleaseStatus(nil), base.KnowledgeGraphs[0].Releases...)
	mismatch.KnowledgeGraphs[0].Releases[0].Pool = &PoolStatus{Found: true, IDs: 1}
	if err := mismatch.ValidateIntegrity(); err == nil || !strings.Contains(err.Error(), "index says") {
		t.Fatalf("mismatched pool: %v", err)
	}
}

func TestStatusIntegrityRejectsLegacyIndex(t *testing.T) {
	report := StatusReport{PoolIndexPresent: true, PoolIndexSchema: "pre-per-release"}
	if err := report.ValidateIntegrity(); err == nil {
		t.Fatal("legacy pool index accepted as current")
	}
}

func TestProbeIntegrityRejectsMissingPools(t *testing.T) {
	if err := (ProbeReport{MissingPools: 1}).ValidateIntegrity(); err == nil {
		t.Fatal("missing pool accepted by probe")
	}
}

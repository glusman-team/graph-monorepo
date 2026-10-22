package worker

import "context"

// TaskOperation maps a stage name to its task-level operation-index key (the
// already-done skip). The cmd/dakp-extract pipes worker uses this for its
// pre-dispatch cache probe.
func TaskOperation(stage string) string {
	switch stage {
	case "dailymed":
		return dailymedTaskOperation
	case "faers":
		return faersTaskOperation
	case "drugsfda":
		return drugsfdaTaskOperation
	default:
		return ""
	}
}

// ExtractFunc is one stage's extraction entry point.
type ExtractFunc func(ctx context.Context, cfg Config, inputs []ArtifactRef) ([]ArtifactRef, error)

// ExtractFor resolves a stage name to its extraction entry point (nil when
// unknown; callers gate the names before calling).
func ExtractFor(stage string) ExtractFunc {
	switch stage {
	case "dailymed":
		return ExtractDailyMed
	case "faers":
		return ExtractFAERS
	case "drugsfda":
		return ExtractDrugsFDA
	default:
		return nil
	}
}

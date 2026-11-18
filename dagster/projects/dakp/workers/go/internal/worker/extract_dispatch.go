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
	case "faers_merge":
		return faersMergeOperation
	case "faers_parse":
		return faersParseTaskOperation
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
	case "faers_parse":
		return func(ctx context.Context, cfg Config, inputs []ArtifactRef) ([]ArtifactRef, error) {
			shards, err := ParseFAERSQuarters(ctx, cfg, inputs)
			if err != nil {
				return nil, err
			}
			return append(shards, inputs...), nil
		}
	case "faers_merge":
		return MergeFAERSQuarters
	case "drugsfda":
		return ExtractDrugsFDA
	default:
		return nil
	}
}

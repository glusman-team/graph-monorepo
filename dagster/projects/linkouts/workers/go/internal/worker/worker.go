package worker

import (
	"context"
	"fmt"
	"os"
	"strings"

	dagsterpipes "github.com/hupe1980/dagster-pipes-go"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/config"
)

func Run(
	ctx context.Context,
	pipes *dagsterpipes.Context[map[string]any],
	getenv config.Getenv,
) error {
	getenv = getenvOrDefault(getenv)
	request, err := ParseRequest(pipes.Extras())
	if err != nil {
		return err
	}
	if request.Operation == "purge" && !truthy(getenv("LINKOUTS_ENABLE_DESTRUCTIVE_JOBS")) {
		return fmt.Errorf("purge is disabled; set LINKOUTS_ENABLE_DESTRUCTIVE_JOBS=1 explicitly")
	}
	settings, err := config.Load(getenv)
	if err != nil {
		return err
	}
	var metadata map[string]any
	switch cfg := request.Config.(type) {
	case *StageConfig:
		metadata, err = runStage(ctx, cfg, settings)
	case *LoadConfig:
		metadata, err = runLoad(ctx, cfg, settings)
	case *PushConfig:
		metadata, err = runPush(ctx, cfg, settings)
	case *TrainDictConfig:
		metadata, err = runTrainDict(ctx, cfg, settings)
	case *StatusConfig:
		metadata, err = runStatus(ctx, cfg, settings)
	case *ProbeConfig:
		metadata, err = runProbe(ctx, cfg, settings)
	case *GetConfig:
		metadata, err = runGet(ctx, cfg, settings)
	case *RigConfig:
		metadata, err = runRig(cfg)
	case *InitConfig:
		metadata, err = runInit(ctx, cfg, settings)
	case *PurgeConfig:
		metadata, err = runPurge(ctx, cfg, settings)
	default:
		err = fmt.Errorf("unhandled operation config %T", request.Config)
	}
	if err != nil {
		return err
	}
	dataVersion, err := operationDataVersion(request.Operation, metadata)
	if err != nil {
		return err
	}
	if _, ok := metadata["cache_key"]; !ok {
		metadata["cache_key"] = dataVersion
	}
	if _, ok := metadata["cache_hit"]; !ok {
		metadata["cache_hit"] = false
	}
	metadata["worker_version"] = WorkerVersion
	if err := pipes.ReportAssetMaterialization(&dagsterpipes.AssetMaterialization{
		DataVersion: dataVersion,
		Metadata:    metadata,
	}); err != nil {
		return fmt.Errorf("report %s materialization: %w", request.Operation, err)
	}
	if err := pipes.LogInfo(fmt.Sprintf("linkouts %s completed", request.Operation)); err != nil {
		return fmt.Errorf("report %s log: %w", request.Operation, err)
	}
	return nil
}

func operationDataVersion(operation string, metadata map[string]any) (string, error) {
	identity := map[string]any{
		"operation":      operation,
		"worker_version": WorkerVersion,
		"staging_digest": metadata["staging_digest"],
	}
	if path, ok := metadata["report_path"].(string); ok && path != "" {
		ref, err := RefForFile(path, ReportMediaType)
		if err != nil {
			return "", err
		}
		identity["report"] = ref.Blake3
	}
	if path, ok := metadata["manifest_path"].(string); ok && path != "" {
		identity["manifest_path"] = path
	}
	return fingerprintDigest(identity)
}

func truthy(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func getenvOrDefault(getenv config.Getenv) config.Getenv {
	if getenv == nil {
		return os.Getenv
	}
	return getenv
}

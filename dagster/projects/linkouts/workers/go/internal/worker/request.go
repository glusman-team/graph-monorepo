package worker

import (
	"fmt"
	"strings"
)

func ParseRequest(extras map[string]any) (Request, error) {
	if len(extras) == 0 {
		return Request{}, fmt.Errorf("no pipes extras: operation and config_json are required")
	}
	for key := range extras {
		if key != "operation" && key != "config_json" {
			return Request{}, fmt.Errorf("unknown pipes extra %q", key)
		}
	}
	operation, ok := extras["operation"].(string)
	if !ok || operation == "" {
		return Request{}, fmt.Errorf("extras.operation is required")
	}
	raw, ok := extras["config_json"].(string)
	if !ok || raw == "" {
		return Request{}, fmt.Errorf("extras.config_json is required")
	}
	var config any
	var expected string
	switch operation {
	case "stage":
		config = &StageConfig{}
	case "push":
		config = &PushConfig{}
	case "train_dict":
		config = &TrainDictConfig{}
	case "status":
		config = &StatusConfig{}
	case "probe":
		config = &ProbeConfig{}
	case "get":
		config = &GetConfig{}
	case "rig":
		config = &RigConfig{}
	case "init":
		config = &InitConfig{}
	case "purge":
		config = &PurgeConfig{}
	default:
		return Request{}, fmt.Errorf("unknown worker operation %q", operation)
	}
	if err := decodeStrict([]byte(raw), config); err != nil {
		return Request{}, fmt.Errorf("decode %s config: %w", operation, err)
	}
	switch cfg := config.(type) {
	case *StageConfig:
		expected = cfg.Operation
	case *PushConfig:
		expected = cfg.Operation
	case *TrainDictConfig:
		expected = cfg.Operation
	case *StatusConfig:
		expected = cfg.Operation
	case *ProbeConfig:
		expected = cfg.Operation
	case *GetConfig:
		expected = cfg.Operation
	case *RigConfig:
		expected = cfg.Operation
	case *InitConfig:
		expected = cfg.Operation
	case *PurgeConfig:
		expected = cfg.Operation
	}
	if expected != operation {
		return Request{}, fmt.Errorf("envelope operation %q does not match config operation %q", operation, expected)
	}
	request := Request{Operation: operation, Config: config}
	if err := request.Validate(); err != nil {
		return Request{}, fmt.Errorf("invalid %s config: %w", operation, err)
	}
	return request, nil
}

func (r Request) Validate() error {
	switch cfg := r.Config.(type) {
	case *StageConfig:
		if cfg.Key == "" || cfg.NodesPath == "" || cfg.EdgesPath == "" || cfg.Workdir == "" {
			return fmt.Errorf("key, nodes_path, edges_path, and workdir are required")
		}
		if cfg.ZstdLevel < 0 || cfg.ZstdLevel > 22 {
			return fmt.Errorf("zstd_level must be between 0 and 22")
		}
		if cfg.SampleSize < 0 || cfg.Concurrency <= 0 || cfg.Threads < 0 {
			return fmt.Errorf("sample_size and threads must be non-negative; concurrency must be positive")
		}
		if cfg.Engine != "chdb" && cfg.Engine != "fake" {
			return fmt.Errorf("engine must be chdb or fake")
		}
		if cfg.SampleFullRelease && cfg.IDsPath == "" {
			return fmt.Errorf("sample_full_release requires ids_path")
		}
	case *PushConfig:
		if cfg.ManifestPath == "" || cfg.ReportPath == "" {
			return fmt.Errorf("manifest_path and report_path are required")
		}
		if err := validateStore(cfg.TargetStore); err != nil {
			return err
		}
		if cfg.TargetStore == "mem://" {
			return fmt.Errorf("a publication target must survive the worker process")
		}
		if cfg.Concurrency <= 0 {
			return fmt.Errorf("concurrency must be positive")
		}
	case *TrainDictConfig:
		if cfg.NodesPath == "" || cfg.EdgesPath == "" || cfg.OutputPath == "" || cfg.ReportPath == "" {
			return fmt.Errorf("nodes_path, edges_path, output_path, and report_path are required")
		}
		if cfg.Samples <= 0 || cfg.Threads < 0 {
			return fmt.Errorf("samples must be positive and threads non-negative")
		}
		if cfg.Engine != "chdb" && cfg.Engine != "fake" {
			return fmt.Errorf("engine must be chdb or fake")
		}
	case *StatusConfig:
		if cfg.OutputPath == "" {
			return fmt.Errorf("output_path is required")
		}
		return validateStore(cfg.Store)
	case *ProbeConfig:
		if cfg.OutputPath == "" || cfg.Count <= 0 {
			return fmt.Errorf("output_path is required and count must be positive")
		}
		if cfg.MaxMeanRU != nil && *cfg.MaxMeanRU <= 0 || cfg.MaxWorstRU != nil && *cfg.MaxWorstRU <= 0 {
			return fmt.Errorf("RU thresholds must be positive")
		}
		return validateStore(cfg.Store)
	case *GetConfig:
		if cfg.EdgeID == "" || cfg.OutputPath == "" {
			return fmt.Errorf("edge_id and output_path are required")
		}
		if cfg.Raw && cfg.ListVersions {
			return fmt.Errorf("raw and list_versions are mutually exclusive")
		}
		return validateStore(cfg.Store)
	case *RigConfig:
		if cfg.EdgesPath == "" || cfg.OutputPath == "" || cfg.Limit < 0 {
			return fmt.Errorf("edges_path and output_path are required; limit must be non-negative")
		}
	case *InitConfig:
		if cfg.OutputPath == "" {
			return fmt.Errorf("output_path is required")
		}
		return validateStore(cfg.Store)
	case *PurgeConfig:
		if cfg.OutputPath == "" {
			return fmt.Errorf("output_path is required")
		}
		if err := validateStore(cfg.Store); err != nil {
			return err
		}
		switch cfg.Scope {
		case "kg":
			if cfg.KG == "" || cfg.Key != "" {
				return fmt.Errorf("scope=kg requires kg and forbids key")
			}
		case "release":
			if cfg.Key == "" || cfg.KG != "" {
				return fmt.Errorf("scope=release requires key and forbids kg")
			}
		case "all", "container":
			if cfg.KG != "" || cfg.Key != "" {
				return fmt.Errorf("scope=%s forbids kg and key", cfg.Scope)
			}
		default:
			return fmt.Errorf("unknown purge scope %q", cfg.Scope)
		}
		if cfg.Scope == "container" && cfg.DryRun {
			return fmt.Errorf("container deletion has no dry-run preview")
		}
		if !cfg.DryRun && cfg.Confirmation == "" {
			return fmt.Errorf("a destructive run requires exact target confirmation")
		}
	default:
		return fmt.Errorf("unknown config type %T", r.Config)
	}
	return nil
}

func validateStore(store string) error {
	if store == "cosmos" || store == "mem://" || strings.HasPrefix(store, "file:") && len(store) > len("file:") {
		return nil
	}
	return fmt.Errorf("store must be cosmos, file:<path>, or mem://, got %q", store)
}

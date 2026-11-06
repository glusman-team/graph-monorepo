from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from linkouts_pipeline.operations import (
    GetConfig,
    ProbeConfig,
    PurgeConfig,
    PushConfig,
    StageConfig,
    StatusConfig,
    WorkerRequest,
)


def test_worker_request_round_trip(tmp_path: Path, kgx_pair: tuple[Path, Path]) -> None:
    nodes, edges = kgx_pair
    config = StageConfig(
        key="infores:test-kp-1.0.0", nodes_path=nodes, edges_path=edges, workdir=tmp_path
    )
    request = WorkerRequest.for_config(config)
    assert request.extras() == {
        "operation": "stage",
        "config_json": config.model_dump_json(),
    }
    assert WorkerRequest.model_validate(request.model_dump()).parsed_config() == config


def test_worker_request_rejects_operation_substitution(tmp_path: Path) -> None:
    config = StatusConfig(output_path=tmp_path / "status.json")
    payload = WorkerRequest.for_config(config).model_dump()
    payload["operation"] = "purge"
    with pytest.raises(ValidationError, match="purge"):
        WorkerRequest.model_validate(payload)


def test_unknown_and_secret_fields_are_not_accepted(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PushConfig.model_validate(
            {
                "manifest_path": str(tmp_path / "stage.json"),
                "report_path": str(tmp_path / "push.json"),
                "connection_string": "secret",
            }
        )


def test_production_stage_rejects_diagnostic_modes(
    tmp_path: Path, kgx_pair: tuple[Path, Path]
) -> None:
    nodes, edges = kgx_pair
    base = {
        "key": "infores:test-kp-1.0.0",
        "nodes_path": nodes,
        "edges_path": edges,
        "workdir": tmp_path,
    }
    StageConfig(**base).validate_for_publication()
    with pytest.raises(ValueError, match="validation-only"):
        StageConfig(**base, dry_run=True).validate_for_publication()
    with pytest.raises(ValueError, match="diagnostic-only"):
        StageConfig(**base, ids_path=nodes).validate_for_publication()
    with pytest.raises(ValueError, match="random-pool"):
        StageConfig(**base, sample_size=0).validate_for_publication()


def test_push_rejects_ephemeral_target(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="survive"):
        PushConfig(
            manifest_path=tmp_path / "stage.json",
            report_path=tmp_path / "report.json",
            target_store="mem://",
        )


def test_purge_requires_exact_scope_and_confirmation(tmp_path: Path) -> None:
    output = tmp_path / "purge.json"
    with pytest.raises(ValidationError, match="confirmation"):
        PurgeConfig(output_path=output, scope="all")
    with pytest.raises(ValidationError, match="requires key"):
        PurgeConfig(output_path=output, scope="release", dry_run=True)
    with pytest.raises(ValidationError, match="no dry-run"):
        PurgeConfig(output_path=output, scope="container", dry_run=True)
    config = PurgeConfig(
        output_path=output,
        scope="release",
        key="infores:test-kp-1.0.0",
        confirmation="DELETE file:test",
    )
    assert config.scope == "release"


def test_get_output_modes_are_unambiguous(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        GetConfig(
            edge_id="uuid:1",
            output_path=tmp_path / "edge.json",
            raw=True,
            list_versions=True,
        )


def test_probe_thresholds_are_positive(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ProbeConfig(output_path=tmp_path / "probe.json", max_mean_ru=0)


def test_payload_is_plain_json_without_environment_expansion(tmp_path: Path) -> None:
    config = PushConfig(
        manifest_path=tmp_path / "stage.json",
        report_path=tmp_path / "report.json",
        target_store="file:/tmp/target.ndjson",
    )
    decoded = json.loads(WorkerRequest.for_config(config).config_json)
    assert decoded["target_store"] == "file:/tmp/target.ndjson"
    assert set(WorkerRequest.for_config(config).extras()) == {"operation", "config_json"}

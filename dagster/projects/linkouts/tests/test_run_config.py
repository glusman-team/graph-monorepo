from __future__ import annotations

import pytest
from linkouts_pipeline.operations import PushConfig, StageConfig

from linkouts_dag.assets import PushRunConfig, StageRunConfig


def test_stage_run_config_maps_to_dagster_free_model(tmp_path) -> None:
    ui = StageRunConfig(
        key="infores:test-kp-1.0.0",
        nodes_path=str(tmp_path / "nodes.ndjson"),
        edges_path=str(tmp_path / "edges.ndjson"),
    )
    parsed = StageConfig(**ui.model_dump(), workdir=tmp_path)
    assert parsed.concurrency == 8
    assert parsed.sample_size == 1024
    assert parsed.engine == "chdb"
    parsed.validate_for_publication()


def test_push_run_config_preserves_expected_key(tmp_path) -> None:
    ui = PushRunConfig(expected_key="infores:test-kp-1.0.0", target_store="file:/tmp/out.ndjson")
    parsed = PushConfig(
        **ui.model_dump(),
        manifest_path=tmp_path / "stage.json",
        report_path=tmp_path / "report.json",
        dry_run=False,
    )
    assert parsed.expected_key == ui.expected_key
    assert parsed.target_store == "file:/tmp/out.ndjson"


def test_stage_rejects_invalid_compression_level(tmp_path) -> None:
    with pytest.raises(ValueError, match="less than or equal to 22"):
        StageConfig(
            key="infores:test-kp-1.0.0",
            nodes_path=tmp_path / "nodes",
            edges_path=tmp_path / "edges",
            workdir=tmp_path,
            zstd_level=23,
        )

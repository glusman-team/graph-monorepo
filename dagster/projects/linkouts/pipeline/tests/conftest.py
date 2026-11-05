from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from linkouts_pipeline.jsonio import hash_file
from linkouts_pipeline.refs import ArtifactRef
from linkouts_pipeline.staging import StageManifest, staging_dir


@pytest.fixture
def kgx_pair(tmp_path: Path) -> tuple[Path, Path]:
    nodes = tmp_path / "nodes.ndjson"
    edges = tmp_path / "edges.ndjson"
    nodes.write_text('{"id":"CHEBI:1","name":"one"}\n', encoding="utf-8")
    edges.write_text(
        '{"id":"uuid:1","subject":"CHEBI:1","predicate":"biolink:related_to","object":"CHEBI:1"}\n',
        encoding="utf-8",
    )
    return nodes, edges


@pytest.fixture
def input_refs(kgx_pair: tuple[Path, Path]) -> list[ArtifactRef]:
    return [
        ArtifactRef(
            uri=str(path),
            blake3=hash_file(path),
            media_type="application/x-ndjson",
        )
        for path in kgx_pair
    ]


@pytest.fixture
def stage_manifest(tmp_path: Path, input_refs: list[ArtifactRef]) -> StageManifest:
    store = tmp_path / "staged.ndjson"
    store.write_text('{"id":"uuid:1"}\n', encoding="utf-8")
    return StageManifest(
        digest="b3:" + "1" * 64,
        key="infores:test-kp-1.0.0",
        store=f"file:{store}",
        inputs=input_refs,
        config_fingerprint={"worker_version": "0.1.0", "zstd_level": 0},
        complete=True,
        created_at=datetime.now(UTC),
        edges=1,
        created=1,
        versions=1,
        sampled=1,
        offered=1,
    )


@pytest.fixture
def staged_workdir(tmp_path: Path) -> Path:
    workdir = tmp_path / "workdir"
    assert staging_dir(workdir, "b3:" + "1" * 64).name == "1" * 64
    return workdir

from __future__ import annotations

import json
from pathlib import Path

import pytest

from linkouts_pipeline.refs import ArtifactRef, load_refs, publish_refs, refs_path


def test_refs_round_trip(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.ndjson"
    artifact.write_text('{"id":"uuid:1"}\n', encoding="utf-8")
    ref = ArtifactRef.for_file(artifact, media_type="application/x-ndjson", rows=1)
    path = publish_refs(tmp_path, "stage_release", [ref])
    assert path == refs_path(tmp_path, "stage_release")
    assert load_refs(tmp_path, "stage_release") == [ref]
    assert json.loads(path.read_text()) == [ref.model_dump(mode="json", exclude_none=True)]


def test_missing_upstream_names_producer_job(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="linkouts_stage_release"):
        load_refs(tmp_path, "stage_release")


def test_refs_must_be_a_list(tmp_path: Path) -> None:
    path = refs_path(tmp_path, "stage_release")
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    with pytest.raises(ValueError, match="JSON list"):
        load_refs(tmp_path, "stage_release")


def test_stage_name_cannot_escape_refs_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe stage"):
        refs_path(tmp_path, "../other")


def test_tampered_artifact_is_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.ndjson"
    artifact.write_text('{"id":"uuid:1"}\n', encoding="utf-8")
    ref = ArtifactRef.for_file(artifact, media_type="application/x-ndjson")
    publish_refs(tmp_path, "stage_release", [ref])
    artifact.write_text('{"id":"uuid:tampered"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="changed after"):
        load_refs(tmp_path, "stage_release")


def test_missing_referenced_artifact_is_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.ndjson"
    artifact.write_text('{"id":"uuid:1"}\n', encoding="utf-8")
    ref = ArtifactRef.for_file(artifact, media_type="application/x-ndjson")
    publish_refs(tmp_path, "stage_release", [ref])
    artifact.unlink()
    with pytest.raises(FileNotFoundError, match="referenced artifact missing"):
        load_refs(tmp_path, "stage_release")

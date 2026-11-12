from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from linkouts_pipeline.staging import StageManifest, manifest_path, staging_dir


def test_manifest_round_trip_is_content_addressed(
    stage_manifest: StageManifest, tmp_path: Path
) -> None:
    path = manifest_path(tmp_path, stage_manifest.digest)
    stage_manifest.write(path)
    loaded = StageManifest.read(path)
    assert loaded == stage_manifest
    assert loaded.validate_for_publication() == stage_manifest


def test_staging_path_rejects_non_digest_input(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        staging_dir(tmp_path, "../../production")


def test_manifest_requires_local_store(stage_manifest: StageManifest) -> None:
    payload = stage_manifest.model_dump(mode="json")
    payload["store"] = "cosmos"
    with pytest.raises(ValidationError, match="file:<path>"):
        StageManifest.model_validate(payload)


def test_incomplete_filtered_or_failed_manifest_cannot_publish(
    stage_manifest: StageManifest,
) -> None:
    for field, value, message in [
        ("complete", False, "incomplete"),
        ("filtered", True, "diagnostic"),
        ("failed", 1, "failed edges"),
        ("edges", 0, "no edges"),
        ("sampled", 0, "random-pool"),
    ]:
        payload = stage_manifest.model_dump(mode="json")
        payload[field] = value
        with pytest.raises(ValueError, match=message):
            StageManifest.model_validate(payload).validate_for_publication()


def test_manifest_requires_existing_store(stage_manifest: StageManifest) -> None:
    stage_manifest.store_path.unlink()
    with pytest.raises(FileNotFoundError, match="staged store missing"):
        stage_manifest.validate_for_publication()

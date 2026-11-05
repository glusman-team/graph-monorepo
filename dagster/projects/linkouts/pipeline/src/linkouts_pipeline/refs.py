"""Artifact references exchanged by Dagster and the Go worker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from linkouts_pipeline.jsonio import hash_file, write_json_atomic

Blake3Id = Annotated[str, Field(pattern=r"^b3:[0-9a-f]{64}$")]


class ContractModel(BaseModel):
    """Strict wire-model defaults: unknown fields are integration errors."""

    model_config = ConfigDict(extra="forbid")


class ArtifactRef(ContractModel):
    """One immutable artifact, using the monorepo's established JSON field names."""

    uri: str
    blake3: Blake3Id
    media_type: str
    rows: int | None = Field(default=None, ge=0)
    schema_fingerprint: str | None = None
    manifest: str | None = None

    @classmethod
    def for_file(
        cls,
        path: Path,
        *,
        media_type: str,
        rows: int | None = None,
        manifest: Path | None = None,
    ) -> ArtifactRef:
        if not path.is_file():
            msg = f"artifact missing: {path}"
            raise FileNotFoundError(msg)
        return cls(
            uri=str(path),
            blake3=hash_file(path),
            media_type=media_type,
            rows=rows,
            manifest=str(manifest) if manifest is not None else None,
        )


def refs_path(published_root: Path, stage: str) -> Path:
    """The sole published handoff path for a stage."""
    if not stage or "/" in stage or stage.startswith("."):
        msg = f"unsafe stage name: {stage!r}"
        raise ValueError(msg)
    return published_root / "linkouts" / "refs" / f"{stage}.refs.json"


def publish_refs(published_root: Path, stage: str, refs: list[ArtifactRef]) -> Path:
    path = refs_path(published_root, stage)
    write_json_atomic(path, [ref.model_dump(mode="json", exclude_none=True) for ref in refs])
    return path


def load_refs(
    published_root: Path, stage: str, *, producer: str | None = None
) -> list[ArtifactRef]:
    path = refs_path(published_root, stage)
    if not path.is_file():
        job = producer or stage
        msg = (
            f"upstream artifact missing: {path}. Publish it with 'linkouts_{job}' "
            f"or launch 'linkouts_upload' for the full chain"
        )
        raise FileNotFoundError(msg)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        msg = f"artifact refs must be a JSON list: {path}"
        raise ValueError(msg)
    refs = [ArtifactRef.model_validate(item) for item in raw]
    for ref in refs:
        artifact = Path(ref.uri)
        if not artifact.is_file():
            msg = f"referenced artifact missing: {artifact}"
            raise FileNotFoundError(msg)
        actual = hash_file(artifact)
        if actual != ref.blake3:
            msg = (
                f"artifact content changed after {stage} publication: {artifact} "
                f"(expected {ref.blake3}, got {actual})"
            )
            raise ValueError(msg)
    return refs


__all__ = [
    "ArtifactRef",
    "Blake3Id",
    "ContractModel",
    "load_refs",
    "publish_refs",
    "refs_path",
]

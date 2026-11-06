"""Content-addressed staging and publication reports."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from linkouts_pipeline.jsonio import canonical_json, hash_bytes, write_json_atomic
from linkouts_pipeline.refs import ArtifactRef, Blake3Id, ContractModel

STAGE_SCHEMA = "linkouts.stage/1"
PUBLICATION_SCHEMA = "linkouts.publication/1"
LOAD_SCHEMA = "linkouts.load/1"
REPORT_SCHEMA = "linkouts.report/1"
STAGE_MEDIA_TYPE = "application/vnd.linkouts.stage-manifest+json"
REPORT_MEDIA_TYPE = "application/vnd.linkouts.report+json"


def staging_dir(workdir: Path, digest: str) -> Path:
    """Resolve a digest-scoped store; the pattern blocks traversal and arbitrary deletion."""
    parsed = ArtifactRef(uri="unused", blake3=digest, media_type="application/octet-stream").blake3
    return workdir / "stores" / parsed.removeprefix("b3:")


def manifest_path(workdir: Path, digest: str) -> Path:
    return staging_dir(workdir, digest) / "stage-manifest.json"


class StageManifest(ContractModel):
    """Everything publication needs to trust one completed local staging store."""

    schema_version: Literal["linkouts.stage/1"] = STAGE_SCHEMA
    digest: Blake3Id
    key: str = Field(min_length=1)
    store: str
    inputs: list[ArtifactRef] = Field(min_length=2, max_length=2)
    dictionary: ArtifactRef | None = None
    filter_input: ArtifactRef | None = None
    config_fingerprint: dict[str, Any]
    filtered: bool = False
    complete: bool = False
    created_at: datetime
    edges: int = Field(ge=0)
    created: int = Field(default=0, ge=0)
    merged: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    versions: int = Field(default=0, ge=0)
    sampled: int = Field(default=0, ge=0)
    offered: int = Field(default=0, ge=0)

    @field_validator("store")
    @classmethod
    def local_store_only(cls, value: str) -> str:
        if not value.startswith("file:") or value == "file:":
            msg = "a staged manifest requires file:<path>"
            raise ValueError(msg)
        return value

    @property
    def store_path(self) -> Path:
        return Path(self.store.removeprefix("file:"))

    def content_id(self) -> str:
        return hash_bytes(canonical_json(self.model_dump(mode="json", exclude_none=True)))

    def validate_for_publication(self, *, require_store: bool = True) -> StageManifest:
        if not self.complete:
            msg = "staged release is incomplete"
            raise ValueError(msg)
        if self.filtered:
            msg = "filtered or diagnostic staging cannot be published"
            raise ValueError(msg)
        if self.failed:
            msg = f"staged release has {self.failed} failed edges"
            raise ValueError(msg)
        if self.edges == 0:
            msg = "staged release contains no edges"
            raise ValueError(msg)
        if self.sampled == 0:
            msg = "staged release has no random-pool sample"
            raise ValueError(msg)
        if require_store and not self.store_path.is_file():
            msg = f"staged store missing: {self.store_path}"
            raise FileNotFoundError(msg)
        return self

    def write(self, path: Path) -> None:
        write_json_atomic(path, self.model_dump(mode="json", exclude_none=True))

    @classmethod
    def read(cls, path: Path) -> StageManifest:
        if not path.is_file():
            msg = f"staged manifest missing: {path}"
            raise FileNotFoundError(msg)
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class PublicationReport(ContractModel):
    schema_version: Literal["linkouts.publication/1"] = PUBLICATION_SCHEMA
    source_digest: Blake3Id
    key: str
    target_store: str
    dry_run: bool
    completed_at: datetime
    output_path: Path | None = None
    read: int = Field(ge=0)
    created: int = Field(ge=0)
    skipped: int = Field(ge=0)
    replaced: int = Field(ge=0)
    failed: int = Field(ge=0)
    blob_bytes: int = Field(ge=0)

    def validate_complete(self) -> PublicationReport:
        if self.dry_run:
            msg = "a dry-run report is not a completed publication"
            raise ValueError(msg)
        if self.failed:
            msg = f"publication has {self.failed} failed documents"
            raise ValueError(msg)
        if self.read == 0:
            msg = "publication read no documents"
            raise ValueError(msg)
        return self


class LoadReport(ContractModel):
    """A direct merge/load against an existing store.

    This proves that a release was merged without losing stored history. It is intentionally
    separate from the staged-publication report.
    """

    schema_version: Literal["linkouts.load/1"] = LOAD_SCHEMA
    key: str = Field(min_length=1)
    store: str
    completed_at: datetime
    edges: int = Field(ge=0)
    created: int = Field(ge=0)
    merged: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)
    filtered: int = Field(ge=0)
    versions: int = Field(ge=0)
    sampled: int = Field(ge=0)
    offered: int = Field(ge=0)
    throttled: int = Field(ge=0)
    raw_bytes: int = Field(ge=0)
    blob_bytes: int = Field(ge=0)
    ru: float = Field(ge=0)
    waits: int = Field(ge=0)

    @field_validator("store")
    @classmethod
    def persistent_store(cls, value: str) -> str:
        if value == "mem://":
            msg = "a completed load report requires a persistent store"
            raise ValueError(msg)
        return value

    def validate_complete(self) -> LoadReport:
        if self.failed:
            msg = f"load has {self.failed} failed documents"
            raise ValueError(msg)
        if self.edges == 0:
            msg = "load processed no edges"
            raise ValueError(msg)
        if self.versions == 0:
            msg = "load wrote no version metadata"
            raise ValueError(msg)
        if self.sampled == 0:
            msg = "load wrote no random-pool sample"
            raise ValueError(msg)
        if self.blob_bytes == 0:
            msg = "load wrote no documents"
            raise ValueError(msg)
        return self

    def write(self, path: Path) -> None:
        write_json_atomic(path, self.model_dump(mode="json", exclude_none=True))

    @classmethod
    def read(cls, path: Path) -> LoadReport:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class OperationReport(ContractModel):
    """Structured output for inspection, dictionary, probe, and admin operations."""

    schema_version: Literal["linkouts.report/1"] = REPORT_SCHEMA
    operation: str
    status: Literal["success", "dry_run"]
    created_at: datetime
    details: dict[str, Any]

    def write(self, path: Path) -> None:
        write_json_atomic(path, self.model_dump(mode="json", exclude_none=True))

    @classmethod
    def read(cls, path: Path) -> OperationReport:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def read_json(path: Path) -> dict[str, Any]:
    """Read an object with a useful error for workers that emit malformed reports."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"expected a JSON object: {path}"
        raise ValueError(msg)
    return raw


__all__ = [
    "LOAD_SCHEMA",
    "PUBLICATION_SCHEMA",
    "REPORT_MEDIA_TYPE",
    "REPORT_SCHEMA",
    "STAGE_MEDIA_TYPE",
    "STAGE_SCHEMA",
    "LoadReport",
    "OperationReport",
    "PublicationReport",
    "StageManifest",
    "manifest_path",
    "read_json",
    "staging_dir",
]

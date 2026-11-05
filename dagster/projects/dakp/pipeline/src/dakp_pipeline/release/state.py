"""Persistent release state and deterministic source fingerprints."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from dakp_pipeline.io.content_hash import hash_bytes
from dakp_pipeline.io.contracts import ArtifactRef

STATE_SCHEMA = "dakp.release-state.v1"
DELTA_SCHEMA = "dakp.source-delta.v1"


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _date(value: str | None) -> str | None:
    parsed = _timestamp(value)
    return parsed.date().isoformat() if parsed is not None else None


@dataclass(frozen=True)
class SourceFingerprint:
    fingerprint: str
    artifact_count: int
    upstream_dates: tuple[str, ...] = ()
    retrieved_dates: tuple[str, ...] = ()
    latest_retrieved_at: str | None = None

    @classmethod
    def from_refs(cls, refs: list[ArtifactRef]) -> SourceFingerprint:
        """Hash sorted artifact ids and collect the upstream/retrieval date evidence."""
        artifact_ids = sorted({ref.blake3 for ref in refs})
        fingerprint = hash_bytes("\n".join(artifact_ids).encode("utf-8"))
        upstream: set[str] = set()
        retrieved: set[str] = set()
        retrieval_timestamps: list[tuple[datetime, str]] = []
        # DailyMed can fan out to tens of thousands of manifests. Read the small JSON
        # files directly instead of constructing a pydantic model per document.
        for ref in refs:
            if ref.manifest is None or not ref.manifest.exists():
                continue
            try:
                payload = json.loads(ref.manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            source = payload.get("source") or {}
            if date := _date(source.get("last_modified")):
                upstream.add(date)
            if date := _date(source.get("retrieved_at")):
                retrieved.add(date)
            raw_retrieved = source.get("retrieved_at")
            if parsed := _timestamp(raw_retrieved):
                retrieval_timestamps.append((parsed, raw_retrieved))
        latest = max(retrieval_timestamps, default=(None, None), key=lambda row: row[0])[1]
        return cls(
            fingerprint=fingerprint,
            artifact_count=len(refs),
            upstream_dates=tuple(sorted(upstream)),
            retrieved_dates=tuple(sorted(retrieved)),
            latest_retrieved_at=latest,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SourceFingerprint:
        return cls(
            fingerprint=str(payload["fingerprint"]),
            artifact_count=int(payload["artifact_count"]),
            upstream_dates=tuple(payload.get("upstream_dates") or ()),
            retrieved_dates=tuple(payload.get("retrieved_dates") or ()),
            latest_retrieved_at=payload.get("latest_retrieved_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReleaseState:
    version: str
    published_at: str
    sources: dict[str, SourceFingerprint]
    kg: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA,
            "version": self.version,
            "published_at": self.published_at,
            "sources": {name: source.to_dict() for name, source in self.sources.items()},
            "kg": self.kg,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ReleaseState:
        if payload.get("schema_version") != STATE_SCHEMA:
            msg = f"unsupported release-state schema: {payload.get('schema_version')!r}"
            raise ValueError(msg)
        return cls(
            version=str(payload["version"]),
            published_at=str(payload["published_at"]),
            sources={
                name: SourceFingerprint.from_dict(source)
                for name, source in payload["sources"].items()
            },
            kg=dict(payload.get("kg") or {}),
        )


def state_path(published_root: Path) -> Path:
    return published_root / "dakp" / "release" / "state.json"


def pending_delta_path(published_root: Path) -> Path:
    return published_root / "dakp" / "release" / "pending.json"


def load_state(path: Path) -> ReleaseState | None:
    if not path.exists():
        return None
    return ReleaseState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def write_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        temporary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def write_state(path: Path, state: ReleaseState) -> Path:
    return write_json_atomic(path, state.to_dict())


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "DELTA_SCHEMA",
    "STATE_SCHEMA",
    "ReleaseState",
    "SourceFingerprint",
    "load_state",
    "pending_delta_path",
    "state_path",
    "utc_now_iso",
    "write_json_atomic",
    "write_state",
]

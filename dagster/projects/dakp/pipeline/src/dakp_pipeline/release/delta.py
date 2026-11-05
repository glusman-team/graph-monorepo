"""Source-level release deltas and their pregenerated change message."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dakp_pipeline.release.state import (
    DELTA_SCHEMA,
    ReleaseState,
    SourceFingerprint,
    pending_delta_path,
    utc_now_iso,
    write_json_atomic,
)


@dataclass(frozen=True)
class SourceChange:
    source: str
    current: SourceFingerprint
    previous: SourceFingerprint | None

    @property
    def dates(self) -> tuple[str, ...]:
        return self.current.upstream_dates or self.current.retrieved_dates


@dataclass(frozen=True)
class SourceDelta:
    current: dict[str, SourceFingerprint]
    changes: tuple[SourceChange, ...]
    previous_version: str | None
    created_at: str = field(default_factory=utc_now_iso)
    check_run_id: str | None = None
    check_started_at: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    @property
    def dates(self) -> tuple[str, ...]:
        return tuple(sorted({date for change in self.changes for date in change.dates}))

    @property
    def message(self) -> str:
        if not self.changes:
            return "New data for 0 sources: no source artifacts changed; no build, version bump, or upload."
        count = len(self.changes)
        noun = "source" if count == 1 else "sources"
        dates = ", ".join(self.dates) if self.dates else "unreported upstream dates"
        details = "; ".join(
            f"{change.source} ({', '.join(change.dates) if change.dates else 'date not reported'})"
            for change in self.changes
        )
        return f"New data for {count} {noun} on {dates}: {details}."

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DELTA_SCHEMA,
            "created_at": self.created_at,
            "check_run_id": self.check_run_id,
            "check_started_at": self.check_started_at,
            "previous_version": self.previous_version,
            "changed": self.changed,
            "message": self.message,
            "current": {name: source.to_dict() for name, source in self.current.items()},
            "changes": [
                {
                    "source": change.source,
                    "current": change.current.to_dict(),
                    "previous": change.previous.to_dict() if change.previous else None,
                    "dates": list(change.dates),
                }
                for change in self.changes
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SourceDelta:
        if payload.get("schema_version") != DELTA_SCHEMA:
            msg = f"unsupported source-delta schema: {payload.get('schema_version')!r}"
            raise ValueError(msg)
        current = {
            name: SourceFingerprint.from_dict(source) for name, source in payload["current"].items()
        }
        changes = tuple(
            SourceChange(
                source=str(change["source"]),
                current=SourceFingerprint.from_dict(change["current"]),
                previous=(
                    SourceFingerprint.from_dict(change["previous"])
                    if change.get("previous")
                    else None
                ),
            )
            for change in payload.get("changes") or ()
        )
        return cls(
            current=current,
            changes=changes,
            previous_version=payload.get("previous_version"),
            created_at=str(payload["created_at"]),
            check_run_id=payload.get("check_run_id"),
            check_started_at=payload.get("check_started_at"),
        )


def compute_delta(
    current: dict[str, SourceFingerprint],
    previous: ReleaseState | None,
    *,
    check_run_id: str | None = None,
    check_started_at: str | None = None,
) -> SourceDelta:
    empty = sorted(name for name, fingerprint in current.items() if fingerprint.artifact_count <= 0)
    if empty:
        msg = f"source acquisition produced zero artifacts for: {', '.join(empty)}"
        raise ValueError(msg)
    previous_sources = previous.sources if previous is not None else {}
    vanished = sorted(set(previous_sources) - set(current))
    if vanished:
        msg = (
            f"previously published sources are absent from this acquisition: {', '.join(vanished)}"
        )
        raise ValueError(msg)
    changes = tuple(
        SourceChange(name, fingerprint, previous_sources.get(name))
        for name, fingerprint in sorted(current.items())
        if name not in previous_sources
        or previous_sources[name].fingerprint != fingerprint.fingerprint
    )
    return SourceDelta(
        current=current,
        changes=changes,
        previous_version=previous.version if previous is not None else None,
        check_run_id=check_run_id,
        check_started_at=check_started_at,
    )


def write_pending_delta(published_root: Path, delta: SourceDelta, *, check_run_id: str) -> Path:
    payload = delta.to_dict()
    payload["check_run_id"] = check_run_id
    return write_json_atomic(pending_delta_path(published_root), payload)


def load_pending_delta(published_root: Path) -> SourceDelta:
    path = pending_delta_path(published_root)
    if not path.exists():
        msg = f"no pending source delta at {path}; run dakp_release_check first"
        raise FileNotFoundError(msg)
    return SourceDelta.from_dict(json.loads(path.read_text(encoding="utf-8")))


__all__ = [
    "SourceChange",
    "SourceDelta",
    "compute_delta",
    "load_pending_delta",
    "write_pending_delta",
]

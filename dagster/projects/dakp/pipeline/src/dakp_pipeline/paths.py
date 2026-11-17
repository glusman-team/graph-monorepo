"""Configurable workdir layout. No absolute paths anywhere in the pipeline.

All paths are derived from a single workdir root, which is supplied by the caller
(CLI ``--workdir``, a stage param ``workdir``, or ``tmp_path`` in tests). The
layout mirrors the directory tree the pipeline writes under that root.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Workdir(BaseModel):
    """Immutable handle to the pipeline workdir tree.

    Constructing an instance does not create directories; call :meth:`create` to
    materialize the full tree (idempotent).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    root: Path

    # -- raw acquisition layer -------------------------------------------------
    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def by_hash(self) -> Path:
        """Content-addressed store: ``raw/by-hash/<hash>/``."""
        return self.raw / "by-hash"

    @property
    def aliases(self) -> Path:
        """Human-readable aliases into the content-addressed store."""
        return self.raw / "aliases"

    # -- processed layers ------------------------------------------------------
    @property
    def interim(self) -> Path:
        """Partitioned parquet interim tables (``interim/``)."""
        return self.root / "interim"

    @property
    def tabular(self) -> Path:
        """Uncompressed TSV assertion tables consumed by Tablassert (``tabular/``)."""
        return self.root / "tabular"

    @property
    def kgx(self) -> Path:
        """Final KGX NDJSON + RIG outputs produced by Tablassert (``kgx/``)."""
        return self.root / "kgx"

    @property
    def manifests(self) -> Path:
        """Per-artifact JSON manifests (``manifests/``)."""
        return self.root / "manifests"

    @property
    def store(self) -> Path:
        """Typed/derived store artifacts (``store/``)."""
        return self.root / "store"

    @property
    def reports(self) -> Path:
        """Per-task ``task_report.json`` and build summaries."""
        return self.root / "reports"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def create(self) -> None:
        """Create the full directory tree (idempotent)."""
        for path in (
            self.by_hash,
            self.aliases,
            self.interim,
            self.tabular,
            self.kgx,
            self.manifests,
            self.store,
            self.reports,
            self.logs,
        ):
            path.mkdir(parents=True, exist_ok=True)

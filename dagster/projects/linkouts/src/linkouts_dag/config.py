"""Process-level paths; secrets remain in the worker's environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeConfig:
    published_root: Path
    workdir: Path
    worker_bin: Path
    run_dir: Path
    destructive_jobs_enabled: bool = False


def run_config() -> RuntimeConfig:
    published = Path(
        os.environ.get("MO_PUBLISHED_ROOT", str(PROJECT_ROOT / "published"))
    ).expanduser()
    workdir = Path(
        os.environ.get("LINKOUTS_WORKDIR", str(published / "linkouts" / "workdir"))
    ).expanduser()
    worker = Path(
        os.environ.get(
            "LINKOUTS_WORKER_BIN",
            str(PROJECT_ROOT / "workers" / "go" / "bin" / "linkouts-worker"),
        )
    ).expanduser()
    return RuntimeConfig(
        published_root=published,
        workdir=workdir,
        worker_bin=worker,
        run_dir=workdir / "runs",
        destructive_jobs_enabled=_truthy(os.environ.get("LINKOUTS_ENABLE_DESTRUCTIVE_JOBS")),
    )


__all__ = ["PROJECT_ROOT", "RuntimeConfig", "run_config"]

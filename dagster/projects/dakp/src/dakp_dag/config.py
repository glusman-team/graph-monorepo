"""Run configuration for the DAKP code location (env-driven, no orchestrator Variables).

Every knob is an environment variable resolved once per process; assets read them through
:func:`run_config` so a test can monkeypatch the env (or pass an explicit config) without
touching asset bodies. Nothing here imports dagster: it is plain configuration plumbing
shared by the assets and their tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# The project root (dagster/projects/dakp) and the published-artifact root follow the
# monorepo convention: env wins, the project dir is the default.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


def _env_int(name: str, default: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        msg = f"{name}={raw!r} is not an integer"
        raise ValueError(msg) from None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RunConfig:
    """The resolved run configuration for one code-location process."""

    published_root: Path
    workdir: Path
    worker_bin: Path
    fullmap: str | None
    quarter_limit: int
    release_limit: int
    threads: int
    memory_budget_gb: int
    force: bool
    ner_offline: bool
    publication_version: str | None

    @property
    def refs_dir(self) -> Path:
        """Where each stage publishes its ArtifactRef handoff (the inter-stage contract)."""
        return self.published_root / "dakp" / "refs"

    @property
    def kg_dir(self) -> Path:
        """Where the KG build publishes its KGX ndjson pair + QC report."""
        return self.published_root / "dakp" / "kg"

    def params(self) -> dict[str, object]:
        """The stage params forwarded into every :class:`TaskContext`."""
        params: dict[str, object] = {}
        if self.quarter_limit:
            params["quarter_limit"] = self.quarter_limit
        if self.release_limit:
            params["release_limit"] = self.release_limit
        if self.threads:
            params["threads"] = self.threads
        if self.memory_budget_gb:
            params["memory_budget_gb"] = self.memory_budget_gb
        if self.force:
            params["force"] = True
        if self.fullmap:
            params["fullmap"] = self.fullmap
            # A configured fullmap is what turns the Tablassert handoff real (DAKP runtime.py
            # derives run_tablassert the same way); without it kg_build defers.
            params["run_tablassert"] = True
        if self.publication_version:
            params["publication_version"] = self.publication_version
        return params


def run_config() -> RunConfig:
    """Resolve the run configuration from the environment (see the module docstring)."""
    published_root = _env_path("MO_PUBLISHED_ROOT", PROJECT_ROOT / "published")
    workdir = _env_path("DAKP_WORKDIR", published_root / "dakp" / "work")
    fullmap = os.environ.get("DAKP_FULLMAP") or None
    version = os.environ.get("DAKP_PUBLICATION_VERSION") or None
    return RunConfig(
        published_root=published_root,
        workdir=workdir,
        worker_bin=_env_path(
            "DAKP_EXTRACT_WORKER_BIN", PROJECT_ROOT / "workers" / "go" / "bin" / "dakp-extract"
        ),
        fullmap=fullmap,
        quarter_limit=_env_int("DAKP_QUARTER_LIMIT"),
        release_limit=_env_int("DAKP_RELEASE_LIMIT"),
        threads=_env_int("DAKP_THREADS"),
        memory_budget_gb=_env_int("DAKP_MEMORY_BUDGET_GB"),
        force=_env_bool("DAKP_FORCE"),
        ner_offline=_env_bool("DAKP_NER_OFFLINE"),
        publication_version=version,
    )


def task_context(config: RunConfig | None = None):
    """Build the pipeline :class:`TaskContext` for a stage run.

    Imported lazily so this module stays importable without the pipeline package (the
    dagster code location loads defs even when a stage will never run).
    """
    from dakp_pipeline.io.contracts import TaskContext
    from dakp_pipeline.paths import Workdir

    cfg = config or run_config()
    workdir = Workdir(cfg.workdir)
    workdir.create()
    return TaskContext(workdir=workdir.root, fixture_root=None, params=cfg.params())


__all__ = ["PROJECT_ROOT", "RunConfig", "run_config", "task_context"]

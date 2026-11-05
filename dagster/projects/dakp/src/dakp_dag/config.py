"""Run configuration for the DAKP code location (env-driven, no orchestrator Variables).

Every knob is an environment variable resolved once per process; assets read them through
:func:`run_config` so a test can monkeypatch the env (or pass an explicit config) without
touching asset bodies. Nothing here imports dagster: it is plain configuration plumbing
shared by the assets and their tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# The project root (dagster/projects/dakp) and the published-artifact root follow the
# monorepo convention: env wins, the project dir is the default.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# DAKP production builds load the lexical disease map + offline NER gazetteer from the repo's
# pipeline fixture root (cli.py ``_DEFAULT_FIXTURE_ROOT``); the port mirrors that default.
DEFAULT_FIXTURE_ROOT = PROJECT_ROOT / "pipeline" / "tests" / "fixtures" / "pipeline"
# DAKP freshness-gates DailyMed / Drugs@FDA / EMA re-downloads to a 7-day cache window.
DEFAULT_SOURCE_MAX_AGE_DAYS = 7.0
PUBLICATION_VERSION_TAG = "dakp/publication_version"


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


def _env_float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        msg = f"{name}={raw!r} is not a number"
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
    fixture_root: Path | None = None
    # Tablassert ``build-kg --release`` / ``--qc``: always on for DAKP production builds.
    release: bool = True
    qc: bool = True
    source_max_age_days: float | None = DEFAULT_SOURCE_MAX_AGE_DAYS
    source_refresh: str = "auto"
    dailymed_max_age_days: float | None = None
    drugsfda_max_age_days: float | None = None
    ema_max_age_days: float | None = None
    ema_documents_max_age_days: float | None = None
    ema_smpc_max_age_days: float | None = 30.0
    canada_vigilance_max_age_days: float | None = 30.0
    faers_max_age_days: float | None = None
    icd10cm_max_age_days: float | None = None
    # ICD-10-CM release ZIP override; None discovers the latest CDC release.
    icd10cm_url: str | None = None

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
        params["source_refresh"] = self.source_refresh
        if self.fullmap:
            params["fullmap"] = self.fullmap
            # A configured fullmap is what turns the Tablassert handoff real (DAKP runtime.py
            # derives run_tablassert the same way); without it kg_build defers.
            params["run_tablassert"] = True
        if self.publication_version:
            params["publication_version"] = self.publication_version
        params["release"] = self.release
        if self.icd10cm_url:
            params["icd10cm_url"] = self.icd10cm_url
        params["qc"] = self.qc
        source_ages = {
            "dailymed": self.dailymed_max_age_days,
            "drugsfda": self.drugsfda_max_age_days,
            "ema": self.ema_max_age_days,
            "ema_documents": self.ema_documents_max_age_days,
            "ema_smpc": self.ema_smpc_max_age_days,
            "canada_vigilance": self.canada_vigilance_max_age_days,
            "faers": self.faers_max_age_days,
            "icd10cm": self.icd10cm_max_age_days,
        }
        for source, age in source_ages.items():
            resolved = self.source_max_age_days if age is None else age
            if resolved is not None:
                params[f"{source}_max_age_days"] = resolved
        return params


def run_config(publication_version: str | None = None) -> RunConfig:
    """Resolve the run configuration from the environment (see the module docstring)."""
    published_root = _env_path("MO_PUBLISHED_ROOT", PROJECT_ROOT / "published")
    workdir = _env_path("DAKP_WORKDIR", published_root / "dakp" / "work")
    fullmap = os.environ.get("DAKP_FULLMAP") or None
    version = publication_version or os.environ.get("DAKP_PUBLICATION_VERSION") or None
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
        # Explicit source URL override (tests / reproducible historical builds).
        icd10cm_url=os.environ.get("DAKP_ICD10CM_URL") or None,
        fixture_root=_env_path("DAKP_FIXTURE_ROOT", DEFAULT_FIXTURE_ROOT),
        release=_env_bool("DAKP_TABLASSERT_RELEASE", True),
        qc=_env_bool("DAKP_TABLASSERT_QC", True),
        source_max_age_days=_env_float("DAKP_SOURCE_MAX_AGE_DAYS", DEFAULT_SOURCE_MAX_AGE_DAYS),
        source_refresh=os.environ.get("DAKP_SOURCE_REFRESH", "auto").strip().lower(),
        dailymed_max_age_days=_env_float("DAKP_DAILYMED_MAX_AGE_DAYS", None),
        drugsfda_max_age_days=_env_float("DAKP_DRUGSFDA_MAX_AGE_DAYS", None),
        ema_max_age_days=_env_float("DAKP_EMA_MAX_AGE_DAYS", None),
        ema_documents_max_age_days=_env_float("DAKP_EMA_DOCUMENTS_MAX_AGE_DAYS", None),
        ema_smpc_max_age_days=_env_float("DAKP_EMA_SMPC_MAX_AGE_DAYS", 30.0),
        canada_vigilance_max_age_days=_env_float("DAKP_CANADA_VIGILANCE_MAX_AGE_DAYS", 30.0),
        faers_max_age_days=_env_float("DAKP_FAERS_MAX_AGE_DAYS", None),
        icd10cm_max_age_days=_env_float("DAKP_ICD10CM_MAX_AGE_DAYS", None),
    )


def run_config_for(context):
    """Resolve config with the release run's immutable version tag, when present."""
    version = context.run.tags.get(PUBLICATION_VERSION_TAG) if context.run else None
    return run_config(publication_version=version)


def task_context(config: RunConfig | None = None):
    """Build the pipeline :class:`TaskContext` for a stage run.

    Imported lazily so this module stays importable without the pipeline package (the
    dagster code location loads defs even when a stage will never run).
    """
    from dakp_pipeline.paths import Workdir
    from dakp_pipeline.runtime import build_context

    cfg = config or run_config()
    workdir = Workdir(cfg.workdir)
    workdir.create()
    # build_context loads the lexical disease map from the fixture root into
    # params["disease_map"] (DAKP runtime.py), and the shapers' default_ner reads the
    # offline gazetteer from the same root.
    return build_context(workdir, cfg.fixture_root, cfg.params())


@dataclass(frozen=True)
class ReleaseSettings:
    """Environment-backed automation settings, resolved without contacting any API."""

    enabled: bool = False
    dry_run: bool = False
    cron: str = "0 17 * * FRI"
    timezone: str = "America/Los_Angeles"
    gpu_max_used_mib: int = 1024
    gpu_gate_hours: int = 24
    min_free_gb: float = 100.0
    hf_repo_id: str = "SkyeAv/drug-approvals-kp"
    github_repo: str = "glusman-team/graph-monorepo"
    repo_root: Path = PROJECT_ROOT.parents[2]
    dagster_url: str = "http://localhost:3000"
    hf_token: str | None = field(default=None, repr=False)
    github_token: str | None = field(default=None, repr=False)
    nercache_external: bool = False
    nercache_required: bool = False


def release_settings() -> ReleaseSettings:
    return ReleaseSettings(
        enabled=_env_bool("DAKP_RELEASE_ENABLED"),
        dry_run=_env_bool("DAKP_PUBLISH_DRY_RUN"),
        cron=os.environ.get("DAKP_RELEASE_CRON", "0 17 * * FRI"),
        timezone=os.environ.get("DAKP_RELEASE_TIMEZONE", "America/Los_Angeles"),
        gpu_max_used_mib=_env_int("DAKP_GPU_MAX_USED_MIB", 1024),
        gpu_gate_hours=_env_int("DAKP_GPU_GATE_HOURS", 24),
        min_free_gb=_env_float("DAKP_MIN_FREE_GB", 100.0) or 0.0,
        hf_repo_id=os.environ.get("DAKP_HF_REPO_ID", "SkyeAv/drug-approvals-kp"),
        github_repo=os.environ.get("DAKP_GITHUB_REPO", "glusman-team/graph-monorepo"),
        repo_root=_env_path("DAKP_REPO_ROOT", PROJECT_ROOT.parents[2]),
        dagster_url=os.environ.get("DAKP_DAGSTER_URL", "http://localhost:3000").rstrip("/"),
        hf_token=os.environ.get("HF_TOKEN") or None,
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        nercache_external=_env_bool("DAKP_NERCACHE_EXTERNAL"),
        nercache_required=_env_bool("DAKP_NERCACHE_REQUIRED"),
    )


__all__ = [
    "PROJECT_ROOT",
    "PUBLICATION_VERSION_TAG",
    "ReleaseSettings",
    "RunConfig",
    "release_settings",
    "run_config",
    "run_config_for",
    "task_context",
]

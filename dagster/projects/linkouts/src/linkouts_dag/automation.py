"""DAKP release automation for the Linkouts live-load job.

The sensor consumes only the public ``hf_publish`` materialization contract. It never imports
DAKP code and never carries Cosmos credentials; the worker reads those from its process
environment after Dagster launches the narrow job.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dagster as dg
from dagster import SensorEvaluationContext

from linkouts_dag.assets import linkouts_load_release
from linkouts_dag.config import PROJECT_ROOT

MONOREPO_ROOT = PROJECT_ROOT.parents[2]
DEFAULT_DAKP_DICTIONARY_PATH = (
    MONOREPO_ROOT / "sites" / "linkouts" / "priv" / "zstd" / "drugapprovals-kp.77140996.dict"
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        msg = f"{name}={raw!r} is not an integer"
        raise ValueError(msg) from None


@dataclass(frozen=True)
class LinkoutsAutomationSettings:
    """Environment-backed settings for the DAKP-to-Linkouts release path."""

    enabled: bool = False
    target_store: str = "cosmos"
    dictionary_path: Path = DEFAULT_DAKP_DICTIONARY_PATH
    zstd_level: int = 0
    sample_size: int = 1024
    sample_seed: int = 0
    concurrency: int = 8
    threads: int = 0
    engine: str = "chdb"
    progress: bool = True


def automation_settings() -> LinkoutsAutomationSettings:
    dictionary = os.environ.get("LINKOUTS_DAKP_DICTIONARY_PATH")
    return LinkoutsAutomationSettings(
        enabled=_env_bool("LINKOUTS_DAKP_INGEST_ENABLED"),
        target_store=os.environ.get("LINKOUTS_DAKP_TARGET_STORE", "cosmos"),
        dictionary_path=(
            Path(dictionary).expanduser() if dictionary else DEFAULT_DAKP_DICTIONARY_PATH
        ),
        zstd_level=_env_int("LINKOUTS_DAKP_ZSTD_LEVEL", 0),
        sample_size=_env_int("LINKOUTS_DAKP_SAMPLE_SIZE", 1024),
        sample_seed=_env_int("LINKOUTS_DAKP_SAMPLE_SEED", 0),
        concurrency=_env_int("LINKOUTS_DAKP_CONCURRENCY", 8),
        threads=_env_int("LINKOUTS_DAKP_THREADS", 0),
        engine=os.environ.get("LINKOUTS_DAKP_ENGINE", "chdb"),
        progress=_env_bool("LINKOUTS_DAKP_PROGRESS", True),
    )


def _metadata_value(metadata: dict[str, Any], key: str) -> Any:
    value = metadata.get(key)
    return getattr(value, "value", value)


def _required_text(metadata: dict[str, Any], key: str) -> str:
    value = _metadata_value(metadata, key)
    if not isinstance(value, str) or not value.strip():
        msg = f"hf_publish metadata is missing non-empty string {key!r}"
        raise ValueError(msg)
    return value


def _required_bool(metadata: dict[str, Any], key: str) -> bool:
    value = _metadata_value(metadata, key)
    if not isinstance(value, bool):
        msg = f"hf_publish metadata is missing boolean {key!r}"
        raise ValueError(msg)
    return value


def _nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def build_dakp_load_request(
    settings: LinkoutsAutomationSettings, metadata: dict[str, Any]
) -> dg.RunRequest | dg.SkipReason:
    """Turn one verified live hf_publish materialization into a deterministic run request."""
    version = _required_text(metadata, "version")
    if _required_bool(metadata, "dry_run"):
        return dg.SkipReason(f"hf_publish {version} was a dry run; no Linkouts load.")
    if not _required_bool(metadata, "verified"):
        return dg.SkipReason(f"hf_publish {version} was not verified; no Linkouts load.")

    key = _required_text(metadata, "linkouts_key")
    expected_key = f"infores:drugapprovals-kp-{version}"
    if key != expected_key:
        msg = f"hf_publish linkouts_key {key!r} does not match version {version!r}"
        raise ValueError(msg)
    nodes = Path(_required_text(metadata, "linkouts_nodes_path"))
    edges = Path(_required_text(metadata, "linkouts_edges_path"))
    if not _nonempty_file(nodes) or not _nonempty_file(edges):
        return dg.SkipReason(
            f"hf_publish {version} Linkouts KGX paths are not complete on this host yet"
        )
    if settings.target_store == "mem://":
        msg = "LINKOUTS_DAKP_TARGET_STORE must survive the worker process"
        raise ValueError(msg)
    if not _nonempty_file(settings.dictionary_path):
        msg = f"configured Linkouts DAKP dictionary is missing or empty: {settings.dictionary_path}"
        raise RuntimeError(msg)

    return dg.RunRequest(
        run_key=f"linkouts-dakp-release-{version}",
        run_config={
            "ops": {
                "load_release": {
                    "config": {
                        "key": key,
                        "nodes_path": str(nodes),
                        "edges_path": str(edges),
                        "dictionary_path": str(settings.dictionary_path),
                        "target_store": settings.target_store,
                        "zstd_level": settings.zstd_level,
                        "sample_size": settings.sample_size,
                        "sample_seed": settings.sample_seed,
                        "concurrency": settings.concurrency,
                        "threads": settings.threads,
                        "engine": settings.engine,
                        "progress": settings.progress,
                    }
                }
            }
        },
        tags={"linkouts/dakp_release": version, "linkouts/ingest": "dakp"},
    )


def dakp_release_tick(
    context: SensorEvaluationContext | None,
    event: Any,
    settings: LinkoutsAutomationSettings,
) -> dg.RunRequest | dg.SkipReason:
    if not settings.enabled:
        return dg.SkipReason("Linkouts DAKP ingest is disabled.")
    materialization = event.dagster_event.event_specific_data.materialization
    return build_dakp_load_request(settings, dict(materialization.metadata))


def build_dakp_release_sensor(
    settings: LinkoutsAutomationSettings,
) -> dg.SensorDefinition:
    default_status = (
        dg.DefaultSensorStatus.RUNNING if settings.enabled else dg.DefaultSensorStatus.STOPPED
    )

    @dg.asset_sensor(
        name="linkouts_dakp_release",
        asset_key=dg.AssetKey("hf_publish"),
        job=linkouts_load_release,
        minimum_interval_seconds=300,
        default_status=default_status,
    )
    def linkouts_dakp_release_sensor(
        context: SensorEvaluationContext, event: dg.EventLogEntry
    ) -> dg.RunRequest | dg.SkipReason:
        return dakp_release_tick(context, event, settings)

    return linkouts_dakp_release_sensor


_module_settings = automation_settings()
dakp_release_sensor = build_dakp_release_sensor(_module_settings)
automation_sensors = [dakp_release_sensor]

__all__ = [
    "DEFAULT_DAKP_DICTIONARY_PATH",
    "LinkoutsAutomationSettings",
    "automation_sensors",
    "automation_settings",
    "build_dakp_load_request",
    "build_dakp_release_sensor",
    "dakp_release_sensor",
    "dakp_release_tick",
]

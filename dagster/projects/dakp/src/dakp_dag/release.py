"""Automated DAKP release assets and jobs.

Release-check acquisition is deliberately separate from the manual ``acquire_*``
asset keys: its assets depend on preflight, while narrow/manual builds retain the
original graph. Both paths call the same ``_run_acquisition`` implementation and
publish the same per-stage refs consumed by downstream extraction assets.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import dagster as dg
from dagster import AssetExecutionContext
from dakp_pipeline.ner.mention_cache import nercache_health as check_nercache_health
from dakp_pipeline.paths import Workdir
from dakp_pipeline.release.card import (
    build_dataset_card,
    compute_kg_stats,
    stage_release_files,
)
from dakp_pipeline.release.delta import compute_delta, load_pending_delta, write_pending_delta
from dakp_pipeline.release.gpu import find_dakp_gpu_processes, probe_gpus
from dakp_pipeline.release.hf_publish import publish_release
from dakp_pipeline.release.preflight import run_preflight
from dakp_pipeline.release.state import (
    ReleaseState,
    SourceFingerprint,
    load_state,
    state_path,
    utc_now_iso,
    write_json_atomic,
    write_state,
)
from dakp_pipeline.release.verify import ReleaseVerification, verify_release

from dakp_dag import assets as core_assets
from dakp_dag.assets import SOURCES, Source, _run_acquisition
from dakp_dag.config import release_settings, run_config_for
from dakp_dag.refsio import load_refs

SOURCE_LABELS = {
    "dailymed": "DailyMed",
    "drugsfda": "Drugs@FDA",
    "faers": "FAERS",
    "ema": "EMA medicines",
    "ema_smpc": "EMA product information",
    "canada_vigilance": "Canada Vigilance",
    "icd10cm": "ICD-10-CM",
}


@dg.asset(group_name="dakp_release", tags={"dakp/release": "check"})
def release_preflight(context: AssetExecutionContext) -> None:
    settings = release_settings()
    config = run_config_for(context)
    report = run_preflight(
        config,
        repo_root=settings.repo_root,
        hf_repo_id=settings.hf_repo_id,
        github_repo=settings.github_repo,
        hf_token=settings.hf_token,
        github_token=settings.github_token,
        min_free_gb=settings.min_free_gb,
        nercache_health=(
            (lambda: check_nercache_health(config.workdir)) if settings.nercache_required else None
        ),
    )
    if report.git_sha:
        context.instance.add_run_tags(context.run.run_id, {"git/commit": report.git_sha})
    context.add_output_metadata(
        {
            "git_sha": report.git_sha,
            "hf_user": report.hf_user,
            "checks": dg.MetadataValue.json(report.checks),
        }
    )


def _release_acquire_asset(source: Source) -> dg.AssetsDefinition:
    @dg.asset(
        name=f"release_acquire_{source.key}",
        group_name="dakp_release",
        deps=[release_preflight],
        tags={"dakp/stage": f"acquire_{source.key}", "dakp/release": "check"},
    )
    def _asset(context: AssetExecutionContext) -> dg.MaterializeResult:
        return _run_acquisition(
            context, run_config_for(context), f"acquire_{source.key}", source.acquire
        )

    return _asset


release_acquisitions = [_release_acquire_asset(source) for source in SOURCES]


@dg.asset(
    group_name="dakp_release",
    deps=release_acquisitions,
    tags={"dakp/release": "check"},
)
def source_delta(context: AssetExecutionContext) -> None:
    config = run_config_for(context)
    current = {
        SOURCE_LABELS[source.key]: SourceFingerprint.from_refs(
            load_refs(config, f"acquire_{source.key}")
        )
        for source in SOURCES
    }
    previous = load_state(state_path(config.published_root))
    run_record = context.instance.get_run_record_by_id(context.run.run_id)
    check_started_at = (
        datetime.fromtimestamp(run_record.start_time, UTC).isoformat()
        if run_record is not None and run_record.start_time is not None
        else None
    )
    delta = compute_delta(
        current,
        previous,
        check_run_id=context.run.run_id,
        check_started_at=check_started_at,
    )
    pending_path = write_pending_delta(
        config.published_root, delta, check_run_id=context.run.run_id
    )
    context.add_output_metadata(
        {
            "changes": len(delta.changes),
            "changed_sources": [change.source for change in delta.changes],
            "message": delta.message,
            "previous_version": delta.previous_version,
            "pending_delta": str(pending_path),
            "delta": dg.MetadataValue.json(delta.to_dict()),
        }
    )
    if not delta.changed:
        context.log.info("No upstream source artifacts changed; no build, version bump, or upload.")


release_check_assets = [release_preflight, *release_acquisitions, source_delta]

dakp_release_check = dg.define_asset_job(
    name="dakp_release_check",
    selection=release_check_assets,
    tags={"dakp/release": "check"},
)

_RELEASE_BUILD_STAGES = {
    *(core_assets.extract_stage(source.key) for source in SOURCES),
    core_assets.acquire_ner_models.key.to_user_string(),
    *core_assets.SHAPE_STAGES,
    core_assets.TABLASSERT_CONFIGS,
    core_assets.KG_BUILD,
    core_assets.KG_QC,
}
release_build_core_assets = [
    asset for asset in core_assets.all_assets if asset.key.to_user_string() in _RELEASE_BUILD_STAGES
]


def _release_version(context: AssetExecutionContext, config) -> str:
    version = config.publication_version
    if not version:
        raise RuntimeError(
            "automated release builds require the dakp/publication_version run tag "
            "(or DAKP_PUBLICATION_VERSION for a manual release)"
        )
    return version


def _release_output_paths(config, version: str) -> tuple[Path, Path, Path]:
    return (
        config.kg_dir / core_assets.KG_NODES,
        config.kg_dir / core_assets.KG_EDGES,
        Workdir(config.workdir).kgx / f"DRUG_APPROVALS_KP_{version}.RIG.yaml",
    )


@dg.asset(
    group_name="dakp_release",
    deps=[core_assets.kg_qc],
    tags={"dakp/release": "build"},
)
def release_verify(context: AssetExecutionContext) -> None:
    config = run_config_for(context)
    version = _release_version(context, config)
    nodes, edges, rig = _release_output_paths(config, version)
    verification = verify_release(version=version, nodes_path=nodes, edges_path=edges, rig_path=rig)
    report_path = config.kg_dir / "release_verification.json"
    write_json_atomic(report_path, verification.to_dict())
    context.add_output_metadata(
        {"version": version, "ok": verification.ok, "report": str(report_path)}
    )


def _hf_api(settings):
    from huggingface_hub import HfApi

    return HfApi(token=settings.hf_token)


@dg.asset(
    group_name="dakp_release",
    deps=[release_verify],
    tags={"dakp/release": "build"},
)
def dataset_card(context: AssetExecutionContext) -> None:
    config = run_config_for(context)
    settings = release_settings()
    version = _release_version(context, config)
    delta = load_pending_delta(config.published_root)
    if not delta.changed:
        raise RuntimeError("dataset_card refuses to publish a card for an unchanged source delta")
    check_run_id = context.run.tags.get("dakp/check_run_id")
    if check_run_id and check_run_id != delta.check_run_id:
        raise RuntimeError(
            f"pending delta belongs to check run {delta.check_run_id}, not {check_run_id}"
        )
    nodes, edges, rig = _release_output_paths(config, version)
    verification_payload = json.loads(
        (config.kg_dir / "release_verification.json").read_text(encoding="utf-8")
    )
    verification = ReleaseVerification(**verification_payload)
    from huggingface_hub.errors import EntryNotFoundError

    try:
        previous_card_path = _hf_api(settings).hf_hub_download(
            repo_id=settings.hf_repo_id,
            filename="README.md",
            repo_type="dataset",
            revision="main",
        )
        previous_card = Path(previous_card_path).read_text(encoding="utf-8")
    except EntryNotFoundError:
        context.log.warning("No README.md on HF main; generating the card without prior rows.")
        previous_card = ""
    staging_root = config.published_root / "dakp" / "release" / "staging"
    files = stage_release_files(
        staging_root=staging_root,
        version=version,
        nodes_path=nodes,
        edges_path=edges,
        rig_path=rig,
    )
    result = build_dataset_card(
        version=version,
        files=files,
        stats=compute_kg_stats(nodes, edges),
        rig_path=rig,
        delta=delta,
        previous_card=previous_card,
        verification=verification,
    )
    card_path = staging_root / "README.md"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(result.markdown, encoding="utf-8")
    context.add_output_metadata(
        {
            "version": version,
            "nodes": result.stats.nodes,
            "edges": result.stats.edges,
            "message": delta.message,
            "card": str(card_path),
            "staging": str(staging_root / version),
        }
    )


release_output_assets = [release_verify, dataset_card]


@dg.asset(
    group_name="dakp_release",
    deps=[dataset_card],
    tags={"dakp/release": "build"},
)
def gpu_release_check(context: AssetExecutionContext) -> None:
    config = run_config_for(context)
    status = probe_gpus()
    leaks = find_dakp_gpu_processes(status, workdir=config.workdir)
    context.add_output_metadata(
        {
            "devices": dg.MetadataValue.json([device.__dict__ for device in status.devices]),
            "processes": dg.MetadataValue.json(list(status.processes)),
            "dakp_leaks": dg.MetadataValue.json(list(leaks)),
        }
    )
    if leaks:
        # The build is already verified; do not block publication over a lingering
        # process. Surface it loudly in metadata/logs for the operator instead.
        context.log.error(f"DAKP processes still hold GPU memory after the build: {leaks}")


@dg.asset(
    group_name="dakp_release",
    deps=[gpu_release_check],
    tags={"dakp/release": "build"},
)
def hf_publish(context: AssetExecutionContext) -> None:
    config = run_config_for(context)
    settings = release_settings()
    version = _release_version(context, config)
    delta = load_pending_delta(config.published_root)
    if not delta.changed:
        raise RuntimeError("hf_publish refuses an unchanged source delta")
    check_run_id = context.run.tags.get("dakp/check_run_id")
    if check_run_id and check_run_id != delta.check_run_id:
        raise RuntimeError(
            f"pending delta belongs to check run {delta.check_run_id}, not {check_run_id}"
        )
    qc = json.loads((config.kg_dir / core_assets.QC_REPORT).read_text(encoding="utf-8"))
    result = publish_release(
        _hf_api(settings),
        repo_id=settings.hf_repo_id,
        staging_root=config.published_root / "dakp" / "release" / "staging",
        version=version,
        change_message=delta.message,
        nodes=int(qc["nodes"]),
        edges=int(qc["edges"]),
        dry_run=settings.dry_run,
    )
    if not result.dry_run:
        write_state(
            state_path(config.published_root),
            ReleaseState(
                version=version,
                published_at=utc_now_iso(),
                sources=delta.current,
                kg={
                    "nodes": int(qc["nodes"]),
                    "edges": int(qc["edges"]),
                    "message": delta.message,
                    "verified": result.verified,
                },
            ),
        )
    context.add_output_metadata(
        {
            "version": version,
            "dry_run": result.dry_run,
            "verified": result.verified,
            "superseded": list(result.superseded),
            "actions": list(result.actions),
            "state_updated": not result.dry_run,
        }
    )


dakp_release_build = dg.define_asset_job(
    name="dakp_release_build",
    selection=[
        *release_build_core_assets,
        *release_output_assets,
        gpu_release_check,
        hf_publish,
    ],
    executor_def=dg.in_process_executor,
    tags={"dakp/release": "build"},
)

__all__ = [
    "SOURCE_LABELS",
    "dakp_release_build",
    "dakp_release_check",
    "dataset_card",
    "gpu_release_check",
    "hf_publish",
    "release_acquisitions",
    "release_check_assets",
    "release_output_assets",
    "release_preflight",
    "release_verify",
    "source_delta",
]

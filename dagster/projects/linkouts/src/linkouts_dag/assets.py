"""Linkouts asset graph and narrow manual jobs.

Stage-to-stage data crosses only published ArtifactRef JSON. ``deps`` expresses lineage; it
never passes an in-memory value or forces an upstream rebuild in a narrow job.
"""

from pathlib import Path

import dagster as dg
from dagster import AssetExecutionContext
from linkouts_pipeline.operations import PushConfig, StageConfig
from linkouts_pipeline.refs import ArtifactRef, load_refs, publish_refs
from linkouts_pipeline.staging import (
    REPORT_MEDIA_TYPE,
    STAGE_MEDIA_TYPE,
    PublicationReport,
    StageManifest,
)
from mo_dagster import pipes as mo_pipes

from linkouts_dag.config import run_config
from linkouts_dag.execution import invoke, required_path
from linkouts_dag.run_configs import PushRunConfig, StageRunConfig
from linkouts_dag.stages import PUSH_STAGED_RELEASE, STAGE_RELEASE
from linkouts_dag.supplemental import (
    destructive_assets,
    supplemental_assets,
    upload_verification_assets,
)


@dg.asset(
    name=STAGE_RELEASE,
    description="Join one KGX release into a content-fingerprinted, resumable local store.",
)
def stage_release(context: AssetExecutionContext, config: StageRunConfig) -> dg.MaterializeResult:
    runtime = run_config()
    operation = StageConfig(
        **config.model_dump(),
        workdir=runtime.workdir,
    ).validate_for_publication()
    metadata = invoke(context, runtime, operation)
    path = required_path(metadata, "manifest_path")
    manifest = StageManifest.read(path).validate_for_publication()
    if manifest.key != operation.key:
        msg = f"worker staged {manifest.key!r}, expected {operation.key!r}"
        raise ValueError(msg)
    if (reported := metadata.get("staging_digest")) and reported != manifest.digest:
        msg = "worker staging digest does not match its manifest"
        raise ValueError(msg)
    refs = [ArtifactRef.for_file(path, media_type=STAGE_MEDIA_TYPE)]
    published = publish_refs(runtime.published_root, STAGE_RELEASE, refs)
    context.log.info(f"stage_release: published {published}")
    return dg.MaterializeResult(
        metadata={
            **metadata,
            "stage": STAGE_RELEASE,
            "manifest_path": str(path),
            "refs_path": str(published),
            "refs": 1,
            "rows": manifest.edges,
        }
    )


@dg.asset(
    name=PUSH_STAGED_RELEASE,
    deps=[STAGE_RELEASE],
    description="Publish a validated staged store to Cosmos or a persistent file backend.",
)
def push_staged_release(
    context: AssetExecutionContext, config: PushRunConfig
) -> dg.MaterializeResult:
    runtime = run_config()
    refs = load_refs(
        runtime.published_root,
        STAGE_RELEASE,
        producer=STAGE_RELEASE,
    )
    if len(refs) != 1 or refs[0].media_type != STAGE_MEDIA_TYPE:
        msg = "stage_release must publish exactly one staging manifest ref"
        raise ValueError(msg)
    manifest = StageManifest.read(Path(refs[0].uri)).validate_for_publication()
    if config.expected_key is not None and manifest.key != config.expected_key:
        msg = f"staged release is {manifest.key!r}, expected {config.expected_key!r}"
        raise ValueError(msg)
    runtime.run_dir.mkdir(parents=True, exist_ok=True)
    report_path = runtime.run_dir / f"{context.run.run_id}.publication.json"
    operation = PushConfig(
        **config.model_dump(),
        manifest_path=Path(refs[0].uri),
        report_path=report_path,
        dry_run=False,
    )
    metadata = invoke(context, runtime, operation)
    if not report_path.is_file():
        msg = f"publication worker exited without writing {report_path}"
        raise RuntimeError(msg)
    report = PublicationReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    ).validate_complete()
    if report.source_digest != manifest.digest or report.key != manifest.key:
        msg = "publication report does not match the staged manifest"
        raise ValueError(msg)
    if report.target_store != operation.target_store:
        msg = "publication report target does not match the requested target"
        raise ValueError(msg)
    if operation.target_store.startswith("file:") and (
        report.output_path is None or not report.output_path.is_file()
    ):
        msg = "file publication did not produce its output store"
        raise RuntimeError(msg)
    published = publish_refs(
        runtime.published_root,
        PUSH_STAGED_RELEASE,
        [ArtifactRef.for_file(report_path, media_type=REPORT_MEDIA_TYPE)],
    )
    context.log.info(f"push_staged_release: published {published}")
    return dg.MaterializeResult(
        metadata={
            **metadata,
            "stage": PUSH_STAGED_RELEASE,
            "report_path": str(report_path),
            "refs_path": str(published),
            "refs": 1,
            "rows": report.read,
            "target_store": report.target_store,
        }
    )


upload_assets: list[dg.AssetsDefinition] = [
    stage_release,
    push_staged_release,
    *upload_verification_assets,
]
all_assets: list[dg.AssetsDefinition] = [*upload_assets, *supplemental_assets]

linkouts_upload = dg.define_asset_job(
    "linkouts_upload",
    selection=dg.AssetSelection.assets(*upload_assets),
    description="Stage and publish one complete Linkouts release. Launch manually with typed config.",
)


def _single_output_job(asset: dg.AssetsDefinition) -> dg.JobDefinition:
    stage = asset.key.to_user_string()
    return dg.define_asset_job(
        f"linkouts_{stage}",
        selection=dg.AssetSelection.assets(asset),
        description=(
            f"Build only {stage!r}; upstream artifacts are read from published refs, "
            "never rebuilt implicitly"
        ),
        tags={"linkouts/stage": stage},
    )


single_output_jobs: list[dg.JobDefinition] = [_single_output_job(asset) for asset in all_assets]
linkouts_purge = dg.define_asset_job(
    "linkouts_purge",
    selection=dg.AssetSelection.assets(*destructive_assets),
    description="Opt-in purge requiring exact target confirmation; excluded from upload.",
)
all_jobs: list[dg.JobDefinition] = [linkouts_upload, *single_output_jobs]

resources: dict[str, object] = {
    "pipes_client": mo_pipes.subprocess_client_resource(),
}

__all__ = [
    "PUSH_STAGED_RELEASE",
    "STAGE_RELEASE",
    "PushRunConfig",
    "StageRunConfig",
    "all_assets",
    "all_jobs",
    "destructive_assets",
    "linkouts_purge",
    "resources",
    "upload_assets",
]

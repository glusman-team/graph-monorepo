"""Verification, dry-run, inspection, dictionary, and opt-in admin assets."""

from pathlib import Path

import dagster as dg
from dagster import AssetExecutionContext
from linkouts_pipeline.operations import (
    GetConfig,
    InitConfig,
    ProbeConfig,
    PurgeConfig,
    PushConfig,
    RigConfig,
    StageConfig,
    StatusConfig,
    TrainDictConfig,
)
from linkouts_pipeline.refs import ArtifactRef, load_refs
from linkouts_pipeline.staging import (
    REPORT_MEDIA_TYPE,
    STAGE_MEDIA_TYPE,
    PublicationReport,
    StageManifest,
)

from linkouts_dag.config import run_config
from linkouts_dag.execution import (
    complete_asset,
    invoke,
    required_path,
    run_output_operation,
    run_output_path,
    selected_store,
)
from linkouts_dag.run_configs import PushRunConfig, ValidationRunConfig
from linkouts_dag.stages import (
    DRY_RUN_PUBLICATION,
    INIT_STORE,
    INSPECT_EDGE,
    INSPECT_KGX,
    PROBE_RELEASE,
    PURGE_STORE,
    PUSH_STAGED_RELEASE,
    STAGE_RELEASE,
    STATUS_REPORT,
    TRAIN_DICTIONARY,
    VALIDATE_RELEASE,
)


class StatusRunConfig(dg.Config):
    store: str | None = None
    dictionary_path: str | None = None
    check_pools: bool = True


class ProbeRunConfig(dg.Config):
    store: str | None = None
    dictionary_path: str | None = None
    count: int = 20
    seed: int | None = None
    max_mean_ru: float | None = None
    max_worst_ru: float | None = None


class TrainDictRunConfig(dg.Config):
    nodes_path: str
    edges_path: str
    samples: int = 4096
    threads: int = 0
    engine: str = "chdb"


class GetRunConfig(dg.Config):
    edge_id: str
    store: str = "cosmos"
    dictionary_path: str | None = None
    version_key: str | None = None
    list_versions: bool = False
    raw: bool = False


class RigRunConfig(dg.Config):
    edges_path: str
    limit: int = 20000
    predicate: str | None = None
    emit_config: bool = False


class InitRunConfig(dg.Config):
    store: str = "cosmos"


class PurgeRunConfig(dg.Config):
    store: str = "cosmos"
    scope: str = "all"
    kg: str | None = None
    key: str | None = None
    dictionary_path: str | None = None
    dry_run: bool = True
    confirmation: str | None = None


@dg.asset(name=STATUS_REPORT, deps=[PUSH_STAGED_RELEASE])
def status_report(context: AssetExecutionContext, config: StatusRunConfig) -> dg.MaterializeResult:
    runtime = run_config()
    output = run_output_path(runtime, context, "status.json")
    operation = StatusConfig(
        **config.model_dump(exclude={"store"}),
        store=selected_store(runtime, config.store, PUSH_STAGED_RELEASE),
        output_path=output,
    )
    return run_output_operation(context, STATUS_REPORT, operation, output)


@dg.asset(name=PROBE_RELEASE, deps=[PUSH_STAGED_RELEASE])
def probe_release(context: AssetExecutionContext, config: ProbeRunConfig) -> dg.MaterializeResult:
    runtime = run_config()
    output = run_output_path(runtime, context, "probe.json")
    operation = ProbeConfig(
        **config.model_dump(exclude={"store"}),
        store=selected_store(runtime, config.store, PUSH_STAGED_RELEASE),
        output_path=output,
    )
    return run_output_operation(context, PROBE_RELEASE, operation, output)


@dg.asset(name=VALIDATE_RELEASE, deps=[STAGE_RELEASE])
def validate_release(
    context: AssetExecutionContext, config: ValidationRunConfig
) -> dg.MaterializeResult:
    runtime = run_config()
    operation = StageConfig(**config.model_dump(), workdir=runtime.workdir, dry_run=True)
    metadata = invoke(context, runtime, operation)
    path = required_path(metadata, "manifest_path")
    manifest = StageManifest.read(path)
    if not manifest.complete or not manifest.filtered:
        msg = "validation must complete into a diagnostic-only manifest"
        raise RuntimeError(msg)
    ref = ArtifactRef.for_file(path, media_type=STAGE_MEDIA_TYPE)
    return complete_asset(context, runtime, VALIDATE_RELEASE, [ref], metadata, rows=manifest.edges)


@dg.asset(name=DRY_RUN_PUBLICATION, deps=[STAGE_RELEASE])
def dry_run_publication(
    context: AssetExecutionContext, config: PushRunConfig
) -> dg.MaterializeResult:
    runtime = run_config()
    refs = load_refs(runtime.published_root, STAGE_RELEASE, producer=STAGE_RELEASE)
    if len(refs) != 1:
        msg = "stage_release must publish exactly one staging manifest ref"
        raise ValueError(msg)
    manifest = StageManifest.read(Path(refs[0].uri)).validate_for_publication()
    if config.expected_key is not None and manifest.key != config.expected_key:
        msg = f"staged release is {manifest.key!r}, expected {config.expected_key!r}"
        raise ValueError(msg)
    output = run_output_path(runtime, context, "publication-dry-run.json")
    operation = PushConfig(
        **config.model_dump(),
        manifest_path=Path(refs[0].uri),
        report_path=output,
        dry_run=True,
    )
    metadata = invoke(context, runtime, operation)
    report = PublicationReport.model_validate_json(output.read_text(encoding="utf-8"))
    if not report.dry_run or report.output_path is not None:
        msg = "dry-run publication wrote a persistent target"
        raise RuntimeError(msg)
    ref = ArtifactRef.for_file(output, media_type=REPORT_MEDIA_TYPE)
    return complete_asset(context, runtime, DRY_RUN_PUBLICATION, [ref], metadata, rows=report.read)


@dg.asset(name=TRAIN_DICTIONARY)
def train_dictionary(
    context: AssetExecutionContext, config: TrainDictRunConfig
) -> dg.MaterializeResult:
    runtime = run_config()
    output = run_output_path(runtime, context, "zstd.dict")
    report = run_output_path(runtime, context, "dictionary-report.json")
    operation = TrainDictConfig(**config.model_dump(), output_path=output, report_path=report)
    metadata = invoke(context, runtime, operation)
    for path in (output, report):
        if not path.is_file():
            msg = f"dictionary worker exited without writing {path}"
            raise RuntimeError(msg)
    rows = int(metadata.get("rows", 0))
    refs = [
        ArtifactRef.for_file(output, media_type="application/zstd-dictionary"),
        ArtifactRef.for_file(report, media_type=REPORT_MEDIA_TYPE),
    ]
    return complete_asset(context, runtime, TRAIN_DICTIONARY, refs, metadata, rows=rows)


@dg.asset(name=INSPECT_KGX)
def inspect_kgx(context: AssetExecutionContext, config: RigRunConfig) -> dg.MaterializeResult:
    runtime = run_config()
    suffix = "display-starter.exs" if config.emit_config else "kgx-report.txt"
    output = run_output_path(runtime, context, suffix)
    operation = RigConfig(**config.model_dump(), output_path=output)
    return run_output_operation(context, INSPECT_KGX, operation, output, media_type="text/plain")


@dg.asset(name=INSPECT_EDGE)
def inspect_edge(context: AssetExecutionContext, config: GetRunConfig) -> dg.MaterializeResult:
    runtime = run_config()
    output = run_output_path(runtime, context, "edge-inspection.json")
    operation = GetConfig(**config.model_dump(), output_path=output)
    return run_output_operation(context, INSPECT_EDGE, operation, output)


@dg.asset(name=INIT_STORE)
def init_store(context: AssetExecutionContext, config: InitRunConfig) -> dg.MaterializeResult:
    runtime = run_config()
    output = run_output_path(runtime, context, "provision.json")
    operation = InitConfig(**config.model_dump(), output_path=output)
    return run_output_operation(context, INIT_STORE, operation, output)


@dg.asset(name=PURGE_STORE)
def purge_store(context: AssetExecutionContext, config: PurgeRunConfig) -> dg.MaterializeResult:
    runtime = run_config()
    if not runtime.destructive_jobs_enabled:
        msg = "purge_store requires LINKOUTS_ENABLE_DESTRUCTIVE_JOBS=1"
        raise RuntimeError(msg)
    output = run_output_path(runtime, context, "purge.json")
    operation = PurgeConfig(**config.model_dump(), output_path=output)
    return run_output_operation(context, PURGE_STORE, operation, output)


supplemental_assets: list[dg.AssetsDefinition] = [
    validate_release,
    dry_run_publication,
    train_dictionary,
    inspect_kgx,
    inspect_edge,
    init_store,
]
upload_verification_assets: list[dg.AssetsDefinition] = [status_report, probe_release]
destructive_assets: list[dg.AssetsDefinition] = [purge_store]

__all__ = ["destructive_assets", "supplemental_assets", "upload_verification_assets"]

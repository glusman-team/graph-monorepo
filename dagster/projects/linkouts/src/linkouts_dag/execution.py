"""Shared Pipes invocation and published-artifact plumbing."""

from pathlib import Path
from typing import Any

import dagster as dg
from dagster import AssetExecutionContext
from linkouts_pipeline.operations import OperationConfig, WorkerRequest
from linkouts_pipeline.refs import ArtifactRef, load_refs, publish_refs
from linkouts_pipeline.staging import PublicationReport
from mo_dagster import pipes as mo_pipes

from linkouts_dag.config import RuntimeConfig, run_config


def _plain_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: getattr(value, "value", value) for key, value in metadata.items()}


def invoke(
    context: AssetExecutionContext,
    runtime: RuntimeConfig,
    config: OperationConfig,
) -> dict[str, Any]:
    request = WorkerRequest.for_config(config)
    invocation = mo_pipes.launch_worker(
        context,
        str(runtime.worker_bin),
        extras=request.extras(),
    )
    result = invocation.get_materialize_result()
    return _plain_metadata(dict(result.metadata or {}))


def required_path(metadata: dict[str, Any], key: str) -> Path:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        msg = f"worker result is missing string metadata {key!r}"
        raise RuntimeError(msg)
    return Path(value)


def run_output_path(runtime: RuntimeConfig, context: AssetExecutionContext, suffix: str) -> Path:
    runtime.run_dir.mkdir(parents=True, exist_ok=True)
    return runtime.run_dir / f"{context.run.run_id}.{suffix}"


def complete_asset(
    context: AssetExecutionContext,
    runtime: RuntimeConfig,
    stage: str,
    refs: list[ArtifactRef],
    metadata: dict[str, Any],
    *,
    rows: int,
) -> dg.MaterializeResult:
    published = publish_refs(runtime.published_root, stage, refs)
    context.log.info(f"{stage}: published {published}")
    return dg.MaterializeResult(
        metadata={
            **metadata,
            "stage": stage,
            "refs_path": str(published),
            "refs": len(refs),
            "rows": rows,
        }
    )


def run_output_operation(
    context: AssetExecutionContext,
    stage: str,
    operation: OperationConfig,
    output_path: Path,
    *,
    media_type: str = "application/json",
) -> dg.MaterializeResult:
    runtime = run_config()
    metadata = invoke(context, runtime, operation)
    if not output_path.is_file():
        msg = f"{operation.operation} worker exited without writing {output_path}"
        raise RuntimeError(msg)
    rows = int(metadata.get("rows", 0))
    ref = ArtifactRef.for_file(output_path, media_type=media_type, rows=rows)
    return complete_asset(context, runtime, stage, [ref], metadata, rows=rows)


def published_report(runtime: RuntimeConfig, producer: str) -> PublicationReport:
    refs = load_refs(runtime.published_root, producer, producer=producer)
    if len(refs) != 1:
        msg = f"{producer} must publish exactly one report ref"
        raise ValueError(msg)
    return PublicationReport.model_validate_json(
        Path(refs[0].uri).read_text(encoding="utf-8")
    ).validate_complete()


def selected_store(runtime: RuntimeConfig, override: str | None, producer: str) -> str:
    return override if override is not None else published_report(runtime, producer).target_store


__all__ = [
    "complete_asset",
    "invoke",
    "published_report",
    "required_path",
    "run_output_operation",
    "run_output_path",
    "selected_store",
]

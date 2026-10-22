"""The DAKP asset graph (port of the Airflow ``dakp_build`` DAG).

One asset per DAKP stage, one published artifact per stage, lineage-only deps. Acquisition
and extraction assets are generated from the :data:`SOURCES` table, so a new source is one
table entry; the shaping assets iterate the pipeline's edge-family registry, so a new edge
predicate type is one registry entry plus one thin asset.

Data plane (two layers, both content-addressed):
  - the DAKP workdir (``$DAKP_WORKDIR``): BLAKE3 CAS, interim parquet, tabular TSVs,
    manifests and the operation index (``manifests/_index.json``). The op index is the
    primary cache: identical inputs + config fingerprint => the stage's outputs are reused
    without re-running. It works across dagster restarts and across the Python/Go boundary
    because both sides write the same index.
  - the published dir (``$MO_PUBLISHED_ROOT/dakp``): each stage's ArtifactRef handoff
    (``refs/<stage>.refs.json``) plus the KG build's KGX ndjson pair and QC report.
    Downstream stages READ these instead of rebuilding them, which is what makes a narrow
    single-output job possible: ``deps=[...]`` is lineage only, never a value input.

Trigger surfaces:
  - ``dakp_build``         -> every stage, every output
  - ``dakp_build_<stage>`` -> exactly one output, none of the others
Both are generated from :data:`all_assets`, so a new stage gets its narrow job for free.
Headless equivalent: ``dagster asset materialize --select <stage>`` (quote
``--select '*+<stage>'`` to pull the upstream chain into the same run).
"""

import importlib
import json
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import dagster as dg
from dagster import AssetExecutionContext
from mo_dagster import pipes as mo_pipes
from mo_kg import kgx

from dakp_dag import refsio
from dakp_dag.config import RunConfig, run_config, task_context

# --- source table (the extension seam for new sources) -------------------------------


@dataclass(frozen=True)
class Source:
    """One upstream data source: how it is acquired and how it is extracted.

    ``extractor`` is ``"go:<stage>"`` (the Dagster Pipes worker in ``workers/go`` for the
    heavy parses: DailyMed SPL zips, FAERS ASCII quarters, Drugs@FDA tables) or
    ``"py:<module>"`` (an in-process extractor module under ``dakp_pipeline.extract``).
    """

    key: str
    acquire: str
    extractor: str
    doc: str


SOURCES: tuple[Source, ...] = (
    Source(
        key="dailymed",
        acquire="acquire_dailymed",
        extractor="go:dailymed",
        doc="DailyMed SPL full-release zips (release index, then per-release parts).",
    ),
    Source(
        key="faers",
        acquire="acquire_faers",
        extractor="go:faers",
        doc="FDA FAERS ASCII quarterly archives (drug, indi, therapy, demo).",
    ),
    Source(
        key="drugsfda",
        acquire="acquire_drugsfda",
        extractor="go:drugsfda",
        doc="Drugs@FDA application and product tables (the FDA application register).",
    ),
    Source(
        key="ema",
        acquire="acquire_ema",
        extractor="py:ema_registry",
        doc="EMA medicines registry xlsx (the EU product register).",
    ),
    Source(
        key="ema_smpc",
        acquire="acquire_ema_smpc",
        extractor="py:ema_smpc",
        doc="EMA SmPC/EPAR product-information PDFs (section-cut EU labels).",
    ),
    Source(
        key="canada_vigilance",
        acquire="acquire_canada_vigilance",
        extractor="py:canada_vigilance",
        doc="Health Canada Vigilance adverse-reaction extract (observed-use evidence).",
    ),
)

SOURCE_BY_KEY: dict[str, Source] = {source.key: source for source in SOURCES}

ACQUIRE_NER_MODELS = "acquire_ner_models"
NER_TRAINING_EXPORT = "ner_training_export"
TABLASSERT_CONFIGS = "tablassert_configs"
KG_BUILD = "kg_build"
KG_QC = "kg_qc"
LEGACY_TSV_EXPORT = "legacy_tsv_export"

KG_NODES = "kg.nodes.ndjson"
KG_EDGES = "kg.edges.ndjson"
QC_REPORT = "qc.json"


def acquire_stage(key: str) -> str:
    """Asset/stage name of a source's acquisition stage."""
    return f"acquire_{key}"


def extract_stage(key: str) -> str:
    """Asset/stage name of a source's extraction stage."""
    return f"extract_{key}"


# --- shared helpers ------------------------------------------------------------------


@contextmanager
def _stage_logs(context: AssetExecutionContext) -> Iterator[None]:
    """Bridge the pipeline's loguru narration into the dagster step log.

    Without this bridge the pipeline's per-line stats never reach the UI. The sink is
    removed on exit so repeated asset runs do not stack sinks on the global logger.
    """
    from loguru import logger as pipeline_logger

    sink_id = pipeline_logger.add(
        lambda message: context.log.info(message.record["message"]),
        level="INFO",
    )
    try:
        yield
    finally:
        pipeline_logger.remove(sink_id)


def _load(config: RunConfig, stages: Sequence[str]) -> list:
    """Concatenate the published refs of several upstream stages (loud on a missing one)."""
    return [ref for stage in stages for ref in refsio.load_refs(config, stage)]


def _ner(profile: str | None, config: RunConfig, ctx):
    """Build the production NER backend for a family profile (None => family needs none).

    ``DAKP_NER_OFFLINE=1`` selects the curated-gazetteer-only backend, which keeps mining
    deterministic and torch-free (the CI/smoke path); the default is the composite
    gazetteer + GLiNER2 recall backend using the checkpoints ``acquire_ner_models`` cached.
    """
    if profile is None:
        return None
    from dakp_pipeline.ner.ner import DiseaseNER

    builders = {
        "indications": DiseaseNER.for_indications,
        "contraindications": DiseaseNER.for_contraindications,
    }
    try:
        build = builders[profile]
    except KeyError:
        msg = f"unknown NER profile {profile!r} (expected one of {sorted(builders)})"
        raise ValueError(msg) from None
    return build(offline=config.ner_offline, workdir=ctx.workdir)


def _with_ner(ctx, ner):
    """Return a TaskContext whose params carry the injected NER backend."""
    from dakp_pipeline.io.contracts import TaskContext

    return TaskContext(
        workdir=ctx.workdir, fixture_root=ctx.fixture_root, params={**ctx.params, "ner": ner}
    )


def _run_acquisition(
    context: AssetExecutionContext, config: RunConfig, stage: str, fn_name: str
) -> dg.MaterializeResult:
    """Run one acquisition function from ``dakp_pipeline.acquire`` and publish its refs."""
    acquire = importlib.import_module("dakp_pipeline.acquire")
    ctx = task_context(config)
    with _stage_logs(context):
        refs = getattr(acquire, fn_name)(ctx)
    path = refsio.publish_refs(config, stage, refs)
    context.log.info(f"{stage}: published {len(refs)} refs -> {path}")
    return dg.MaterializeResult(
        metadata={**refsio.refs_metadata(refs), "stage": stage, "path": str(path)}
    )


def _run_go_extract(
    context: AssetExecutionContext,
    config: RunConfig,
    stage: str,
    worker_stage: str,
    inputs: list,
) -> dg.MaterializeResult:
    """Run one Go extraction stage as a Dagster Pipes subprocess.

    ``stage`` is the dag asset/stage name that owns the published refs; ``worker_stage`` is
    the name the Go worker dispatches on (its own stage vocabulary). The refs-file protocol
    is the Python/Go contract: input refs are written to a JSON file the worker reads, output
    refs are written by the worker and published here. The worker reports its own cache
    metadata (cache_key/cache_hit/rows); the published-refs counts are merged on top so one
    materialization shows both.
    """
    from dakp_pipeline.io.refs import refs_from_file, refs_to_dicts

    if not config.worker_bin.exists():
        msg = (
            f"extraction worker not built: {config.worker_bin} "
            "(run `go build -o bin/dakp-extract ./cmd/dakp-extract` inside workers/go)"
        )
        raise FileNotFoundError(msg)

    # Scratch protocol files live in the WORKDIR: the published dir holds published
    # artifacts only, so a crashed worker leaves nothing behind there.
    pipes_dir = config.workdir / "pipes"
    pipes_dir.mkdir(parents=True, exist_ok=True)
    input_path = pipes_dir / f"{stage}.inputs.json"
    output_path = pipes_dir / f"{stage}.outputs.json"
    output_path.unlink(missing_ok=True)
    input_path.write_text(json.dumps(refs_to_dicts(inputs), indent=2) + "\n", encoding="utf-8")

    extras: dict[str, object] = {
        "stage": worker_stage,
        "input_refs": str(input_path),
        "output_refs": str(output_path),
        "workdir": str(config.workdir),
    }
    if config.force:
        extras["force"] = True
    if config.threads:
        extras["threads"] = config.threads
    if config.memory_budget_gb:
        extras["memory_budget_gb"] = config.memory_budget_gb
    if config.quarter_limit:
        extras["quarter_limit"] = config.quarter_limit

    invocation = mo_pipes.launch_worker(context, str(config.worker_bin), extras=extras)
    worker_result = invocation.get_materialize_result()

    if not output_path.exists():
        msg = f"extraction worker exited without writing {output_path}"
        raise RuntimeError(msg)
    out_refs = refs_from_file(output_path)
    refsio.publish_refs(config, stage, out_refs)
    metadata = dict(worker_result.metadata or {})
    metadata.update(refsio.refs_metadata(out_refs))
    # The worker echoes its own stage vocabulary ("dailymed"); do not clobber it.
    metadata.setdefault("stage", stage)
    return dg.MaterializeResult(metadata=metadata)


def _run_py_extract(
    context: AssetExecutionContext,
    config: RunConfig,
    stage: str,
    module: str,
    inputs: list,
) -> dg.MaterializeResult:
    """Run one in-process Python extraction stage and publish its refs."""
    extractor = importlib.import_module(f"dakp_pipeline.extract.{module}").extract
    ctx = task_context(config)
    with _stage_logs(context):
        out_refs = extractor(inputs, ctx)
    path = refsio.publish_refs(config, stage, out_refs)
    context.log.info(f"{stage}: published {len(out_refs)} refs -> {path}")
    return dg.MaterializeResult(
        metadata={**refsio.refs_metadata(out_refs), "stage": stage, "path": str(path)}
    )


def _publish(
    config: RunConfig, context: AssetExecutionContext, stage: str, refs: list
) -> dg.MaterializeResult:
    """Publish a stage's refs and report the standard metadata for it."""
    path = refsio.publish_refs(config, stage, refs)
    context.log.info(f"{stage}: published {len(refs)} refs -> {path}")
    return dg.MaterializeResult(
        metadata={**refsio.refs_metadata(refs), "stage": stage, "path": str(path)}
    )


# --- acquisition + extraction assets (generated from SOURCES) ------------------------


def _make_acquire_asset(source: Source) -> dg.AssetsDefinition:
    stage = acquire_stage(source.key)

    @dg.asset(name=stage, description=f"Acquire {source.key}: {source.doc}")
    def _acquire_asset(context: AssetExecutionContext) -> dg.MaterializeResult:
        return _run_acquisition(context, run_config(), stage, source.acquire)

    return _acquire_asset


def _make_extract_asset(source: Source, acquire_asset: dg.AssetsDefinition) -> dg.AssetsDefinition:
    stage = extract_stage(source.key)
    acquire_name = acquire_stage(source.key)
    kind, _, target = (source.extractor or "").partition(":")

    @dg.asset(
        name=stage,
        deps=[acquire_asset],
        description=f"Extract {source.key} via the {kind} worker: {source.doc}",
    )
    def _extract_asset(context: AssetExecutionContext) -> dg.MaterializeResult:
        config = run_config()
        inputs = refsio.load_refs(config, acquire_name)
        if kind == "go":
            return _run_go_extract(context, config, stage, target, inputs)
        return _run_py_extract(context, config, stage, target, inputs)

    return _extract_asset


ACQUIRE_ASSETS: dict[str, dg.AssetsDefinition] = {
    source.key: _make_acquire_asset(source) for source in SOURCES
}
EXTRACT_ASSETS: dict[str, dg.AssetsDefinition] = {
    source.key: _make_extract_asset(source, ACQUIRE_ASSETS[source.key]) for source in SOURCES
}


@dg.asset(
    name=ACQUIRE_NER_MODELS,
    description=(
        "Cache the GLiNER2 NER checkpoints under the workdir. Ordering dependency for every "
        "mining stage (the models are loaded lazily from this cache, not passed as data)."
    ),
)
def acquire_ner_models(context: AssetExecutionContext) -> dg.MaterializeResult:
    return _run_acquisition(context, run_config(), ACQUIRE_NER_MODELS, ACQUIRE_NER_MODELS)


# --- shaping assets (edge-family registry driven) ------------------------------------


def _run_shape(
    context: AssetExecutionContext,
    *,
    stage: str,
    family_name: str,
    operation: str,
    input_stages: Sequence[str],
) -> dg.MaterializeResult:
    """Run one edge-family shaper, honoring the op-index already-done skip.

    ``stage`` is the dag asset name that owns the published refs; ``family_name`` keys the
    pipeline's edge-family registry (the two differ: the family is the assertion type, the
    stage is the DAG node that builds it).
    """
    from dakp_pipeline import families
    from dakp_pipeline.assertions.evidence import cached_shape_outputs

    config = run_config()
    family = families.get_family(family_name)
    in_refs = _load(config, input_stages)
    ctx = task_context(config)

    # The NER backend is part of the shape config fingerprint, so it must be injected
    # BEFORE the cache probe or a warm run can never match the cold run's key.
    ner = _ner(family.ner_profile, config, ctx)
    run_ctx = _with_ner(ctx, ner) if ner is not None else ctx

    cached = cached_shape_outputs(operation, in_refs, run_ctx)
    if cached is not None:
        refsio.publish_refs(config, stage, cached)
        context.log.info(f"{stage}: op-index hit, reused {len(cached)} cached refs")
        return dg.MaterializeResult(
            metadata={**refsio.refs_metadata(cached), "stage": stage, "cache_hit": True}
        )

    with _stage_logs(context):
        out_refs = family.shaper().transform(in_refs, run_ctx)
    result = _publish(config, context, stage, out_refs)
    return dg.MaterializeResult(
        metadata={
            **result.metadata,
            "cache_hit": False,
            "family": family.name,
            "table": family.table,
        }
    )


@dg.asset(
    name="shape_approved_treats",
    description="Shape FDA/EMA-approved treatment assertions (family: approved_treats).",
    deps=[
        EXTRACT_ASSETS["dailymed"],
        EXTRACT_ASSETS["drugsfda"],
        EXTRACT_ASSETS["faers"],
        EXTRACT_ASSETS["ema"],
        EXTRACT_ASSETS["ema_smpc"],
        acquire_ner_models,
    ],
)
def shape_approved_treats(context: AssetExecutionContext) -> dg.MaterializeResult:
    return _run_shape(
        context,
        stage="shape_approved_treats",
        family_name="approved_treats",
        operation="shape_approved_treats",
        input_stages=[
            extract_stage("dailymed"),
            extract_stage("drugsfda"),
            extract_stage("faers"),
            extract_stage("ema"),
            extract_stage("ema_smpc"),
        ],
    )


@dg.asset(
    name="shape_faers_uses",
    description=(
        "Shape FAERS + Canada Vigilance observed-use assertions (family: "
        "faers_applied_to_treat). Depends on the approved-treats table for the "
        "approval-status cross-reference; FAERS indication text bypasses NER."
    ),
    deps=[
        EXTRACT_ASSETS["faers"],
        EXTRACT_ASSETS["dailymed"],
        EXTRACT_ASSETS["drugsfda"],
        EXTRACT_ASSETS["canada_vigilance"],
        shape_approved_treats,
    ],
)
def shape_faers_uses(context: AssetExecutionContext) -> dg.MaterializeResult:
    return _run_shape(
        context,
        stage="shape_faers_uses",
        family_name="faers_applied_to_treat",
        operation="shape_faers_applied_to_treat",
        input_stages=[
            extract_stage("faers"),
            extract_stage("dailymed"),
            extract_stage("drugsfda"),
            "shape_approved_treats",
            extract_stage("canada_vigilance"),
        ],
    )


@dg.asset(
    name="shape_contraindications",
    description=(
        "Mine contraindication assertions from DailyMed SPL sections, Drugs@FDA labels and "
        "EMA SmPC sections (family: contraindications). Uses the lower acceptance point: a "
        "missed contraindication is more harmful than a retained low-confidence candidate."
    ),
    deps=[
        EXTRACT_ASSETS["dailymed"],
        EXTRACT_ASSETS["drugsfda"],
        EXTRACT_ASSETS["ema_smpc"],
        EXTRACT_ASSETS["ema"],
        acquire_ner_models,
    ],
)
def shape_contraindications(context: AssetExecutionContext) -> dg.MaterializeResult:
    return _run_shape(
        context,
        stage="shape_contraindications",
        family_name="contraindications",
        operation="shape_contraindications",
        input_stages=[
            extract_stage("dailymed"),
            extract_stage("drugsfda"),
            extract_stage("ema_smpc"),
            extract_stage("ema"),
        ],
    )


# --- NER training-data export --------------------------------------------------------


@dg.asset(
    name=NER_TRAINING_EXPORT,
    description=(
        "Export the GLiNER2 training-data bundle. Runs after shaping so the disease mention "
        "cache is warm and its re-extraction of the same section texts lands on cache hits. "
        "The shape dependency is ordering only; the extracts are the data inputs."
    ),
    deps=[
        EXTRACT_ASSETS["dailymed"],
        EXTRACT_ASSETS["faers"],
        EXTRACT_ASSETS["ema"],
        acquire_ner_models,
        shape_contraindications,
    ],
)
def ner_training_export(context: AssetExecutionContext) -> dg.MaterializeResult:
    from dakp_pipeline import ner_export

    config = run_config()
    in_refs = _load(
        config, [extract_stage("dailymed"), extract_stage("faers"), extract_stage("ema")]
    )
    ctx = task_context(config)
    ner = _ner("contraindications", config, ctx)
    run_ctx = _with_ner(ctx, ner) if ner is not None else ctx
    with _stage_logs(context):
        out_refs = ner_export.export(in_refs, run_ctx)
    return _publish(config, context, NER_TRAINING_EXPORT, out_refs)


# --- Tablassert handoff, KG build, QC, legacy export ---------------------------------

SHAPE_STAGES: tuple[str, ...] = (
    "shape_approved_treats",
    "shape_faers_uses",
    "shape_contraindications",
)


@dg.asset(
    name=TABLASSERT_CONFIGS,
    description="Generate the Tablassert graph and per-table YAML configs for the assertion tables.",
    deps=[shape_approved_treats, shape_faers_uses, shape_contraindications],
)
def tablassert_configs(context: AssetExecutionContext) -> dg.MaterializeResult:
    from dakp_pipeline import tablassert

    config = run_config()
    assertion_refs = _load(config, SHAPE_STAGES)
    ctx = task_context(config)
    with _stage_logs(context):
        out_refs = tablassert.generate(assertion_refs, ctx)
    return _publish(config, context, TABLASSERT_CONFIGS, out_refs)


def _kgx_pair(config: RunConfig) -> tuple[Path, Path]:
    """The published KGX ndjson pair paths (nodes, edges)."""
    return config.kg_dir / KG_NODES, config.kg_dir / KG_EDGES


@dg.asset(
    name=KG_BUILD,
    description=(
        "Build the KG with the installed Tablassert CLI and publish the KGX ndjson pair. "
        "Without a configured fullmap ($DAKP_FULLMAP) the handoff is deferred: no KGX is "
        "produced and downstream KG stages report the deferral instead of failing."
    ),
    deps=[shape_approved_treats, shape_faers_uses, shape_contraindications, tablassert_configs],
)
def kg_build(context: AssetExecutionContext) -> dg.MaterializeResult:
    from dakp_pipeline import tablassert
    from dakp_pipeline.paths import Workdir

    config = run_config()
    assertion_refs = _load(config, list(SHAPE_STAGES))
    config_refs = refsio.load_refs(config, TABLASSERT_CONFIGS)
    ctx = task_context(config)
    with _stage_logs(context):
        out_refs = tablassert.run(assertion_refs, config_refs, ctx)
    refsio.publish_refs(config, KG_BUILD, out_refs)

    # ``tablassert.run`` returns exactly one ref: the handoff report. Its ``mode`` decides
    # whether a KGX pair exists to publish (deferred => no build-kg ran => nothing to copy).
    report_refs = [ref for ref in out_refs if ref.uri.name == tablassert.REPORT_NAME]
    if len(report_refs) != 1:
        msg = (
            f"kg_build expected exactly one {tablassert.REPORT_NAME} among the handoff refs, "
            f"found {len(report_refs)}"
        )
        raise RuntimeError(msg)
    report = json.loads(report_refs[0].uri.read_text(encoding="utf-8"))
    if report.get("mode") == "deferred":
        context.log.warning(
            "kg_build: Tablassert handoff deferred (no fullmap configured); no KGX produced"
        )
        return dg.MaterializeResult(
            metadata={
                **refsio.refs_metadata(out_refs),
                "stage": KG_BUILD,
                "deferred": True,
                "reason": "no fullmap configured ($DAKP_FULLMAP)",
            }
        )

    stem = str(report.get("kgx_stem") or "")
    if not stem:
        msg = "kg_build: a non-deferred handoff report carries no kgx_stem; cannot locate the KGX pair"
        raise RuntimeError(msg)
    kgx_dir = Workdir(ctx.workdir).kgx
    sources = {"nodes": kgx_dir / f"{stem}.nodes.ndjson", "edges": kgx_dir / f"{stem}.edges.ndjson"}
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        msg = f"kg_build: the handoff reported stem {stem!r} but these KGX files are missing: {missing}"
        raise RuntimeError(msg)

    config.kg_dir.mkdir(parents=True, exist_ok=True)
    published = {
        kind: str(shutil.copyfile(src, config.kg_dir / name))
        for (kind, src), name in zip(sources.items(), (KG_NODES, KG_EDGES), strict=True)
    }
    report_path = config.kg_dir / tablassert.REPORT_NAME
    shutil.copyfile(report_refs[0].uri, report_path)

    context.log.info(f"kg_build: published KGX pair + report -> {config.kg_dir}")
    return dg.MaterializeResult(
        metadata={
            **refsio.refs_metadata(out_refs),
            "stage": KG_BUILD,
            "deferred": False,
            "kgx_stem": stem,
            "nodes_path": published["nodes"],
            "edges_path": published["edges"],
            "report": str(report_path),
        }
    )


@dg.asset(
    name=KG_QC,
    description=(
        "QC the published KGX pair: node/edge counts, predicate breakdown and dangling-edge "
        "check. A dangling edge fails the run (data integrity, not a warning). Reports the "
        "deferral when kg_build produced no KGX."
    ),
    deps=[kg_build],
)
def kg_qc(context: AssetExecutionContext) -> dg.MaterializeResult:
    config = run_config()
    # Distinguish "kg_build never ran" (loud) from "kg_build deferred" (reported): the
    # handoff refs exist in both real and deferred builds, the KGX pair only in a real one.
    refsio.load_refs(config, KG_BUILD, producer=KG_BUILD)
    nodes_path, edges_path = _kgx_pair(config)
    if not (nodes_path.exists() and edges_path.exists()):
        context.log.warning("kg_qc: no published KGX pair (deferred handoff); nothing to QC")
        return dg.MaterializeResult(
            metadata={"stage": KG_QC, "deferred": True, "reason": "no published KGX pair"}
        )

    report = kgx.qc_counts(nodes_path, edges_path)
    dangling = int(report.get("dangling_edges", 0))
    config.kg_dir.mkdir(parents=True, exist_ok=True)
    report_path = config.kg_dir / QC_REPORT
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    context.log.info(f"kg_qc: {report_path} -> {report}")
    if dangling:
        msg = f"kg_qc: {dangling} edges reference unknown nodes (see {report_path})"
        raise ValueError(msg)
    return dg.MaterializeResult(
        metadata={**report, "stage": KG_QC, "deferred": False, "report": str(report_path)}
    )


@dg.asset(
    name=LEGACY_TSV_EXPORT,
    description=(
        "Retrofit the published KGX pair into the legacy DAKP TSV schema. Reports the "
        "deferral when kg_build produced no KGX (there is nothing to convert)."
    ),
    deps=[kg_build],
)
def legacy_tsv_export(context: AssetExecutionContext) -> dg.MaterializeResult:
    from dakp_pipeline import legacy_tsv

    config = run_config()
    kgx_refs = refsio.load_refs(config, KG_BUILD)
    ctx = task_context(config)
    with _stage_logs(context):
        out_refs = legacy_tsv.export(kgx_refs, ctx)
    if not out_refs:
        context.log.warning(
            "legacy_tsv_export: Tablassert handoff was deferred; nothing to retrofit"
        )
        return dg.MaterializeResult(
            metadata={
                "stage": LEGACY_TSV_EXPORT,
                "deferred": True,
                "reason": "no KGX to retrofit (deferred handoff)",
            }
        )
    return _publish(config, context, LEGACY_TSV_EXPORT, out_refs)


# --- graph surface: assets, jobs, resources ------------------------------------------

all_assets: list[dg.AssetsDefinition] = [
    *ACQUIRE_ASSETS.values(),
    acquire_ner_models,
    *EXTRACT_ASSETS.values(),
    shape_approved_treats,
    shape_faers_uses,
    shape_contraindications,
    ner_training_export,
    tablassert_configs,
    kg_build,
    kg_qc,
    legacy_tsv_export,
]

# The published artifact each stage produces (used by the narrow jobs below and by the
# tests that assert a single-output run touches nothing else).
stage_outputs: dict[str, list[str]] = {
    **{acquire_stage(key): [f"refs/{acquire_stage(key)}.refs.json"] for key in SOURCE_BY_KEY},
    ACQUIRE_NER_MODELS: [f"refs/{ACQUIRE_NER_MODELS}.refs.json"],
    **{extract_stage(key): [f"refs/{extract_stage(key)}.refs.json"] for key in SOURCE_BY_KEY},
    **{stage: [f"refs/{stage}.refs.json"] for stage in SHAPE_STAGES},
    NER_TRAINING_EXPORT: [f"refs/{NER_TRAINING_EXPORT}.refs.json"],
    TABLASSERT_CONFIGS: [f"refs/{TABLASSERT_CONFIGS}.refs.json"],
    KG_BUILD: [
        f"refs/{KG_BUILD}.refs.json",
        f"kg/{KG_NODES}",
        f"kg/{KG_EDGES}",
        "kg/tablassert_handoff.json",
    ],
    KG_QC: [f"kg/{QC_REPORT}"],
    LEGACY_TSV_EXPORT: [f"refs/{LEGACY_TSV_EXPORT}.refs.json"],
}

dakp_build = dg.define_asset_job(
    "dakp_build",
    selection=dg.AssetSelection.assets(*all_assets),
    description="Full DAKP DAG: materialize every stage and every output.",
)


def _single_output_job(asset: dg.AssetsDefinition) -> dg.JobDefinition:
    """A job that builds exactly one output and none of the others."""
    stage = asset.key.to_user_string()
    return dg.define_asset_job(
        f"dakp_build_{stage}",
        selection=dg.AssetSelection.assets(asset),
        description=(
            f"Build only the '{stage}' output. Upstream artifacts are read from the "
            "published dir, never rebuilt; the run fails loudly if one is missing."
        ),
        tags={"dakp/stage": stage},
    )


# One narrow job per output, derived from all_assets so new stages are covered for free.
single_output_jobs: list[dg.JobDefinition] = [_single_output_job(asset) for asset in all_assets]

all_jobs: list[dg.JobDefinition] = [dakp_build, *single_output_jobs]

resources: dict[str, object] = {
    "pipes_client": mo_pipes.subprocess_client_resource(),
}

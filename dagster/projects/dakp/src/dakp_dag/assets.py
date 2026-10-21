"""Asset graph mirroring DAKP's stages.

Phase 1 bodies are demo implementations: they exercise the full pattern
(python assets -> Go pipes worker -> KGX ndjson output) with toy data.
Phase 2 replaces the bodies with the real DAKP pipeline package without
changing this asset graph.

Stage mapping from DAKP's Airflow DAG (`dakp_build`):
  acquisition  <- DailyMed / Drugs@FDA / FAERS pulls (Phase 2: dakp package)
  extraction   <- native Go extraction worker via Dagster Pipes
  ner          <- disease NER (gliner) (Phase 2)
  assertions   <- Tablassert-style assertion tables (Phase 2)
  kg_build     <- KGX ndjson via mo-kg (KGX ndjson is the only sink)

Data plane: every stage publishes one artifact under `PUBLISHED_ROOT` and
reads its upstream artifact from there. Stages declare `deps=[...]`
(lineage only) instead of value inputs, because a value input forces the
producer into the same run: dagster refuses to build a selection whose input
assets are absent ("is not produced by any of the provided asset ops"). With
lineage-only deps, one output can be triggered on its own, its upstreams are
read (never rebuilt), and it fails loudly when the artifact it needs is not
published yet.

Two trigger surfaces, both single-DAG:
  - `dakp_build`            -> every stage, every output (whole DAG)
  - `dakp_build_<stage>`    -> exactly one output, none of the others
Generated from `all_assets`, so a new stage gets its single-output job for
free. Headless equivalent of the narrow job:
`dagster asset materialize --select <stage>` (quote `--select '*+<stage>'` to
pull the whole upstream chain into one run).
"""

import json
import os
from pathlib import Path

import dagster as dg
from mo_dagster import pipes as mo_pipes
from mo_kg import kgx

# Everything runs from env; nothing hardcoded outside these defaults.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PUBLISHED_ROOT = Path(os.environ.get("MO_PUBLISHED_ROOT", PROJECT_ROOT / "published"))
WORKER_BIN = Path(
    os.environ.get("DAKP_WORKER_BIN", PROJECT_ROOT / "workers" / "go" / "bin" / "extractor")
)

# Published artifact per stage output (the only inter-stage contract).
ACQUISITION_RECORDS = "acquisition.ndjson"
EXTRACTED_RECORDS = "extraction.ndjson"
NER_RECORDS = "ner.ndjson"
ASSERTION_RECORDS = "assertions.ndjson"
KG_NODES = "kg.nodes.ndjson"
KG_EDGES = "kg.edges.ndjson"


def _published(name: str) -> Path:
    path = PUBLISHED_ROOT / "dakp"
    path.mkdir(parents=True, exist_ok=True)
    return path / name


def _write_ndjson(path: Path, rows: list[dict]) -> Path:
    """Line-delimited JSON: what every stage reader (and the Go worker) expects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    return path


def _read_ndjson(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _require(name: str, producer: str) -> tuple[Path, list[dict]]:
    """Read an upstream artifact, failing loudly when a narrow run skipped its stage."""
    path = _published(name)
    if not path.exists():
        raise FileNotFoundError(
            f"upstream artifact missing: {path}. Publish it with 'dakp_build_{producer}' "
            "(or materialize the whole chain in one run: dagster asset materialize "
            f"--select '*+{producer}')"
        )
    return path, _read_ndjson(path)


@dg.asset(description="DAKP stage 1: acquire source records (toy manifest in phase 1)")
def acquisition(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    records = [
        {"id": "demo-1", "source": "dailymed", "text": "Demo record one."},
        {"id": "demo-2", "source": "faers", "text": "Demo record two."},
    ]
    path = _write_ndjson(_published(ACQUISITION_RECORDS), records)
    context.log.info(f"wrote {len(records)} records to {path}")
    return dg.MaterializeResult(metadata={"records": len(records), "path": str(path)})


@dg.asset(
    description="DAKP stage 2: Go extraction worker via Dagster Pipes",
    deps=[acquisition],
)
def extraction(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    if not WORKER_BIN.exists():
        raise FileNotFoundError(
            f"extractor worker not built: {WORKER_BIN} "
            "(build with `go build -o bin/extractor ./extractor` in workers/go)"
        )
    input_path, _ = _require(ACQUISITION_RECORDS, producer="acquisition")
    output_path = _published(EXTRACTED_RECORDS)
    # run() blocks until the child exits (non-zero exit fails the step), and the
    # materialization the worker reports - cache_key/cache_hit/rows - is this
    # asset's result.
    return mo_pipes.launch_worker(
        context,
        str(WORKER_BIN),
        extras={"input": str(input_path), "output": str(output_path)},
    ).get_materialize_result()


@dg.asset(
    description="DAKP stage 3: disease NER (placeholder in phase 1)",
    deps=[extraction],
)
def ner(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    _, records = _require(EXTRACTED_RECORDS, producer="extraction")
    # Phase 2: gliner-based NER from the dakp package. Placeholder entities now.
    rows = [{"id": record["id"], "entities": []} for record in records]
    path = _write_ndjson(_published(NER_RECORDS), rows)
    context.log.info(f"ner placeholder: {len(rows)} records -> {path}")
    return dg.MaterializeResult(
        metadata={"stage": "ner", "status": "placeholder", "records": len(rows), "path": str(path)}
    )


@dg.asset(
    description="DAKP stage 4: Tablassert-style assertions (placeholder in phase 1)",
    deps=[ner],
)
def assertions(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    _, rows = _require(NER_RECORDS, producer="ner")
    tables = [{"id": row["id"], "assertions": []} for row in rows]
    path = _write_ndjson(_published(ASSERTION_RECORDS), tables)
    context.log.info(f"assertions placeholder: {len(tables)} records -> {path}")
    return dg.MaterializeResult(
        metadata={
            "stage": "assertions",
            "status": "placeholder",
            "records": len(tables),
            "path": str(path),
        }
    )


@dg.asset(
    description="DAKP stage 5: build the knowledge graph as KGX ndjson",
    deps=[assertions],
)
def kg_build(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    _, tables = _require(ASSERTION_RECORDS, producer="assertions")
    nodes = [
        {"id": "DRUG:demo", "name": "Demo drug", "category": "biolink:Drug"},
        {"id": "DISEASE:demo", "name": "Demo disease", "category": "biolink:Disease"},
    ]
    edges = [
        {
            "subject": "DRUG:demo",
            "predicate": "biolink:treats",
            "object": "DISEASE:demo",
            "knowledge_level": "prediction",
        }
    ]
    nodes_path = kgx.write_kgx_nodes(_published(KG_NODES), nodes)
    edges_path = kgx.write_kgx_edges(_published(KG_EDGES), edges)
    qc = kgx.qc_counts(nodes_path, edges_path)
    context.log.info(f"KGX written: {nodes_path}, {edges_path}")
    return dg.MaterializeResult(
        metadata={
            **qc,
            "assertion_records": len(tables),
            "nodes_path": str(nodes_path),
            "edges_path": str(edges_path),
        }
    )


all_assets = [
    acquisition,
    extraction,
    ner,
    assertions,
    kg_build,
]

# Every stage publishes these artifacts (used by the single-output jobs below
# and by the tests that assert a narrow run touches nothing else).
stage_outputs: dict[str, list[str]] = {
    asset.key.to_user_string(): artifacts
    for asset, artifacts in zip(
        all_assets,
        [
            [ACQUISITION_RECORDS],
            [EXTRACTED_RECORDS],
            [NER_RECORDS],
            [ASSERTION_RECORDS],
            [KG_NODES, KG_EDGES],
        ],
        strict=True,
    )
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
            f"Build only the '{stage}' output. Upstream artifacts are read from "
            "the published dir, never rebuilt; the run fails if one is missing."
        ),
        tags={"dakp/stage": stage},
    )


# One narrow job per output, derived from all_assets so new stages are covered.
single_output_jobs = [_single_output_job(asset) for asset in all_assets]

all_jobs = [dakp_build, *single_output_jobs]

resources: dict[str, object] = {
    "pipes_client": mo_pipes.subprocess_client_resource(),
}

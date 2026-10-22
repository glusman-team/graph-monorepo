"""Shared test harness for the DAKP code location.

The data-plane tests here exercise the ASSET GRAPH, not the pipeline's data semantics (the
pipeline package has its own 1400-test suite for that). So the heavy pieces are stubbed at
their documented seams:

- acquisition: ``dakp_pipeline.acquire.acquire_*`` functions
- Go extraction: a pipes-speaking python stub (same extras contract as ``workers/go``)
- Python extraction: ``dakp_pipeline.extract.<module>.extract``
- shaping: the edge-family shaper classes' ``transform`` (registry-driven, so one patch
  covers every family); the stubs still record their outputs in the operation index, which
  is what makes the already-done cache-hit path testable
- Tablassert: ``tablassert.generate`` / ``tablassert.run`` (their own docstring names
  ``monkeypatch.setattr("dakp_pipeline.tablassert.run", ...)`` as the seam)
- NER export: ``ner_export.export``

Left REAL on purpose: the refs handoff (publish/load), the op-index skip, ``legacy_tsv``
(the KGX -> legacy TSV retrofit), ``mo_kg.kgx`` QC, and every dagster definition.
"""

import importlib
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dakp_dag import assets

# The operation name each family records in the op index (mirrors the DAKP task names).
FAMILY_OPERATIONS = {
    "approved_treats": "shape_approved_treats",
    "faers_applied_to_treat": "shape_faers_applied_to_treat",
    "contraindications": "shape_contraindications",
}

PIPES_STUB = '''#!{python}
"""Stand-in for workers/go/cmd/dakp-extract: same pipes contract, no Go."""
import json
from pathlib import Path

import dagster_pipes

with dagster_pipes.open_dagster_pipes() as context:
    extras = context.extras
    missing = [key for key in ("stage", "input_refs", "output_refs", "workdir") if key not in extras]
    assert not missing, f"extraction worker extras missing keys: {{missing}}"
    inputs = json.loads(Path(extras["input_refs"]).read_text())
    Path(extras["output_refs"]).write_text(json.dumps(inputs, indent=2) + "\\n")
    context.report_asset_materialization(
        metadata={{
            "rows": len(inputs),
            "cache_hit": False,
            "cache_key": "0" * 64,
            "worker": "stub",
            "stage": extras["stage"],
        }}
    )
'''

CRASH_STUB = '''#!{python}
"""Exits non-zero without reporting, the way a crashed extraction worker does."""
import sys

sys.exit(3)
'''


def write_worker(path: Path, source: str) -> Path:
    """Materialize an executable python stub worker."""
    path.write_text(source.format(python=sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def make_ref(path: Path, rows: int | None = 1):
    """A real ArtifactRef over a small synthetic file (content-addressed like production)."""
    from dakp_pipeline.io.content_hash import hash_file
    from dakp_pipeline.io.contracts import ArtifactRef
    from dakp_pipeline.io.downloads import infer_media_type

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"synthetic {path.name}\n", encoding="utf-8")
    return ArtifactRef(
        uri=path, blake3=hash_file(path), media_type=infer_media_type(path), rows=rows
    )


def published_files(root: Path) -> set[str]:
    """Every file under the published root, as a root-relative posix path."""
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def snapshot(root: Path) -> dict[str, str]:
    """Name -> bytes (as text) for every published file, to prove a run rewrote nothing."""
    return {name: (root / name).read_bytes().hex() for name in sorted(published_files(root))}


def executed_stages(result) -> list[str]:
    """The step keys that succeeded, in execution order."""
    return [event.step_key for event in result.get_step_success_events()]


def failure_text(result) -> str:
    """The raised exception text of a failed run's steps."""
    events = result.get_step_failure_events()
    assert events, "run failed without a step failure event"
    return "\n".join(str(getattr(event.event_specific_data, "error", event)) for event in events)


def materialization(result, stage: str) -> dict:
    """The materialization metadata of one asset in a run, as plain python values."""
    events = [
        event
        for event in result.get_asset_materialization_events()
        if event.event_specific_data.materialization.asset_key.to_user_string() == stage
    ]
    assert len(events) == 1, f"expected exactly one materialization for {stage}, got {len(events)}"
    values = {}
    for key, value in events[0].event_specific_data.materialization.metadata.items():
        resolved = getattr(value, "value", None)
        values[key] = getattr(value, "text", resolved) if resolved is None else resolved
    return values


def run_job(name: str, *, raise_on_error: bool = True):
    """Execute one job of the code location in-process."""
    from dakp_dag.definitions import defs

    return defs.resolve_job_def(name).execute_in_process(raise_on_error=raise_on_error)


@pytest.fixture
def dag_env(tmp_path, monkeypatch) -> SimpleNamespace:
    """Isolate the data plane: published root, workdir, offline NER, stub worker binary."""
    published = tmp_path / "published"
    workdir = tmp_path / "workdir"
    worker_bin = write_worker(tmp_path / "stub_extractor.py", PIPES_STUB)
    monkeypatch.setenv("MO_PUBLISHED_ROOT", str(published))
    monkeypatch.setenv("DAKP_WORKDIR", str(workdir))
    monkeypatch.setenv("DAKP_NER_OFFLINE", "1")
    monkeypatch.setenv("DAKP_EXTRACT_WORKER_BIN", str(worker_bin))
    monkeypatch.delenv("DAKP_FULLMAP", raising=False)
    monkeypatch.delenv("DAKP_FORCE", raising=False)
    return SimpleNamespace(
        tmp=tmp_path, published=published, workdir=workdir, worker_bin=worker_bin
    )


def _fake_kgx_pair(workdir: Path, stem: str) -> tuple[Path, Path]:
    """A minimal valid KGX pair under ``<workdir>/kgx`` (what Tablassert leaves behind)."""
    kgx_dir = workdir / "kgx"
    kgx_dir.mkdir(parents=True, exist_ok=True)
    nodes = kgx_dir / f"{stem}.nodes.ndjson"
    edges = kgx_dir / f"{stem}.edges.ndjson"
    nodes.write_text(
        json.dumps({"id": "DRUG:1", "name": "Etanercept", "category": ["biolink:Drug"]})
        + "\n"
        + json.dumps({"id": "DISEASE:1", "name": "Psoriasis", "category": ["biolink:Disease"]})
        + "\n",
        encoding="utf-8",
    )
    edges.write_text(
        json.dumps(
            {
                "id": "uuid:1",
                "subject": "DRUG:1",
                "predicate": "biolink:treats",
                "object": "DISEASE:1",
                "knowledge_level": "knowledge_assertion",
                "agent_type": "manual_validation_of_automated_agent",
                "publications": ["SPL:1"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return nodes, edges


@pytest.fixture
def stubbed_pipeline(monkeypatch, dag_env) -> SimpleNamespace:
    """Stub every heavy pipeline entry point at its documented seam (see module docstring).

    ``defer_tablassert()`` switches the handoff to its deferred mode (no fullmap), so the
    deferral path through kg_build / kg_qc / legacy_tsv_export is testable.
    """
    acquire = importlib.import_module("dakp_pipeline.acquire")
    families = importlib.import_module("dakp_pipeline.families")
    ner_export = importlib.import_module("dakp_pipeline.ner_export")
    tablassert = importlib.import_module("dakp_pipeline.tablassert")
    state = SimpleNamespace(deferred=False, calls=[], stem="DRUG_APPROVALS_KP_test")

    def fake_acquire(name: str):
        def _run(ctx):
            state.calls.append(name)
            return [make_ref(dag_env.workdir / "raw" / f"{name}.txt")]

        return _run

    for source in assets.SOURCES:
        monkeypatch.setattr(acquire, source.acquire, fake_acquire(source.acquire))
    monkeypatch.setattr(acquire, "acquire_ner_models", fake_acquire("acquire_ner_models"))

    def fake_extract(name: str):
        def _run(inputs, ctx):
            state.calls.append(name)
            return [make_ref(dag_env.workdir / "interim" / f"{name}.parquet", rows=len(inputs))]

        return _run

    for source in assets.SOURCES:
        kind, _, module = source.extractor.partition(":")
        if kind == "py":
            extractor_module = importlib.import_module(f"dakp_pipeline.extract.{module}")
            monkeypatch.setattr(extractor_module, "extract", fake_extract(module))

    from dakp_pipeline.assertions.evidence import shape_operation_inputs
    from dakp_pipeline.io.artifact_store import ArtifactStore
    from dakp_pipeline.io.manifests import OperationBlock
    from dakp_pipeline.paths import Workdir

    for family in families.all_families():
        operation = FAMILY_OPERATIONS[family.name]

        def fake_transform(self, inputs, ctx, *, _family=family.name, _operation=operation):
            """Stand in for a shaper, registering exactly like ``write_assertion_table`` does.

            The registration (store manifest + op-index entry keyed by the shape inputs and
            the config fingerprint) is what makes the already-done skip observable, so the
            fake keeps it and only skips the row-level shaping semantics.
            """
            state.calls.append(_family)
            out = dag_env.workdir / "tabular" / f"{_family}.tsv"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("synthetic\n", encoding="utf-8")
            return [
                ArtifactStore(Workdir(ctx.workdir)).register(
                    out,
                    media_type="text/tab-separated-values",
                    rows=len(inputs),
                    inputs=shape_operation_inputs(inputs, ctx),
                    operation=OperationBlock(name=_operation),
                )
            ]

        monkeypatch.setattr(family.shaper, "transform", fake_transform)

    def fake_generate(assertion_refs, ctx):
        state.calls.append("tablassert.generate")
        return [make_ref(dag_env.workdir / "tablassert" / "graph.yaml", rows=None)]

    def fake_run(assertion_refs, config_refs, ctx):
        state.calls.append("tablassert.run")
        reports = Workdir(ctx.workdir).reports
        reports.mkdir(parents=True, exist_ok=True)
        report_path = reports / tablassert.REPORT_NAME
        report: dict[str, object] = {
            "schema_version": 1,
            "stage": "tablassert_handoff",
            "assertion_inputs": [],
            "config_inputs": [],
        }
        if state.deferred:
            report.update({"mode": "deferred", "status": "deferred"})
        else:
            _fake_kgx_pair(ctx.workdir, state.stem)
            report.update({"mode": "real", "status": "success", "kgx_stem": state.stem})
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return [make_ref(report_path, rows=None)]

    def fake_export(inputs, ctx):
        state.calls.append("ner_export.export")
        return [make_ref(dag_env.workdir / "ner" / "training_bundle.json", rows=len(inputs))]

    monkeypatch.setattr(tablassert, "generate", fake_generate)
    monkeypatch.setattr(tablassert, "run", fake_run)
    monkeypatch.setattr(ner_export, "export", fake_export)

    def defer_tablassert() -> None:
        state.deferred = True

    state.defer_tablassert = defer_tablassert
    return state

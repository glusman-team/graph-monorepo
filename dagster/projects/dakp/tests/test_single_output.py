"""Single-output triggering and the published-artifact data plane.

Two invariants the monorepo contract rests on:

- a narrow ``dakp_build_<stage>`` job runs exactly that stage and rewrites no other
  published artifact (upstream stages are READ from the published dir, never rebuilt);
- a narrow run whose upstream refs are not published fails loudly, naming the producer job,
  instead of silently rebuilding or emitting an empty output.

Plus the end-to-end shape of one full run: every declared artifact published, stages ordered
by lineage, the operation index serving the second run, the deferred Tablassert handoff
reported (not failed) through kg_build / kg_qc / legacy_tsv_export, and a crashed Go worker
failing its stage without publishing.
"""

import json

import pytest

from dakp_dag import assets

from .conftest import (
    CRASH_STUB,
    executed_stages,
    failure_text,
    materialization,
    published_files,
    run_job,
    snapshot,
    write_worker,
)

STAGES = list(assets.stage_outputs)


def refs_file(stage: str) -> str:
    return f"dakp/refs/{stage}.refs.json"


def published_refs(dag_env, stage: str) -> list[dict]:
    return json.loads((dag_env.published / "dakp" / "refs" / f"{stage}.refs.json").read_text())


def test_full_run_publishes_every_declared_artifact(dag_env, stubbed_pipeline):
    result = run_job("dakp_build")
    assert result.success, failure_text(result) if not result.success else ""

    expected = {f"dakp/{name}" for outputs in assets.stage_outputs.values() for name in outputs}
    assert published_files(dag_env.published) == expected

    # the KG build publishes a readable KGX pair, and QC counts it
    nodes = dag_env.published / "dakp" / "kg" / assets.KG_NODES
    edges = dag_env.published / "dakp" / "kg" / assets.KG_EDGES
    assert len(nodes.read_text().splitlines()) == 2
    assert len(edges.read_text().splitlines()) == 1
    qc = json.loads((dag_env.published / "dakp" / "kg" / assets.QC_REPORT).read_text())
    assert {"nodes": 2, "edges": 1, "dangling_edges": 0} == qc

    # the legacy retrofit is the REAL legacy_tsv code over the published pair
    tsvs = sorted(p.stem for p in (dag_env.workdir / "kgx").glob("*.tsv"))
    stem = stubbed_pipeline.stem
    assert tsvs == [f"{stem}.edges", f"{stem}.nodes"]
    assert len(published_refs(dag_env, assets.LEGACY_TSV_EXPORT)) == 2


def test_full_run_orders_stages_by_lineage(dag_env, stubbed_pipeline):
    result = run_job("dakp_build")
    assert result.success

    order = {stage: index for index, stage in enumerate(executed_stages(result))}
    assert set(order) == set(STAGES), "a stage did not run"
    by_name = {asset.key.to_user_string(): asset for asset in assets.all_assets}
    for stage, asset in by_name.items():
        for spec in asset.specs:
            for dep in spec.deps:
                upstream = dep.asset_key.to_user_string()
                assert order[upstream] < order[stage], f"{upstream} ran after {stage}"


def test_narrow_run_rebuilds_only_its_stage(dag_env, stubbed_pipeline, monkeypatch):
    # force: bypass the op-index skip so this test observes a real re-shape of one stage
    monkeypatch.setenv("DAKP_FORCE", "1")
    assert run_job("dakp_build").success
    before = snapshot(dag_env.published)
    stubbed_pipeline.calls.clear()

    result = run_job(f"dakp_build_{assets.SHAPE_STAGES[0]}")
    assert result.success
    assert executed_stages(result) == [assets.SHAPE_STAGES[0]]
    assert stubbed_pipeline.calls == ["approved_treats"], "a narrow run re-ran another stage"

    after = snapshot(dag_env.published)
    assert set(after) == set(before), "a narrow run added or removed published artifacts"
    rewritten = {name for name in before if before[name] != after[name]}
    assert rewritten <= {refs_file(assets.SHAPE_STAGES[0])}


def test_rerun_hits_the_operation_index_across_runs(dag_env, stubbed_pipeline):
    """Dagster-native caching rides the pipeline op index: same inputs => no re-shape.

    The index lives in the workdir, so the hit survives across dagster runs (a narrow job
    after a full one) - which is the point of caching at the data layer instead of only in
    dagster's asset-check storage.
    """
    assert run_job("dakp_build").success
    stubbed_pipeline.calls.clear()

    warm = materialization(run_job("dakp_build_shape_contraindications"), "shape_contraindications")
    assert warm["cache_hit"] is True, "the op-index already-done skip did not hit"
    assert stubbed_pipeline.calls == [], "a cache hit still ran the shaper"
    assert warm["refs"] >= 1


def test_force_bypasses_the_operation_index(dag_env, stubbed_pipeline, monkeypatch):
    assert run_job("dakp_build").success
    stubbed_pipeline.calls.clear()
    monkeypatch.setenv("DAKP_FORCE", "1")

    forced = materialization(
        run_job("dakp_build_shape_contraindications"), "shape_contraindications"
    )
    assert forced["cache_hit"] is False
    assert stubbed_pipeline.calls == ["contraindications"]


@pytest.mark.parametrize(
    ("stage", "producer"),
    [
        ("extract_dailymed", "acquire_dailymed"),
        ("shape_approved_treats", "extract_dailymed"),
        (assets.KG_BUILD, "shape_approved_treats"),
        (assets.KG_QC, assets.KG_BUILD),
        (assets.LEGACY_TSV_EXPORT, assets.KG_BUILD),
    ],
)
def test_narrow_run_fails_loudly_without_its_published_upstream(
    dag_env, stubbed_pipeline, stage, producer
):
    result = run_job(f"dakp_build_{stage}", raise_on_error=False)
    assert not result.success
    failure = failure_text(result)
    assert "upstream artifact missing" in failure
    assert f"dakp_build_{producer}" in failure
    assert refs_file(stage) not in published_files(dag_env.published)


def test_deferred_handoff_is_reported_not_failed(dag_env, stubbed_pipeline):
    """No fullmap => Tablassert defers; the KG stages report it and the run still succeeds."""
    stubbed_pipeline.defer_tablassert()
    result = run_job("dakp_build")
    assert result.success, failure_text(result) if not result.success else ""

    assert materialization(result, assets.KG_BUILD)["deferred"] is True
    assert materialization(result, assets.KG_QC)["deferred"] is True
    assert materialization(result, assets.LEGACY_TSV_EXPORT)["deferred"] is True

    published = published_files(dag_env.published)
    assert not any(name.startswith("dakp/kg/") for name in published), (
        "a deferred build published KGX"
    )
    assert refs_file(assets.KG_BUILD) in published, "the handoff report refs are still published"


def test_crashed_worker_fails_its_stage_without_publishing(dag_env, stubbed_pipeline, monkeypatch):
    monkeypatch.setenv(
        "DAKP_EXTRACT_WORKER_BIN", str(write_worker(dag_env.tmp / "crasher.py", CRASH_STUB))
    )
    assert run_job("dakp_build_acquire_dailymed").success
    before = snapshot(dag_env.published)

    result = run_job("dakp_build_extract_dailymed", raise_on_error=False)
    assert not result.success
    assert "External execution process failed with code 3" in failure_text(result)
    assert snapshot(dag_env.published) == before, "a crashed worker published artifacts"


def test_missing_worker_binary_fails_loudly(dag_env, stubbed_pipeline, monkeypatch):
    monkeypatch.setenv("DAKP_EXTRACT_WORKER_BIN", str(dag_env.tmp / "nope" / "dakp-extract"))
    assert run_job("dakp_build_acquire_dailymed").success

    result = run_job("dakp_build_extract_dailymed", raise_on_error=False)
    assert not result.success
    failure = failure_text(result)
    assert "extraction worker not built" in failure
    assert "go build -o bin/dakp-extract" in failure


def test_go_worker_extras_carry_the_run_config(dag_env, stubbed_pipeline, monkeypatch):
    """The pipes extras contract the Go worker reads, including the run knobs."""
    monkeypatch.setenv("DAKP_THREADS", "4")
    monkeypatch.setenv("DAKP_MEMORY_BUDGET_GB", "8")
    monkeypatch.setenv("DAKP_QUARTER_LIMIT", "2")
    monkeypatch.setenv("DAKP_FORCE", "1")
    assert run_job("dakp_build_acquire_faers").success

    result = run_job("dakp_build_extract_faers")
    assert result.success
    metadata = materialization(result, "extract_faers")
    assert metadata["worker"] == "stub", "the stub worker never saw the pipes extras contract"
    assert metadata["stage"] == "faers"

    inputs = json.loads((dag_env.workdir / "pipes" / "extract_faers.inputs.json").read_text())
    assert inputs and all({"uri", "blake3", "media_type"} <= set(item) for item in inputs)

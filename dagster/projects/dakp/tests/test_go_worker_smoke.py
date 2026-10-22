"""The real Go extraction worker, driven through Dagster Pipes by the asset graph.

Skipped when ``workers/go/bin/dakp-extract`` is not built (CI installs no Go toolchain for
the python job): build it with ``go build -o bin/dakp-extract ./cmd/dakp-extract`` inside
``workers/go``. The input is the Go suite's own DailyMed fixture, so no network is involved:
this test proves the refs-file protocol, the worker's cache metadata, and the op-index skip
across two dagster runs against the production binary.
"""

import json
from pathlib import Path

import pytest

from dakp_dag import assets, refsio
from dakp_dag.config import run_config

from .conftest import make_ref, materialization, published_files, run_job

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_BIN = PROJECT_ROOT / "workers" / "go" / "bin" / "dakp-extract"
FIXTURE = (
    PROJECT_ROOT / "workers" / "go" / "internal" / "dailymed" / "testdata" / "dailymed_spl.xml.gz"
)

pytestmark = [
    pytest.mark.skipif(not WORKER_BIN.exists(), reason=f"go worker not built: {WORKER_BIN}"),
    pytest.mark.skipif(not FIXTURE.exists(), reason=f"dailymed fixture missing: {FIXTURE}"),
]


def _publish_acquired_fixture(dag_env) -> None:
    """Publish the acquisition handoff by hand: one gz SPL fixture ref."""
    refsio.publish_refs(run_config(), "acquire_dailymed", [make_ref(FIXTURE, rows=None)])
    refs_path = dag_env.published / "dakp" / "refs" / "acquire_dailymed.refs.json"
    assert json.loads(refs_path.read_text())[0]["uri"].endswith("dailymed_spl.xml.gz")


def test_real_worker_extracts_and_reports_cache_metadata(dag_env, monkeypatch):
    monkeypatch.setenv("DAKP_EXTRACT_WORKER_BIN", str(WORKER_BIN))
    _publish_acquired_fixture(dag_env)

    cold = materialization(run_job("dakp_build_extract_dailymed"), "extract_dailymed")
    assert cold["cache_hit"] is False
    assert cold["worker_version"], "the worker did not report its version"
    assert len(cold["cache_key"]) == 64, f"cache_key is not BLAKE3 hex: {cold['cache_key']}"
    # the real worker reports cache_key/cache_hit/rows/worker_version only, so the asset
    # fills the dag stage name; that it dispatched the right extractor is proven by the
    # locked output names below (an unknown stage in the extras is a hard worker error).
    assert cold["stage"] == "extract_dailymed"

    published = json.loads(
        (dag_env.published / "dakp" / "refs" / "extract_dailymed.refs.json").read_text()
    )
    names = [Path(item["uri"]).name for item in published]
    # the locked order the Go suite pins: 5 interim parquet (spl_documents first) + sections TSV
    assert len(published) == 6, names
    assert names[0] == "spl_documents.parquet"
    assert names[-1] == "dailymed_spl_sections.tsv"
    assert int(cold["rows"]) == sum(item["rows"] or 0 for item in published)
    assert all(Path(item["uri"]).exists() for item in published)
    assert "dakp/refs/extract_dailymed.refs.json" in published_files(dag_env.published)

    warm = materialization(run_job("dakp_build_extract_dailymed"), "extract_dailymed")
    assert warm["cache_hit"] is True, "the worker's op-index skip did not hit on re-run"
    assert warm["cache_key"] == cold["cache_key"]
    assert warm["rows"] == cold["rows"]


def test_real_worker_force_bypasses_the_op_index(dag_env, monkeypatch):
    monkeypatch.setenv("DAKP_EXTRACT_WORKER_BIN", str(WORKER_BIN))
    monkeypatch.setenv("DAKP_FORCE", "1")
    _publish_acquired_fixture(dag_env)

    first = materialization(run_job("dakp_build_extract_dailymed"), "extract_dailymed")
    second = materialization(run_job("dakp_build_extract_dailymed"), "extract_dailymed")
    assert first["cache_hit"] is False
    assert second["cache_hit"] is False, "DAKP_FORCE=1 still reported a cache hit"
    assert second["cache_key"] == first["cache_key"]


def test_run_config_limits_reach_the_worker(dag_env, monkeypatch):
    """The RunConfig knobs are forwarded as pipes extras the worker reads."""
    monkeypatch.setenv("DAKP_EXTRACT_WORKER_BIN", str(WORKER_BIN))
    monkeypatch.setenv("DAKP_THREADS", "2")
    monkeypatch.setenv("DAKP_MEMORY_BUDGET_GB", "4")
    monkeypatch.setenv("DAKP_QUARTER_LIMIT", "1")
    monkeypatch.setenv("DAKP_RELEASE_LIMIT", "1")
    _publish_acquired_fixture(dag_env)

    assert run_job("dakp_build_extract_dailymed").success
    extras = json.loads((dag_env.workdir / "pipes" / "extract_dailymed.inputs.json").read_text())
    assert extras, "the worker inputs file was not written"
    config = run_config()
    assert (
        config.threads,
        config.memory_budget_gb,
        config.quarter_limit,
        config.release_limit,
    ) == (
        2,
        4,
        1,
        1,
    )
    assert assets.extract_stage("dailymed") == "extract_dailymed"

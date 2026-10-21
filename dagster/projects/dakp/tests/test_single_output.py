"""Single-output triggering: one narrow job builds exactly one output.

The DAKP DAG publishes one artifact per stage, and every stage has a
`dakp_build_<stage>` job that selects only that asset. These tests pin the two
halves of the requirement:

- exactly the targeted stage runs (no other stage executes);
- no other output is written or rewritten (artifact set + bytes unchanged).

`extraction` runs a Go pipes worker in production. Tests drive it through a
python stub that speaks the same pipes contract, so narrow-run behaviour is
covered even where the worker binary is not built (CI); the real binary is
covered by `test_real_worker_publishes_cache_metadata` when it exists.
"""

import json
import stat
import sys
from pathlib import Path

import pytest

from dakp_dag import assets
from dakp_dag.definitions import defs

STAGES = list(assets.stage_outputs)

PIPES_STUB = """#!/{python}
\"\"\"Minimal stand-in for workers/go/extractor: same pipes contract, no Go.\"\"\"
import json
from pathlib import Path

import dagster_pipes

with dagster_pipes.open_dagster_pipes() as context:
    extras = context.extras
    rows = [
        json.loads(line)
        for line in Path(extras["input"]).read_text().splitlines()
        if line.strip()
    ]
    for row in rows:
        row["extracted"] = True
    Path(extras["output"]).write_text(
        "".join(json.dumps(row, sort_keys=True) + "\\n" for row in rows)
    )
    context.report_asset_materialization(
        metadata={{"rows": len(rows), "worker": "stub"}}, data_version="stub"
    )
"""

CRASH_STUB = """#!/{python}
\"\"\"Exits non-zero without reporting, the way a crashed extraction job does.\"\"\"
import sys

sys.exit(3)
"""


def _write_worker(path: Path, source: str) -> Path:
    path.write_text(source.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _artifacts(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def _snapshot(root: Path) -> dict[str, str]:
    return {name: (root / name).read_text() for name in sorted(_artifacts(root))}


def _materialization(result, stage: str) -> dict:
    events = [
        event
        for event in result.get_asset_materialization_events()
        if event.event_specific_data.materialization.asset_key.to_user_string() == stage
    ]
    assert len(events) == 1, f"expected exactly one materialization for {stage}"
    metadata = events[0].event_specific_data.materialization.metadata
    values = {}
    for key, value in metadata.items():
        values[key] = getattr(value, "value", None)
        if values[key] is None:
            values[key] = getattr(value, "text", value)
    return values


def _run(stage: str):
    return defs.resolve_job_def(f"dakp_build_{stage}").execute_in_process()


def _run_expect_failure(stage: str):
    """Narrow run allowed to fail: return the result instead of raising."""
    return defs.resolve_job_def(f"dakp_build_{stage}").execute_in_process(raise_on_error=False)


def executed_stages(result) -> list[str]:
    return [event.step_key for event in result.get_step_success_events()]


def failure_text(result) -> str:
    """The raised exception text for the failed step(s) of a run."""
    events = result.get_step_failure_events()
    assert events, "run failed without a step failure event"
    return "\n".join(str(getattr(event.event_specific_data, "error", event)) for event in events)


@pytest.fixture
def published(tmp_path, monkeypatch):
    """Isolate the on-disk data plane for one test."""
    root = tmp_path / "published"
    monkeypatch.setattr(assets, "PUBLISHED_ROOT", root)
    return root


@pytest.fixture
def stub_worker(tmp_path, monkeypatch):
    """Swap the Go extractor for a pipes-speaking python stub."""
    monkeypatch.setattr(
        assets, "WORKER_BIN", _write_worker(tmp_path / "stub_extractor.py", PIPES_STUB)
    )


def test_every_output_has_a_narrow_job():
    names = {job.name for job in assets.all_jobs}
    assert names == {"dakp_build"} | {f"dakp_build_{stage}" for stage in STAGES}
    # loading the repo is the gate for an invalid asset selection
    assert defs.get_repository_def() is not None


@pytest.mark.parametrize("stage", STAGES)
def test_narrow_job_selects_only_its_asset(stage):
    job = defs.resolve_job_def(f"dakp_build_{stage}")
    assert {node.name for node in job.nodes} == {stage}


def test_narrow_run_builds_one_output_and_leaves_the_others(published, stub_worker):
    seen: set[str] = set()
    for stage in STAGES:
        before = _snapshot(published)
        result = _run(stage)
        assert result.success, f"{stage} failed: {result.get_run_failure_event()}"

        assert executed_stages(result) == [stage]

        # only this stage's artifacts may appear, and earlier ones keep their bytes
        seen |= {f"dakp/{name}" for name in assets.stage_outputs[stage]}
        assert _artifacts(published) == seen
        for name, content in before.items():
            assert _snapshot(published)[name] == content, f"{stage} rewrote {name}"


def test_narrow_run_fails_loudly_when_its_input_is_not_published(published, stub_worker):
    for stage, producer in [("extraction", "acquisition"), ("kg_build", "assertions")]:
        result = _run_expect_failure(stage)
        assert not result.success
        failure = failure_text(result)
        assert "upstream artifact missing" in failure
        assert f"dakp_build_{producer}" in failure
    assert _artifacts(published) == set()


def test_full_dag_job_still_builds_every_output_in_order(published, stub_worker):
    result = defs.resolve_job_def("dakp_build").execute_in_process()
    assert result.success

    # lineage-only deps must still serialize the stages
    assert executed_stages(result) == STAGES

    expected = {f"dakp/{name}" for names in assets.stage_outputs.values() for name in names}
    assert _artifacts(published) == expected


def test_failed_worker_fails_the_narrow_run_without_publishing(published, tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "WORKER_BIN", _write_worker(tmp_path / "crasher.py", CRASH_STUB))
    assert _run("acquisition").success
    before = _snapshot(published)

    result = _run_expect_failure("extraction")
    assert not result.success
    assert "External execution process failed with code 3" in failure_text(result)
    assert _snapshot(published) == before, "a crashed worker rewrote artifacts"


@pytest.mark.skipif(
    not Path(assets.WORKER_BIN).exists(),
    reason=f"go worker not built: {assets.WORKER_BIN} (see README first-time worker build)",
)
def test_real_worker_publishes_cache_metadata(published):
    """The Go extractor's cache trio lands on the extraction asset, and a re-run hits."""
    assert _run("acquisition").success

    cold = _materialization(_run("extraction"), "extraction")
    assert cold["cache_hit"] is False
    assert int(cold["rows"]) == 2
    assert len(str(cold["cache_key"])) == 64  # BLAKE3 hex

    warm = _materialization(_run("extraction"), "extraction")
    assert warm["cache_hit"] is True, "content-addressed cache did not hit"
    assert warm["cache_key"] == cold["cache_key"]

    path = published / "dakp" / assets.EXTRACTED_RECORDS
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert [row["id"] for row in rows] == ["demo-1", "demo-2"]
    assert all(row["extracted"] is True for row in rows)

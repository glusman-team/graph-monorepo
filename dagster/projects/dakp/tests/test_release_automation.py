"""Dagster wiring for scheduled release checks (API boundaries are faked)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import dagster as dg
import pytest
from dakp_pipeline.io.artifact_store import ArtifactStore
from dakp_pipeline.io.manifests import SourceBlock
from dakp_pipeline.paths import Workdir
from dakp_pipeline.release.delta import (
    SourceChange,
    SourceDelta,
    load_pending_delta,
    write_pending_delta,
)
from dakp_pipeline.release.gpu import GPUDevice, GPUStatus
from dakp_pipeline.release.preflight import PreflightReport
from dakp_pipeline.release.state import ReleaseState, SourceFingerprint, state_path, write_state

from dakp_dag import assets, automation, refsio, release
from dakp_dag.automation import weekly_release_schedule
from dakp_dag.definitions import defs


def test_release_check_orders_preflight_before_acquisition_and_delta() -> None:
    job = defs.resolve_job_def("dakp_release_check")
    assert "release_preflight" in {node.name for node in job.nodes}
    for asset in release.release_acquisitions:
        deps = {dep.asset_key.to_user_string() for spec in asset.specs for dep in spec.deps}
        assert deps == {"release_preflight"}
    delta_deps = {
        dep.asset_key.to_user_string() for spec in release.source_delta.specs for dep in spec.deps
    }
    assert delta_deps == {asset.key.to_user_string() for asset in release.release_acquisitions}


def test_weekly_schedule_is_friday_1700_pacific_and_deduplicated() -> None:
    schedule = defs.resolve_schedule_def("dakp_weekly_release")
    assert schedule.cron_schedule == "0 17 * * FRI"
    assert schedule.execution_timezone == "America/Los_Angeles"
    tick = dg.build_schedule_context(
        scheduled_execution_time=datetime(
            2026, 11, 6, 17, 0, tzinfo=ZoneInfo("America/Los_Angeles")
        )
    )
    requests = weekly_release_schedule.evaluate_tick(tick).run_requests
    assert len(requests) == 1
    assert requests[0].run_key == "dakp-release-check-2026-W45"


def _fake_acquisition(context, config, stage: str, fn_name: str) -> dg.MaterializeResult:
    store = ArtifactStore(Workdir(config.workdir))
    path = config.workdir / "downloads" / f"{stage}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(stage.encode("utf-8"))
    ref, _ = store.ingest(
        path,
        media_type="application/octet-stream",
        alias=f"{stage}/source.bin",
        source=SourceBlock(
            url=f"https://example.test/{stage}",
            last_modified="2026-11-04T00:00:00+00:00",
            retrieved_at="2026-11-06T17:00:00+00:00",
        ),
    )
    refsio.publish_refs(config, stage, [ref])
    return dg.MaterializeResult(metadata={"stage": stage})


def _run_check(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        release, "run_preflight", lambda *args, **kwargs: PreflightReport(git_sha="abc")
    )
    monkeypatch.setattr(release, "_run_acquisition", _fake_acquisition)
    return defs.resolve_job_def("dakp_release_check").execute_in_process()


def _delta_materialization(result) -> dict:
    event = next(
        event
        for event in result.all_events
        if event.is_step_materialization
        and event.step_materialization_data.materialization.asset_key.to_user_string()
        == "source_delta"
    )
    return event.step_materialization_data.materialization.metadata


def test_release_check_writes_delta_then_skips_unchanged_sources(
    dag_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _run_check(monkeypatch)
    assert first.success
    metadata = _delta_materialization(first)
    assert metadata["changes"].value == len(assets.SOURCES)
    pending = load_pending_delta(dag_env.published)
    assert pending.changed and pending.check_run_id
    write_state(
        state_path(dag_env.published),
        ReleaseState("1.27.0", "2026-11-01T00:00:00+00:00", pending.current),
    )

    second = _run_check(monkeypatch)
    assert second.success
    metadata = _delta_materialization(second)
    assert metadata["changes"].value == 0
    assert "no build, version bump, or upload" in metadata["message"].value
    assert not load_pending_delta(dag_env.published).changed


def _write_gate_delta(root: Path, *, changed: bool = True) -> SourceDelta:
    fingerprint = SourceFingerprint("b3:abc", 1, ("2026-11-04",), ("2026-11-06",))
    delta = SourceDelta(
        current={"DailyMed": fingerprint},
        changes=(SourceChange("DailyMed", fingerprint, None),) if changed else (),
        previous_version="1.27.0" if changed else None,
        created_at="2026-11-06T17:10:00-08:00",
        check_started_at="2026-11-06T17:00:00-08:00",
    )
    write_pending_delta(root, delta, check_run_id="check-run-1")
    return delta


class _Instance:
    """Minimal instance double: the gate only reads the check run's status."""

    def __init__(self, status=None):
        self.status = status if status is not None else dg.DagsterRunStatus.SUCCESS

    def get_run_record_by_id(self, run_id):
        from types import SimpleNamespace

        return SimpleNamespace(dagster_run=SimpleNamespace(status=self.status))


class _SensorContext:
    def __init__(self, cursor=None, status=None):
        self.cursor = cursor
        self.instance = _Instance(status)
        self.log = SimpleNamespaceLog()

    def update_cursor(self, cursor):
        self.cursor = cursor


class SimpleNamespaceLog:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)


def _sensor_context(cursor: str | None = None, status=None):
    return _SensorContext(cursor, status)


def test_gpu_gate_retries_then_launches_once_with_version(
    dag_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_gate_delta(dag_env.published)
    busy = GPUStatus((GPUDevice(0, "P100", 13000, 16384),))
    free = GPUStatus((GPUDevice(0, "P100", 512, 16384),))
    monkeypatch.setattr(automation, "probe_gpus", lambda: busy)
    monkeypatch.setattr(automation, "_hf_api", lambda settings: object())
    monkeypatch.setattr(automation, "next_hf_patch_version", lambda api, repo: "1.27.1")
    now = datetime.fromisoformat("2026-11-06T18:00:00-08:00")

    closed = automation._gpu_gate_tick(_sensor_context(), now=now)
    assert isinstance(closed, dg.SkipReason)
    monkeypatch.setattr(automation, "probe_gpus", lambda: free)
    launched_context = _sensor_context()
    launched = automation._gpu_gate_tick(launched_context, now=now)
    assert isinstance(launched, dg.RunRequest)
    assert launched.run_key == "check-run-1"
    assert launched.tags["dakp/publication_version"] == "1.27.1"
    cursor = launched_context.cursor
    assert "consumed_run_id" in cursor

    duplicate = automation._gpu_gate_tick(_sensor_context(cursor), now=now)
    assert isinstance(duplicate, dg.SkipReason)
    assert "already consumed" in duplicate.skip_message


def test_gpu_gate_times_out_after_24h_and_files_one_issue(
    dag_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_gate_delta(dag_env.published)
    issues = []
    monkeypatch.setattr(automation, "_gpu_timeout_issue", lambda **kwargs: issues.append(kwargs))
    deadline = datetime.fromisoformat("2026-11-07T17:00:00-08:00")
    context = _sensor_context()
    result = automation._gpu_gate_tick(context, now=deadline)
    assert isinstance(result, dg.SkipReason)
    assert len(issues) == 1
    assert issues[0]["deadline"].isoformat() == deadline.isoformat()
    second = automation._gpu_gate_tick(_sensor_context(context.cursor), now=deadline)
    assert isinstance(second, dg.SkipReason)
    assert len(issues) == 1


def test_gpu_gate_does_not_probe_when_sources_are_unchanged(
    dag_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_gate_delta(dag_env.published, changed=False)
    monkeypatch.setattr(
        automation, "probe_gpus", lambda: pytest.fail("unchanged delta probed GPUs")
    )
    result = automation._gpu_gate_tick(_sensor_context())
    assert isinstance(result, dg.SkipReason)
    assert "No upstream" in result.skip_message


def test_verify_and_card_assets_stage_real_outputs(
    dag_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dakp_pipeline.release.verify import ReleaseVerification

    _write_gate_delta(dag_env.published)
    monkeypatch.setenv("DAKP_PUBLICATION_VERSION", "1.27.1")
    kg_dir = dag_env.published / "dakp" / "kg"
    kg_dir.mkdir(parents=True)
    nodes = kg_dir / assets.KG_NODES
    edges = kg_dir / assets.KG_EDGES
    nodes.write_text(
        json.dumps({"id": "CHEBI:1", "category": ["biolink:ChemicalEntity"]})
        + "\n"
        + json.dumps({"id": "MONDO:1", "category": ["biolink:Disease"]})
        + "\n",
        encoding="utf-8",
    )
    edges.write_text(
        json.dumps(
            {
                "subject": "CHEBI:1",
                "predicate": "biolink:approved_for_treatment",
                "object": "MONDO:1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rig_dir = Workdir(dag_env.workdir).kgx
    rig_dir.mkdir(parents=True, exist_ok=True)
    rig = rig_dir / "DRUG_APPROVALS_KP_1.27.1.RIG.yaml"
    rig.write_text(
        "version: 1.27.1\nbiolink_version: 4.4.6\nsources: [infores:icd10cm]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        release,
        "verify_release",
        lambda **kwargs: ReleaseVerification(
            version="1.27.1",
            nodes=str(nodes),
            edges=str(edges),
            rig=str(rig),
            validate_kgx_stdout="ok",
            screened_hits=(),
            ok=True,
        ),
    )
    previous = dag_env.tmp / "previous-card.md"
    previous.write_text("---\nlicense: apache-2.0\n---\nold card\n", encoding="utf-8")

    class HF:
        def hf_hub_download(self, **kwargs):
            return str(previous)

    monkeypatch.setattr(release, "_hf_api", lambda settings: HF())
    result = dg.materialize(release.release_output_assets)
    assert result.success
    version_dir = dag_env.published / "dakp" / "release" / "staging" / "1.27.1"
    assert {path.name for path in version_dir.iterdir()} == {
        "DRUG_APPROVALS_KP_1.27.1.nodes.ndjson",
        "DRUG_APPROVALS_KP_1.27.1.edges.ndjson",
        "DRUG_APPROVALS_KP_1.27.1.RIG.yaml",
    }
    card = (version_dir.parent / "README.md").read_text(encoding="utf-8")
    assert "2 nodes · 1 edges" in card
    assert "New data for 1 source" in card


def _hf_publish_metadata(result) -> dict:
    event = next(
        event
        for event in result.get_asset_materialization_events()
        if event.event_specific_data.materialization.asset_key.to_user_string() == "hf_publish"
    )
    return event.event_specific_data.materialization.metadata


@pytest.mark.parametrize("dry_run", [False, True])
def test_hf_publish_asset_updates_state_only_after_live_verified_upload(
    dag_env, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    from dakp_pipeline.release.hf_publish import PublishResult
    from dakp_pipeline.release.state import load_state

    _write_gate_delta(dag_env.published)
    monkeypatch.setenv("DAKP_PUBLICATION_VERSION", "1.27.1")
    monkeypatch.setenv("DAKP_PUBLISH_DRY_RUN", "1" if dry_run else "0")
    kg_dir = dag_env.published / "dakp" / "kg"
    kg_dir.mkdir(parents=True)
    (kg_dir / assets.QC_REPORT).write_text(
        json.dumps({"nodes": 2, "edges": 1, "dangling_edges": 0}), encoding="utf-8"
    )
    monkeypatch.setattr(release, "_hf_api", lambda settings: object())
    monkeypatch.setattr(
        release,
        "publish_release",
        lambda *args, **kwargs: PublishResult(
            version="1.27.1",
            dry_run=dry_run,
            actions=("upload", "card", "tag"),
            superseded=("1.27.0",),
            verified=not dry_run,
        ),
    )
    result = dg.materialize([release.hf_publish])
    assert result.success
    metadata = _hf_publish_metadata(result)
    version_dir = dag_env.published / "dakp" / "release" / "staging" / "1.27.1"
    assert metadata["linkouts_key"].value == "infores:drugapprovals-kp-1.27.1"
    assert metadata["linkouts_nodes_path"].value == str(
        version_dir / "DRUG_APPROVALS_KP_1.27.1.nodes.ndjson"
    )
    assert metadata["linkouts_edges_path"].value == str(
        version_dir / "DRUG_APPROVALS_KP_1.27.1.edges.ndjson"
    )
    state = load_state(state_path(dag_env.published))
    if dry_run:
        assert state is None
    else:
        assert state is not None
        assert state.version == "1.27.1"
        assert state.kg == {
            "nodes": 2,
            "edges": 1,
            "message": state.kg["message"],
            "verified": True,
        }


def test_failure_sensor_builds_step_stats_and_sanitizes_through_issue_client(
    dag_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    _write_gate_delta(dag_env.published)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    logs = [
        SimpleNamespace(
            step_key="extract_faers",
            timestamp=100.0,
            message="started",
            dagster_event=SimpleNamespace(event_type=dg.DagsterEventType.STEP_START),
        ),
        SimpleNamespace(
            step_key="extract_faers",
            timestamp=160.0,
            message="done",
            dagster_event=SimpleNamespace(event_type=dg.DagsterEventType.STEP_SUCCESS),
        ),
        SimpleNamespace(
            step_key="shape_contraindications",
            timestamp=200.0,
            message="NER log line",
            dagster_event=SimpleNamespace(event_type=dg.DagsterEventType.STEP_START),
        ),
        SimpleNamespace(
            step_key="shape_contraindications",
            timestamp=260.0,
            message="final error log",
            dagster_event=SimpleNamespace(event_type=dg.DagsterEventType.STEP_FAILURE),
        ),
    ]
    failures = [
        SimpleNamespace(
            step_key="shape_contraindications",
            timestamp=260.0,
            message="shape failed",
            step_failure_data=SimpleNamespace(
                error=SimpleNamespace(to_string=lambda: "RuntimeError: NER exploded\nstack line")
            ),
        )
    ]
    run = SimpleNamespace(
        run_id="run-failed",
        job_name="dakp_release_build",
        tags={"dakp/publication_version": "1.27.1", "git/commit": "abc"},
    )
    context = SimpleNamespace(
        dagster_run=run,
        instance=SimpleNamespace(all_logs=lambda run_id: logs),
        get_step_failure_events=lambda: failures,
        failure_event=SimpleNamespace(message="run failed"),
    )
    monkeypatch.setattr(
        automation, "probe_gpus", lambda: GPUStatus((GPUDevice(0, "P100", 0, 16384),))
    )
    captured = []

    class Issues:
        def __init__(self, repo, token):
            assert repo == "glusman-team/graph-monorepo"
            assert token == "test-token"

        def create_or_comment(self, event):
            captured.append(event)
            return {"action": "created"}

    monkeypatch.setattr(automation, "GitHubIssueClient", Issues)
    assert automation._report_run_failure(context) == {"action": "created"}
    event = captured[0]
    assert event.run_id == "run-failed"
    assert event.version == "1.27.1"
    assert [step.status for step in event.steps] == ["SUCCESS", "FAILURE"]
    assert event.steps[0].duration_seconds == 60.0
    assert "NER exploded" in event.failed_steps[0].error
    assert "final error log" in event.failed_steps[0].log_tail


def test_gpu_gate_skips_before_any_check_and_for_failed_check(
    dag_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = automation._gpu_gate_tick(_sensor_context())
    assert isinstance(missing, dg.SkipReason)
    assert "No release check" in missing.skip_message

    _write_gate_delta(dag_env.published)
    monkeypatch.setattr(
        automation, "probe_gpus", lambda: pytest.fail("failed check run probed GPUs")
    )
    failed = automation._gpu_gate_tick(
        _sensor_context(status=dg.DagsterRunStatus.FAILURE),
        now=datetime.fromisoformat("2026-11-06T18:00:00-08:00"),
    )
    assert isinstance(failed, dg.SkipReason)
    assert "only a successful check" in failed.skip_message


def test_gpu_timeout_issue_failure_does_not_wedge_the_sensor(
    dag_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_gate_delta(dag_env.published)

    def boom(**kwargs):
        raise RuntimeError("GITHUB_TOKEN is required")

    monkeypatch.setattr(automation, "_gpu_timeout_issue", boom)
    deadline = datetime.fromisoformat("2026-11-07T17:00:00-08:00")
    context = _sensor_context()
    first = automation._gpu_gate_tick(context, now=deadline)
    assert isinstance(first, dg.SkipReason)
    assert "issue filing failed" in first.skip_message
    assert "check-run-1" in context.cursor
    second = automation._gpu_gate_tick(_sensor_context(context.cursor), now=deadline)
    assert "already consumed" in second.skip_message

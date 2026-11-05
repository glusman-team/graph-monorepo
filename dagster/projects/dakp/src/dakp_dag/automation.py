"""Schedules and sensors for unattended DAKP releases."""

import json
from datetime import UTC, datetime, timedelta

import dagster as dg
from dagster import RunFailureSensorContext, ScheduleEvaluationContext, SensorEvaluationContext
from dakp_pipeline.release.delta import load_pending_delta
from dakp_pipeline.release.github_issues import FailureEvent, GitHubIssueClient, StepStat
from dakp_pipeline.release.gpu import evaluate_gpu_gate, probe_gpus
from dakp_pipeline.release.versions import next_hf_patch_version

from dakp_dag.config import PUBLICATION_VERSION_TAG, release_settings, run_config
from dakp_dag.release import dakp_release_build, dakp_release_check

_SETTINGS = release_settings()
_DEFAULT_STATUS = (
    dg.DefaultScheduleStatus.RUNNING if _SETTINGS.enabled else dg.DefaultScheduleStatus.STOPPED
)
_DEFAULT_SENSOR_STATUS = (
    dg.DefaultSensorStatus.RUNNING if _SETTINGS.enabled else dg.DefaultSensorStatus.STOPPED
)


@dg.schedule(
    name="dakp_weekly_release",
    job=dakp_release_check,
    cron_schedule=_SETTINGS.cron,
    execution_timezone=_SETTINGS.timezone,
    default_status=_DEFAULT_STATUS,
)
def weekly_release_schedule(context: ScheduleEvaluationContext) -> dg.RunRequest:
    tick = context.scheduled_execution_time
    run_key = f"dakp-release-check-{tick:%G-W%V}" if tick else None
    return dg.RunRequest(
        run_key=run_key,
        tags={"dakp/release": "check", "dakp/scheduled": "weekly"},
    )


def _cursor(context: SensorEvaluationContext) -> dict:
    if not context.cursor:
        return {}
    return json.loads(context.cursor)


def _hf_api(settings):
    from huggingface_hub import HfApi

    return HfApi(token=settings.hf_token)


def _gpu_timeout_issue(*, delta, deadline: datetime, reason: str) -> None:
    from dakp_pipeline.release.github_issues import report_gpu_timeout

    settings = release_settings()
    report_gpu_timeout(
        repo=settings.github_repo,
        token=settings.github_token,
        check_run_id=delta.check_run_id or "unknown",
        deadline=deadline.isoformat(),
        reason=reason,
        dagster_url=settings.dagster_url,
    )


def _gpu_gate_tick(context: SensorEvaluationContext, now: datetime | None = None):
    settings = release_settings()
    cursor = _cursor(context)
    try:
        delta = load_pending_delta(run_config().published_root)
    except FileNotFoundError:
        return dg.SkipReason("No release check has produced a pending delta yet.")
    run_id = delta.check_run_id
    if not delta.changed:
        if run_id and cursor.get("consumed_run_id") != run_id:
            cursor["consumed_run_id"] = run_id
            context.update_cursor(json.dumps(cursor, sort_keys=True))
        return dg.SkipReason("No upstream source artifacts changed; no build or upload.")

    if not run_id:
        raise RuntimeError("changed pending delta has no check_run_id")
    if cursor.get("consumed_run_id") == run_id:
        return dg.SkipReason(f"Release check run {run_id} was already consumed.")
    record = context.instance.get_run_record_by_id(run_id)
    if record is None or record.dagster_run.status != dg.DagsterRunStatus.SUCCESS:
        status = record.dagster_run.status.value if record is not None else "missing"
        return dg.SkipReason(
            f"Release check run {run_id} is {status}; only a successful check can launch a build."
        )

    started = datetime.fromisoformat(delta.check_started_at or delta.created_at)
    deadline = started + timedelta(hours=settings.gpu_gate_hours)
    now = now or datetime.now(UTC)
    if now >= deadline:
        if cursor.get("timeout_issue_run_id") != run_id:
            reason = (
                f"GPUs did not reach the <= {settings.gpu_max_used_mib} MiB/card gate "
                f"within {settings.gpu_gate_hours} hours"
            )
            cursor["timeout_issue_run_id"] = run_id
            cursor["consumed_run_id"] = run_id
            context.update_cursor(json.dumps(cursor, sort_keys=True))
            try:
                _gpu_timeout_issue(delta=delta, deadline=deadline, reason=reason)
            except (OSError, RuntimeError, ValueError) as exc:
                context.log.error(f"GPU timeout issue could not be filed: {exc}")
                return dg.SkipReason(
                    f"GPU gate deadline passed at {deadline.isoformat()}; issue filing failed: {exc}"
                )
        return dg.SkipReason(f"GPU gate deadline passed at {deadline.isoformat()}.")

    gate = evaluate_gpu_gate(probe_gpus(), settings.gpu_max_used_mib)
    if not gate.free:
        return dg.SkipReason(gate.reason)

    version = next_hf_patch_version(_hf_api(settings), settings.hf_repo_id)
    cursor["consumed_run_id"] = run_id
    cursor["launched_version"] = version
    context.update_cursor(json.dumps(cursor, sort_keys=True))
    return dg.RunRequest(
        run_key=run_id,
        tags={
            "dakp/release": "build",
            "dakp/check_run_id": run_id,
            PUBLICATION_VERSION_TAG: version,
        },
    )


@dg.sensor(
    name="dakp_gpu_gate",
    job=dakp_release_build,
    minimum_interval_seconds=3600,
    default_status=_DEFAULT_SENSOR_STATUS,
)
def gpu_gate_sensor(context: SensorEvaluationContext):
    return _gpu_gate_tick(context)


def _failure_step_stats(context: RunFailureSensorContext) -> tuple[StepStat, ...]:
    records = context.instance.all_logs(context.dagster_run.run_id)
    grouped: dict[str, dict] = {}
    for record in records:
        step = record.step_key
        if not step:
            continue
        row = grouped.setdefault(step, {"messages": []})
        event_type = record.dagster_event.event_type if record.dagster_event else None
        if event_type == dg.DagsterEventType.STEP_START:
            row["started"] = record.timestamp
            row.setdefault("status", "STARTED")
        elif event_type in {
            dg.DagsterEventType.STEP_SUCCESS,
            dg.DagsterEventType.STEP_FAILURE,
            dg.DagsterEventType.STEP_SKIPPED,
        }:
            row["ended"] = record.timestamp
            row["status"] = event_type.value.removeprefix("STEP_")
        if record.message:
            row["messages"].append(record.message)

    for failure in context.get_step_failure_events():
        step = failure.step_key or "run"
        row = grouped.setdefault(step, {"messages": []})
        row["status"] = "FAILURE"
        failure_data = failure.step_failure_data
        error = (
            failure_data.error.to_string()
            if failure_data is not None and failure_data.error
            else failure.message
        )
        row["error"] = error

    stats = []
    for step, row in grouped.items():
        started, ended = row.get("started"), row.get("ended")
        stats.append(
            StepStat(
                name=step,
                status=row.get("status", "FAILURE"),
                started_at=datetime.fromtimestamp(started, UTC).isoformat() if started else "",
                ended_at=datetime.fromtimestamp(ended, UTC).isoformat() if ended else "",
                duration_seconds=max(0.0, ended - started) if started and ended else None,
                error=row.get("error", ""),
                log_tail="\n".join(row["messages"][-80:]),
            )
        )
    if not stats:
        stats.append(
            StepStat(
                name="run",
                status="FAILURE",
                error=context.failure_event.message,
                log_tail=context.failure_event.message,
            )
        )
    return tuple(sorted(stats, key=lambda step: step.started_at))


def _report_run_failure(context: RunFailureSensorContext):
    settings = release_settings()
    run = context.dagster_run
    try:
        delta_message = load_pending_delta(run_config().published_root).message
    except (OSError, ValueError, KeyError) as exc:
        delta_message = f"pending delta unavailable: {exc}"
    try:
        gpu_summary = json.dumps(probe_gpus().to_dict(), indent=2)
    except (OSError, RuntimeError, ValueError) as exc:
        gpu_summary = f"GPU probe unavailable: {exc}"
    event = FailureEvent(
        job_name=run.job_name,
        run_id=run.run_id,
        run_url=f"{settings.dagster_url}/runs/{run.run_id}",
        git_sha=run.tags.get("git/commit", "unknown"),
        version=run.tags.get(PUBLICATION_VERSION_TAG, "not assigned"),
        delta_message=delta_message,
        gpu_summary=gpu_summary,
        steps=_failure_step_stats(context),
    )
    return GitHubIssueClient(settings.github_repo, settings.github_token).create_or_comment(event)


@dg.run_failure_sensor(
    name="dakp_build_failure_issues",
    monitored_jobs=[dakp_release_check, dakp_release_build],
    default_status=_DEFAULT_SENSOR_STATUS,
)
def build_failure_sensor(context: RunFailureSensorContext) -> None:
    result = _report_run_failure(context)
    context.log.info("DAKP failure issue: %s", result)


release_schedules = [weekly_release_schedule]
release_sensors = [gpu_gate_sensor, build_failure_sensor]

__all__ = [
    "build_failure_sensor",
    "gpu_gate_sensor",
    "release_schedules",
    "release_sensors",
    "weekly_release_schedule",
]

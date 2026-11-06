from __future__ import annotations

from linkouts_dag.assets import all_assets
from linkouts_dag.automation import (
    LinkoutsAutomationSettings,
    build_dakp_release_sensor,
)
from linkouts_dag.definitions import defs


def test_code_location_loads_complete_manual_surface() -> None:
    assert defs.resolve_job_def("linkouts_upload")
    assert defs.resolve_job_def("linkouts_stage_release")
    assert defs.resolve_job_def("linkouts_load_release")
    assert defs.resolve_job_def("linkouts_push_staged_release")
    assert len(all_assets) == 11
    assert len(defs.sensors) == 1
    assert not defs.schedules


def test_push_has_lineage_dependency_but_narrow_job_selects_only_push() -> None:
    push = next(
        asset for asset in all_assets if asset.key.to_user_string() == "push_staged_release"
    )
    assert {key.to_user_string() for key in push.dependency_keys} == {"stage_release"}
    job = defs.resolve_job_def("linkouts_push_staged_release")
    assert job.asset_layer.selected_asset_keys == {push.key}


def test_dakp_release_sensor_defaults_stopped_and_can_be_enabled() -> None:
    stopped = build_dakp_release_sensor(LinkoutsAutomationSettings())
    running = build_dakp_release_sensor(LinkoutsAutomationSettings(enabled=True))
    assert stopped.default_status.name == "STOPPED"
    assert running.default_status.name == "RUNNING"


def test_load_has_external_hf_publish_lineage_but_narrow_job_selects_only_load() -> None:
    load = next(asset for asset in all_assets if asset.key.to_user_string() == "load_release")
    assert {key.to_user_string() for key in load.dependency_keys} == {"hf_publish"}
    job = defs.resolve_job_def("linkouts_load_release")
    assert job.asset_layer.selected_asset_keys == {load.key}


def test_destructive_jobs_are_not_registered_by_default() -> None:
    assert not any("purge" in job.name for job in defs.get_repository_def().get_all_jobs())

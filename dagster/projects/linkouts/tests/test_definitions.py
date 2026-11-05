from __future__ import annotations

from linkouts_dag.assets import all_assets
from linkouts_dag.definitions import defs


def test_code_location_loads_complete_manual_surface() -> None:
    assert defs.resolve_job_def("linkouts_upload")
    assert defs.resolve_job_def("linkouts_stage_release")
    assert defs.resolve_job_def("linkouts_push_staged_release")
    assert len(all_assets) == 10
    assert not defs.sensors
    assert not defs.schedules


def test_push_has_lineage_dependency_but_narrow_job_selects_only_push() -> None:
    push = next(
        asset for asset in all_assets if asset.key.to_user_string() == "push_staged_release"
    )
    assert {key.to_user_string() for key in push.dependency_keys} == {"stage_release"}
    job = defs.resolve_job_def("linkouts_push_staged_release")
    assert job.asset_layer.selected_asset_keys == {push.key}


def test_destructive_jobs_are_not_registered_by_default() -> None:
    assert not any("purge" in job.name for job in defs.get_repository_def().get_all_jobs())

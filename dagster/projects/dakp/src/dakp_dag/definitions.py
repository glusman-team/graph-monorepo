"""Code-location entrypoint. dg loads the `defs` symbol (see pyproject [tool.dg])."""

import dagster as dg

from dakp_dag import assets
from dakp_dag.automation import release_schedules, release_sensors
from dakp_dag.release import (
    dakp_release_build,
    dakp_release_check,
    gpu_release_check,
    hf_publish,
    release_check_assets,
    release_output_assets,
)

defs = dg.Definitions(
    assets=[
        *assets.all_assets,
        *release_check_assets,
        *release_output_assets,
        gpu_release_check,
        hf_publish,
    ],
    jobs=[*assets.all_jobs, dakp_release_check, dakp_release_build],
    schedules=release_schedules,
    sensors=release_sensors,
    resources=assets.resources,
)

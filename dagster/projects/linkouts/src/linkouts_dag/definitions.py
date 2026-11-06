"""Code-location entrypoint loaded by dg and workspace.yaml."""

import dagster as dg

from linkouts_dag import assets
from linkouts_dag.config import run_config


def build_definitions() -> dg.Definitions:
    """Keep destructive assets absent unless explicitly enabled at code-location load."""
    enabled = run_config().destructive_jobs_enabled
    return dg.Definitions(
        assets=[*assets.all_assets, *(assets.destructive_assets if enabled else [])],
        jobs=[*assets.all_jobs, *([assets.linkouts_purge] if enabled else [])],
        resources=assets.resources,
    )


defs = build_definitions()

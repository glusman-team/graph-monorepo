"""Dagster-free Linkouts worker and artifact contracts."""

from linkouts_pipeline.operations import WorkerRequest
from linkouts_pipeline.refs import ArtifactRef
from linkouts_pipeline.staging import StageManifest

__all__ = ["ArtifactRef", "StageManifest", "WorkerRequest"]

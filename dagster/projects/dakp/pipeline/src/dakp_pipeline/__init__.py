"""DAKP pipeline core: content-addressed, dagster-free pipeline stages.

Port of the DAKP v1.25.0 pipeline package for the graph-monorepo Dagster
deployment. This package never imports dagster (or any orchestrator): stages
communicate through BLAKE3 content-addressed ArtifactRefs and the operation
index, so the same code runs under dagster assets, tests, or a plain CLI.
"""

__all__: list[str] = []

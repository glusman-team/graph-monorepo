"""Typed Dagster launch configuration shared by the asset modules."""

import dagster as dg


class StageRunConfig(dg.Config):
    key: str
    nodes_path: str
    edges_path: str
    base_key: str | None = None
    dictionary_path: str | None = None
    zstd_level: int = 0
    no_repack: bool = False
    restart: bool = False
    sample_size: int = 1024
    sample_seed: int = 0
    sampled_at: str | None = None
    concurrency: int = 8
    threads: int = 0
    engine: str = "chdb"
    progress: bool = True


class PushRunConfig(dg.Config):
    expected_key: str | None = None
    target_store: str = "cosmos"
    concurrency: int = 8
    confirmation: str | None = None


class ValidationRunConfig(StageRunConfig):
    ids_path: str | None = None
    sample_full_release: bool = False


__all__ = ["PushRunConfig", "StageRunConfig", "ValidationRunConfig"]

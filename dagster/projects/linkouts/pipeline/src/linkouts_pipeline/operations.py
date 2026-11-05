"""Typed, secret-free operation payloads accepted by the hidden Go worker."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from linkouts_pipeline.refs import ContractModel

type Operation = Literal[
    "stage", "push", "train_dict", "status", "probe", "get", "rig", "init", "purge"
]


def _validate_store(value: str) -> str:
    if value == "cosmos" or value == "mem://" or value.startswith("file:"):
        if value == "file:":
            msg = "file store requires a path"
            raise ValueError(msg)
        return value
    msg = "store must be 'cosmos', 'file:<path>', or 'mem://'"
    raise ValueError(msg)


class StoreConfig(ContractModel):
    store: str = "cosmos"
    dictionary_path: Path | None = None

    @field_validator("store")
    @classmethod
    def valid_store(cls, value: str) -> str:
        return _validate_store(value)


class StageConfig(ContractModel):
    operation: Literal["stage"] = "stage"
    key: str = Field(min_length=1)
    nodes_path: Path
    edges_path: Path
    workdir: Path
    base_key: str | None = None
    dictionary_path: Path | None = None
    zstd_level: int = Field(default=0, ge=0, le=22)
    no_repack: bool = False
    restart: bool = False
    ids_path: Path | None = None
    sample_full_release: bool = False
    dry_run: bool = False
    sample_size: int = Field(default=1024, ge=0)
    sample_seed: int = 0
    sampled_at: datetime | None = None
    concurrency: int = Field(default=8, ge=1)
    threads: int = Field(default=0, ge=0)
    engine: Literal["chdb", "fake"] = "chdb"
    progress: bool = True

    def validate_for_publication(self) -> StageConfig:
        """Reject diagnostic/subset modes from the production upload graph."""
        if self.dry_run:
            msg = "dry-run staging is validation-only; use linkouts_validate_release"
            raise ValueError(msg)
        if self.ids_path is not None or self.sample_full_release:
            msg = "ID-filtered staging is diagnostic-only and cannot be published"
            raise ValueError(msg)
        if self.sample_size == 0:
            msg = "production staging requires a random-pool sample"
            raise ValueError(msg)
        return self


class PushConfig(ContractModel):
    operation: Literal["push"] = "push"
    manifest_path: Path
    report_path: Path
    expected_key: str | None = None
    target_store: str = "cosmos"
    dry_run: bool = False
    concurrency: int = Field(default=8, ge=1)
    confirmation: str | None = None

    @field_validator("target_store")
    @classmethod
    def valid_target(cls, value: str) -> str:
        if value == "mem://":
            msg = "a publication target must survive the worker process"
            raise ValueError(msg)
        return _validate_store(value)


class TrainDictConfig(ContractModel):
    operation: Literal["train_dict"] = "train_dict"
    nodes_path: Path
    edges_path: Path
    output_path: Path
    report_path: Path
    samples: int = Field(default=4096, ge=1)
    threads: int = Field(default=0, ge=0)
    engine: Literal["chdb", "fake"] = "chdb"


class StatusConfig(ContractModel):
    operation: Literal["status"] = "status"
    store: str = "cosmos"
    output_path: Path
    dictionary_path: Path | None = None
    check_pools: bool = False

    @field_validator("store")
    @classmethod
    def valid_store(cls, value: str) -> str:
        return _validate_store(value)


class ProbeConfig(StoreConfig):
    operation: Literal["probe"] = "probe"
    output_path: Path
    count: int = Field(default=20, ge=1)
    seed: int | None = None
    max_mean_ru: float | None = Field(default=None, gt=0)
    max_worst_ru: float | None = Field(default=None, gt=0)


class GetConfig(StoreConfig):
    operation: Literal["get"] = "get"
    edge_id: str = Field(min_length=1)
    output_path: Path
    version_key: str | None = None
    list_versions: bool = False
    raw: bool = False

    @model_validator(mode="after")
    def one_output_mode(self) -> GetConfig:
        if self.raw and self.list_versions:
            msg = "raw and list_versions are mutually exclusive"
            raise ValueError(msg)
        return self


class RigConfig(ContractModel):
    operation: Literal["rig"] = "rig"
    edges_path: Path
    output_path: Path
    limit: int = Field(default=20000, ge=0)
    predicate: str | None = None
    emit_config: bool = False


class InitConfig(ContractModel):
    operation: Literal["init"] = "init"
    store: str = "cosmos"
    output_path: Path

    @field_validator("store")
    @classmethod
    def valid_store(cls, value: str) -> str:
        return _validate_store(value)


class PurgeConfig(StoreConfig):
    operation: Literal["purge"] = "purge"
    output_path: Path
    scope: Literal["all", "kg", "release", "container"]
    kg: str | None = None
    key: str | None = None
    dry_run: bool = False
    confirmation: str | None = None

    @model_validator(mode="after")
    def exact_scope_and_confirmation(self) -> PurgeConfig:
        if self.scope == "kg" and (not self.kg or self.key):
            msg = "scope='kg' requires kg and forbids key"
            raise ValueError(msg)
        if self.scope == "release" and (not self.key or self.kg):
            msg = "scope='release' requires key and forbids kg"
            raise ValueError(msg)
        if self.scope in {"all", "container"} and (self.kg or self.key):
            msg = f"scope={self.scope!r} forbids kg and key"
            raise ValueError(msg)
        if self.scope == "container" and self.dry_run:
            msg = "container deletion has no dry-run preview"
            raise ValueError(msg)
        if not self.dry_run and not self.confirmation:
            msg = "a destructive run requires exact target confirmation"
            raise ValueError(msg)
        return self


type OperationConfig = (
    StageConfig
    | PushConfig
    | TrainDictConfig
    | StatusConfig
    | ProbeConfig
    | GetConfig
    | RigConfig
    | InitConfig
    | PurgeConfig
)

_CONFIG_TYPES: dict[str, type[ContractModel]] = {
    "stage": StageConfig,
    "push": PushConfig,
    "train_dict": TrainDictConfig,
    "status": StatusConfig,
    "probe": ProbeConfig,
    "get": GetConfig,
    "rig": RigConfig,
    "init": InitConfig,
    "purge": PurgeConfig,
}


class WorkerRequest(ContractModel):
    """Pipes extras envelope; ``config_json`` contains only a typed operation model."""

    operation: Operation
    config_json: str = Field(min_length=2)

    @model_validator(mode="after")
    def config_matches_operation(self) -> WorkerRequest:
        parsed = self.parsed_config()
        if parsed.operation != self.operation:
            msg = "worker envelope operation does not match its config"
            raise ValueError(msg)
        return self

    @classmethod
    def for_config(cls, config: OperationConfig) -> WorkerRequest:
        return cls(operation=config.operation, config_json=config.model_dump_json())

    def parsed_config(self) -> OperationConfig:
        config_type = _CONFIG_TYPES[self.operation]
        return config_type.model_validate_json(self.config_json)

    def extras(self) -> dict[str, str]:
        return {"operation": self.operation, "config_json": self.config_json}


__all__ = [
    "GetConfig",
    "InitConfig",
    "Operation",
    "OperationConfig",
    "ProbeConfig",
    "PurgeConfig",
    "PushConfig",
    "RigConfig",
    "StageConfig",
    "StatusConfig",
    "TrainDictConfig",
    "WorkerRequest",
]

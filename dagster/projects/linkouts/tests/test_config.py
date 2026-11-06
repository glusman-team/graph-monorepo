from __future__ import annotations

from pathlib import Path

from linkouts_dag.config import PROJECT_ROOT, run_config


def test_defaults_stay_inside_project(monkeypatch) -> None:
    monkeypatch.delenv("MO_PUBLISHED_ROOT", raising=False)
    monkeypatch.delenv("LINKOUTS_WORKDIR", raising=False)
    monkeypatch.delenv("LINKOUTS_WORKER_BIN", raising=False)
    monkeypatch.delenv("LINKOUTS_ENABLE_DESTRUCTIVE_JOBS", raising=False)
    config = run_config()
    assert config.published_root == PROJECT_ROOT / "published"
    assert config.workdir == PROJECT_ROOT / "published" / "linkouts" / "workdir"
    assert config.worker_bin == PROJECT_ROOT / "workers" / "go" / "bin" / "linkouts-worker"
    assert not config.destructive_jobs_enabled


def test_environment_overrides_paths_not_secrets(monkeypatch, tmp_path: Path) -> None:
    worker = tmp_path / "worker"
    monkeypatch.setenv("MO_PUBLISHED_ROOT", str(tmp_path / "published"))
    monkeypatch.setenv("LINKOUTS_WORKDIR", str(tmp_path / "work"))
    monkeypatch.setenv("LINKOUTS_WORKER_BIN", str(worker))
    monkeypatch.setenv("LINKOUTS_ENABLE_DESTRUCTIVE_JOBS", "true")
    config = run_config()
    assert config.published_root == tmp_path / "published"
    assert config.workdir == tmp_path / "work"
    assert config.run_dir == tmp_path / "work" / "runs"
    assert config.worker_bin == worker
    assert config.destructive_jobs_enabled

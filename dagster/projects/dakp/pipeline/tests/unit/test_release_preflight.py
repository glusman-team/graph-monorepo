"""Release preflight validates real inputs and fails before acquisition."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dakp_pipeline.release.preflight import PreflightError, run_preflight


class _Config:
    def __init__(self, root: Path, **overrides):
        fullmap = root / "fullmap.redb"
        fullmap.write_bytes(b"fullmap")
        worker = root / "dakp-extract"
        worker.write_text("#!/bin/sh\n", encoding="utf-8")
        worker.chmod(0o755)
        fixtures = root / "fixtures"
        fixtures.mkdir()
        self.fullmap = str(fullmap)
        self.worker_bin = worker
        self.fixture_root = fixtures
        self.workdir = root / "work"
        self.published_root = root / "published"
        self.ner_offline = False
        self.quarter_limit = 0
        self.release_limit = 0
        self.source_refresh = "auto"
        self.force = False
        for key, value in overrides.items():
            setattr(self, key, value)

    def params(self):
        return {"source_refresh": self.source_refresh}


class _HF:
    def whoami(self):
        return {"name": "SkyeAv"}

    def dataset_info(self, repo_id):
        return {"id": repo_id}


def _runner(head: str = "abc", remote: str = "abc"):
    def run(command, **kwargs):
        if command[1] == "fetch":
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1] == "rev-parse":
            value = head if command[2] == "HEAD" else remote
            return subprocess.CompletedProcess(command, 0, value + "\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def _preflight(config, root: Path, **overrides):
    kwargs = {
        "repo_root": root,
        "hf_repo_id": "SkyeAv/drug-approvals-kp",
        "github_repo": "glusman-team/graph-monorepo",
        "hf_token": "hf-secret",
        "github_token": "gh-secret",
        "min_free_gb": 0,
        "runner": _runner(),
        "hf_api": _HF(),
        "github_check": lambda repo, token: {"id": 1},
    }
    kwargs.update(overrides)
    return run_preflight(config, **kwargs)


def test_preflight_success_checks_inputs_tokens_and_main(tmp_path: Path) -> None:
    report = _preflight(_Config(tmp_path), tmp_path)
    assert report.git_sha == "abc"
    assert report.hf_user == "SkyeAv"
    assert report.failures == []
    assert report.checks["tracks_main"] == "HEAD=abc origin/main=abc"


def test_preflight_aggregates_failures_without_leaking_tokens(tmp_path: Path) -> None:
    config = _Config(
        tmp_path,
        fullmap=None,
        ner_offline=True,
        quarter_limit=1,
        release_limit=2,
        source_refresh="cached",
    )
    config.worker_bin = tmp_path / "missing-worker"
    with pytest.raises(PreflightError) as caught:
        _preflight(
            config,
            tmp_path,
            hf_token=None,
            github_token=None,
            runner=_runner(head="old", remote="new"),
        )
    message = str(caught.value)
    for expected in (
        "DAKP_FULLMAP",
        "extraction worker",
        "DAKP_NER_OFFLINE",
        "DAKP_QUARTER_LIMIT",
        "DAKP_RELEASE_LIMIT",
        "SOURCE_REFRESH=auto",
        "HF_TOKEN",
        "GITHUB_TOKEN",
        "origin/main",
    ):
        assert expected in message
    assert "gh-secret" not in message
    assert "hf-secret" not in message


def test_preflight_fails_on_tracked_source_drift(tmp_path: Path) -> None:
    def dirty_runner(command, **kwargs):
        result = _runner()(command, **kwargs)
        if command[1] == "diff":
            result = subprocess.CompletedProcess(command, 1, "", "")
        return result

    with pytest.raises(PreflightError, match="clean main"):
        _preflight(_Config(tmp_path), tmp_path, runner=dirty_runner)


def test_preflight_checks_required_managed_nercache(tmp_path: Path) -> None:
    with pytest.raises(PreflightError, match="NER cache"):
        _preflight(
            _Config(tmp_path),
            tmp_path,
            nercache_health=lambda: (_ for _ in ()).throw(RuntimeError("connection refused")),
        )


def test_preflight_requires_disk_headroom(tmp_path: Path) -> None:
    with pytest.raises(PreflightError, match="GiB free"):
        _preflight(_Config(tmp_path), tmp_path, min_free_gb=1e12)


def test_preflight_rejects_global_force(tmp_path: Path) -> None:
    config = _Config(tmp_path)
    config.force = True
    with pytest.raises(PreflightError, match="DAKP_FORCE"):
        _preflight(config, tmp_path)

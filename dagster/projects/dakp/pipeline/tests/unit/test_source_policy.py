"""Shared acquisition policy: freshness, force, and strict cached-only builds."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dakp_pipeline.io.contracts import TaskContext
from dakp_pipeline.io.refs import REFS_FILE_MEDIA_TYPE
from dakp_pipeline.paths import Workdir
from dakp_pipeline.sources import dailymed, drugsfda
from dakp_pipeline.sources.policy import source_policy


def _ctx(workdir: Path, **params: object) -> TaskContext:
    return TaskContext(workdir=workdir, fixture_root=None, params=dict(params))


def test_source_policy_modes_and_validation(tmp_path: Path) -> None:
    auto = source_policy(_ctx(tmp_path), 7.0)
    assert auto.reusable(6.9)
    assert not auto.reusable(7.0)
    # Non-positive env value => None => always re-check upstream in auto mode.
    assert source_policy(_ctx(tmp_path), None).reusable(0.1) is False
    assert source_policy(_ctx(tmp_path, source_refresh="cached"), 0).reusable(None)
    assert source_policy(_ctx(tmp_path, source_refresh="force"), 30).force
    assert source_policy(_ctx(tmp_path, force=True), 30).force
    with pytest.raises(ValueError, match="auto/cached/force"):
        source_policy(_ctx(tmp_path, source_refresh="sometimes"), 7)


def test_cached_fixed_url_source_never_calls_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "work"
    Workdir(workdir).create()
    fixture = tmp_path / "drugsfda.zip"
    fixture.write_bytes(b"cached source bytes")

    def fake_download(url: str, dest: Path, **kwargs: object) -> Path:
        shutil.copyfile(fixture, dest)
        return dest

    monkeypatch.setattr(drugsfda, "download_drugsfda_zip", fake_download)
    first = drugsfda.fetch(_ctx(workdir))
    monkeypatch.setattr(
        drugsfda,
        "download_drugsfda_zip",
        lambda *args, **kwargs: pytest.fail("cached-only Drugs@FDA touched the network"),
    )
    second = drugsfda.fetch(_ctx(workdir, source_refresh="cached"))
    assert second[0].blake3 == first[0].blake3


def test_cached_fixed_url_source_fails_loudly_when_missing(tmp_path: Path) -> None:
    Workdir(tmp_path).create()
    with pytest.raises(FileNotFoundError, match=r"Drugs@FDA.*cached-only"):
        drugsfda.fetch(_ctx(tmp_path, source_refresh="cached"))


def test_cached_dailymed_uses_published_refs_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Workdir(tmp_path).create()
    sentinel = dailymed.write_refs_manifest(_ctx(tmp_path), [])
    monkeypatch.setattr(
        dailymed,
        "_fetch_index",
        lambda *args, **kwargs: pytest.fail("cached-only DailyMed fetched its index"),
    )
    refs = dailymed.fetch(_ctx(tmp_path, source_refresh="cached"))
    assert len(refs) == 1
    assert refs[0].blake3 == sentinel.blake3
    assert refs[0].media_type == REFS_FILE_MEDIA_TYPE


def test_cached_dailymed_fails_loudly_when_missing(tmp_path: Path) -> None:
    Workdir(tmp_path).create()
    with pytest.raises(FileNotFoundError, match=r"DailyMed.*cached-only"):
        dailymed.fetch(_ctx(tmp_path, source_refresh="cached"))

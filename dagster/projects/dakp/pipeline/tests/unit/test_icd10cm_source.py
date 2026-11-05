"""Tests for the ICD-10-CM Table of Drugs and Chemicals fetcher.

Covers the real download path (network seam monkeypatched to serve a local ZIP), the
release-pinned cache behaviour (no age gate by default: a pinned URL is canonical), the
``force`` and ``icd10cm_url`` overrides, and the alias layout.
"""

from __future__ import annotations

import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from dakp_pipeline.io.artifact_store import ArtifactStore
from dakp_pipeline.io.contracts import TaskContext
from dakp_pipeline.io.manifests import read_manifest
from dakp_pipeline.paths import Workdir
from dakp_pipeline.sources import icd10cm


def _ctx(workdir: Path, *, params: dict[str, object] | None = None) -> TaskContext:
    return TaskContext(workdir=workdir, fixture_root=None, params=params or {})


def _fixture_zip(
    tmp_path: Path, member: str = "icd10cm-table-and-index-2027/icd10cm-drug-2027.xml"
) -> Path:
    zip_path = tmp_path / "icd10cm.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(member, "<ICD10CM.index><version>2027</version></ICD10CM.index>")
    return zip_path


_PARENT_HTML = """
<a href='2026/'>2026</a><a href='2026-update/'>2026-update</a>
<a href='2027/'>2027</a><a href='CM-%20Committee/'>committee</a>
"""
_FY2027_HTML = "<a href='icd10cm-table-and-index-2027.zip'>table</a>"


def _patch_download(monkeypatch, fixture_zip: Path) -> list[str]:
    captured: list[str] = []

    def fake_download(url: str, dest: Path, **kwargs: object) -> Path:
        captured.append(url)
        shutil.copyfile(fixture_zip, dest)
        return dest

    def fake_listing(self, url: str) -> str:
        return _PARENT_HTML if url == icd10cm.ICD10CM_INDEX_URL else _FY2027_HTML

    monkeypatch.setattr(icd10cm, "download_icd10cm_zip", fake_download)
    monkeypatch.setattr(icd10cm.ICD10CMFetcher, "fetch_listing", fake_listing)
    return captured


def test_fetch_ingests_the_release_zip(monkeypatch, tmp_path: Path) -> None:
    captured = _patch_download(monkeypatch, _fixture_zip(tmp_path))
    Workdir(tmp_path / "work").create()
    refs = icd10cm.fetch(_ctx(tmp_path / "work"))

    assert captured == [icd10cm.ICD10CM_DRUG_TABLE_ZIP_URL]
    assert len(refs) == 1
    ref = refs[0]
    assert ref.media_type == "application/zip"
    assert ref.blake3.startswith("b3:")
    assert ref.uri.suffix == ".zip"
    assert ref.manifest is not None
    assert ref.manifest.exists()
    manifest = read_manifest(ref.manifest)
    assert manifest is not None
    assert manifest.source.url == icd10cm.ICD10CM_DRUG_TABLE_ZIP_URL


def test_release_pinned_artifact_is_reused_without_an_age_gate(monkeypatch, tmp_path: Path) -> None:
    """A cached release-pinned ZIP is canonical: the second fetch never re-downloads it."""
    fixture = _fixture_zip(tmp_path)
    captured = _patch_download(monkeypatch, fixture)
    workdir = tmp_path / "work"
    Workdir(workdir).create()

    first = icd10cm.fetch(_ctx(workdir))
    second = icd10cm.fetch(_ctx(workdir))
    assert captured == [icd10cm.ICD10CM_DRUG_TABLE_ZIP_URL]  # one download only
    assert second[0].blake3 == first[0].blake3
    store = ArtifactStore(Workdir(workdir))
    assert store.cached_ref(f"icd10cm/{Path(icd10cm.ICD10CM_DRUG_TABLE_ZIP_URL).name}") is not None


def test_max_age_days_defaults_to_discovery_gate_and_honours_the_param() -> None:
    """Discovery is refreshed weekly; selected immutable ZIPs still have no age gate."""
    assert icd10cm._max_age_days(_ctx(Path("unused"))) == 7.0
    assert icd10cm._max_age_days(_ctx(Path("unused"), params={"icd10cm_max_age_days": 30})) == 30.0
    assert icd10cm._max_age_days(_ctx(Path("unused"), params={"icd10cm_max_age_days": 0})) is None
    assert (
        icd10cm._max_age_days(_ctx(Path("unused"), params={"icd10cm_max_age_days": "nope"})) is None
    )


def test_cache_age_days_reads_the_retrieval_provenance() -> None:
    fresh = datetime.now(UTC) - timedelta(days=2)
    manifest = lambda value: SimpleNamespace(source=SimpleNamespace(retrieved_at=value))
    age = icd10cm._cache_age_days(manifest(fresh.isoformat()))
    assert age is not None
    assert 1.9 < age < 2.1
    assert icd10cm._cache_age_days(None) is None
    assert icd10cm._cache_age_days(manifest(None)) is None
    assert icd10cm._cache_age_days(manifest("not-a-date")) is None


def test_force_param_redownloads(monkeypatch, tmp_path: Path) -> None:
    fixture = _fixture_zip(tmp_path)
    captured = _patch_download(monkeypatch, fixture)
    workdir = tmp_path / "work"
    Workdir(workdir).create()
    icd10cm.fetch(_ctx(workdir))
    icd10cm.fetch(_ctx(workdir, params={"force": True}))
    assert len(captured) == 2


def test_url_override_acquires_under_its_own_alias(monkeypatch, tmp_path: Path) -> None:
    """A different fiscal-year URL is a different release: its own alias, its own download."""
    fixture = _fixture_zip(tmp_path)
    captured = _patch_download(monkeypatch, fixture)
    workdir = tmp_path / "work"
    Workdir(workdir).create()
    override = "https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/2028/icd10cm-table-and-index-2028.zip"

    refs = icd10cm.fetch(_ctx(workdir, params={"icd10cm_url": override}))
    assert captured == [override]
    store = ArtifactStore(Workdir(workdir))
    assert store.cached_ref("icd10cm/icd10cm-table-and-index-2028.zip") is not None
    assert refs[0].manifest is not None
    assert read_manifest(refs[0].manifest) is not None
    assert read_manifest(refs[0].manifest).source.url == override


def test_pinned_release_member_matches_the_prefix_constant() -> None:
    """The offline fallback URL carries the drug table member the extractor looks for."""
    name = Path(icd10cm.ICD10CM_DRUG_TABLE_ZIP_URL).name
    assert name.endswith(".zip")
    assert icd10cm.ICD10CM_RELEASE in name or icd10cm.ICD10CM_RELEASE[-2:] in name
    assert icd10cm.ICD10CM_DRUG_XML_PREFIX == "icd10cm-drug-"


def test_discovery_prefers_newer_fy_over_older_april_update() -> None:
    year, april, directory = icd10cm.discover_release_directory(_PARENT_HTML)
    assert (year, april) == (2027, False)
    assert directory.endswith("/2027/")


def test_discovery_prefers_april_update_within_same_fy() -> None:
    year, april, directory = icd10cm.discover_release_directory(
        "<a href='2026/'>base</a><a href='2026-update/'>April</a>"
    )
    assert (year, april) == (2026, True)
    assert directory.endswith("/2026-update/")


def test_table_zip_selection_tolerates_spaces_and_april_names() -> None:
    base = "<a href='icd10cm-table%20and%20index-2026.zip'>base</a>"
    april = "<a href='icd10cm-table-and-index-April-1-2026.zip'>april</a>"
    assert "table%20and%20index" in icd10cm.select_table_zip(base, directory_url="https://x/")
    assert "April-1-2026" in icd10cm.select_table_zip(april, directory_url="https://x/")
    with pytest.raises(RuntimeError, match="exactly one"):
        icd10cm.select_table_zip(base + april, directory_url="https://x/")


def test_cached_mode_uses_selection_and_zip_without_network(monkeypatch, tmp_path: Path) -> None:
    captured = _patch_download(monkeypatch, _fixture_zip(tmp_path))
    workdir = tmp_path / "work"
    Workdir(workdir).create()
    first = icd10cm.fetch(_ctx(workdir))
    assert captured == [icd10cm.ICD10CM_DRUG_TABLE_ZIP_URL]

    def no_network(*args, **kwargs):
        pytest.fail("cached-only ICD-10-CM acquisition touched the network")

    monkeypatch.setattr(icd10cm.ICD10CMFetcher, "fetch_listing", no_network)
    monkeypatch.setattr(icd10cm, "download_icd10cm_zip", no_network)
    second = icd10cm.fetch(_ctx(workdir, params={"source_refresh": "cached"}))
    assert second[0].blake3 == first[0].blake3
    assert icd10cm.selected_release(workdir).release == "FY2027"

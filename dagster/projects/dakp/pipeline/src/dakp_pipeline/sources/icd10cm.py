"""ICD-10-CM Table of Drugs and Chemicals acquisition and release discovery.

The CDC publishes fiscal-year directories under :data:`ICD10CM_INDEX_URL`, with
occasional April updates. Auto mode discovers the newest FY (and an April update
within that FY), then reuses the selected immutable ZIP by content hash. The
7-day gate applies to the two small discovery listings, not to re-downloading an
unchanged ZIP. ``source_refresh=cached`` uses only the persisted selection and
cached ZIP; ``icd10cm_url`` explicitly pins a release for reproducible builds.

The selected release is persisted under ``icd10cm/release.json`` so downstream
extraction, the subject screen, Tablassert's RIG and release reporting all name
the release that was actually acquired rather than a hardcoded fiscal year.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

from dakp_pipeline.io.artifact_store import ArtifactStore
from dakp_pipeline.io.contracts import ArtifactRef, TaskContext
from dakp_pipeline.io.downloader import download
from dakp_pipeline.io.manifests import SourceBlock
from dakp_pipeline.logging_setup import logger, stats, step
from dakp_pipeline.paths import Workdir
from dakp_pipeline.sources.policy import require_cached, source_policy

ICD10CM_INDEX_URL = "https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/"
#: Offline/test fallback. Production auto mode discovers instead of pinning this URL.
ICD10CM_DRUG_TABLE_ZIP_URL = f"{ICD10CM_INDEX_URL}2027/icd10cm-table-and-index-2027.zip"
ICD10CM_TABLES_URL = "https://www.cdc.gov/nchs/icd/icd10cm.htm"
ICD10CM_RELEASE = "FY2027"
ICD10CM_DRUG_XML_PREFIX = "icd10cm-drug-"
_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_MAX_AGE_DAYS = 7.0
_SELECTION_ALIAS = "icd10cm/release.json"
_EVENT = "acquire_icd10cm"
_HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)


@dataclass(frozen=True)
class ReleaseSelection:
    release: str
    url: str
    directory_url: str
    retrieved_at: str
    artifact_id: str | None = None


def default_release_selection() -> ReleaseSelection:
    return ReleaseSelection(
        release=ICD10CM_RELEASE,
        url=ICD10CM_DRUG_TABLE_ZIP_URL,
        directory_url=f"{ICD10CM_INDEX_URL}2027/",
        retrieved_at="",
        artifact_id=None,
    )


def _release_label(year: int, april_update: bool) -> str:
    return f"FY{year}" + (" April 1 update" if april_update else "")


def discover_release_directory(
    index_html: str, *, base_url: str = ICD10CM_INDEX_URL
) -> tuple[int, bool, str]:
    """Select the newest FY directory; an April update wins within its FY.

    Committee, presentation and other non-release directories are ignored. A
    newer base FY always beats an older FY's April update (FY2027 > FY2026 April).
    """
    candidates: dict[tuple[int, bool], str] = {}
    for href in _HREF_RE.findall(index_html):
        name = unquote(PurePosixPath(urlsplit(href).path).name)
        match = re.fullmatch(r"(20\d{2})(?:[-_]update)?", name, re.IGNORECASE)
        if match is not None:
            year = int(match.group(1))
            april = bool(re.search(r"[-_]update$", name, re.IGNORECASE))
        else:
            legacy = re.fullmatch(r"April-1-(20\d{2})-Update", name, re.IGNORECASE)
            if legacy is None:
                continue
            year, april = int(legacy.group(1)), True
        directory_url = urljoin(base_url, href)
        if not directory_url.endswith("/"):
            directory_url += "/"
        candidates[(year, april)] = directory_url
    if not candidates:
        msg = "ICD-10-CM discovery found no fiscal-year release directories in the CDC index"
        raise RuntimeError(msg)
    year, april = max(candidates)
    return year, april, candidates[(year, april)]


def select_table_zip(listing_html: str, *, directory_url: str) -> str:
    """Select exactly one table-and-index ZIP despite historical filename drift."""
    matches: list[str] = []
    for href in _HREF_RE.findall(listing_html):
        filename = unquote(PurePosixPath(urlsplit(href).path).name)
        normalized = re.sub(r"[-_]+", " ", filename.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if normalized.startswith("icd10cm table and index") and normalized.endswith(".zip"):
            matches.append(urljoin(directory_url, href))
    matches = list(dict.fromkeys(matches))
    if len(matches) != 1:
        msg = (
            f"ICD-10-CM discovery expected exactly one table-and-index ZIP in {directory_url}, "
            f"found {len(matches)}"
        )
        raise RuntimeError(msg)
    return matches[0]


def _selection_from_url(url: str) -> ReleaseSelection:
    path = unquote(urlsplit(url).path)
    directory_url = url.rsplit("/", 1)[0] + "/"
    match = re.search(r"/(20\d{2})([-_]update)?/", path, re.IGNORECASE)
    if match is None:
        legacy = re.search(r"/April-1-(20\d{2})-Update/", path, re.IGNORECASE)
        year = int(legacy.group(1)) if legacy is not None else 0
        april = legacy is not None
    else:
        year, april = int(match.group(1)), bool(match.group(2))
    if year == 0:
        year_match = re.search(r"(20\d{2})", PurePosixPath(path).name)
        year = int(year_match.group(1)) if year_match is not None else 0
    release = _release_label(year, april) if year else "explicitly pinned release"
    return ReleaseSelection(
        release=release,
        url=url,
        directory_url=directory_url,
        retrieved_at=datetime.now(UTC).isoformat(),
    )


def _read_selection(store: ArtifactStore) -> ReleaseSelection | None:
    cached = store.cached_ref(_SELECTION_ALIAS)
    if cached is None or not cached.uri.exists():
        return None
    try:
        payload = json.loads(cached.uri.read_text(encoding="utf-8"))
        return ReleaseSelection(
            release=str(payload["release"]),
            url=str(payload["url"]),
            directory_url=str(payload["directory_url"]),
            retrieved_at=str(payload.get("retrieved_at") or ""),
            artifact_id=payload.get("artifact_id"),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_selection(store: ArtifactStore, selection: ReleaseSelection) -> None:
    dest = store.workdir.raw / "downloads" / "icd10cm-release.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(asdict(selection), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    store.ingest(
        dest,
        media_type="application/json",
        alias=_SELECTION_ALIAS,
        source=SourceBlock(url=ICD10CM_INDEX_URL, retrieved_at=selection.retrieved_at),
    )
    dest.unlink(missing_ok=True)


def selected_release(workdir: Path | str) -> ReleaseSelection:
    """The acquired selection, or the FY2027 offline fallback before acquisition."""
    store = ArtifactStore(Workdir(Path(workdir)))
    return _read_selection(store) or default_release_selection()


def _selection_age_days(selection: ReleaseSelection) -> float | None:
    if not selection.retrieved_at:
        return None
    try:
        retrieved = datetime.fromisoformat(selection.retrieved_at)
        return (datetime.now(UTC) - retrieved).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return None


class ICD10CMFetcher:
    """Discover and acquire the latest ICD-10-CM Table of Drugs and Chemicals ZIP."""

    url: str = ICD10CM_DRUG_TABLE_ZIP_URL

    def fetch_listing(self, url: str) -> str:
        """Fetch one CDC directory listing (the discovery network boundary)."""
        request = urllib.request.Request(url, headers={"User-Agent": "dakp-pipeline/0.1"})
        with urllib.request.urlopen(request, timeout=_DEFAULT_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="replace")

    def discover_latest(self) -> ReleaseSelection:
        year, april, directory_url = discover_release_directory(
            self.fetch_listing(ICD10CM_INDEX_URL)
        )
        url = select_table_zip(self.fetch_listing(directory_url), directory_url=directory_url)
        return ReleaseSelection(
            release=_release_label(year, april),
            url=url,
            directory_url=directory_url,
            retrieved_at=datetime.now(UTC).isoformat(),
        )

    def fetch(self, ctx: TaskContext) -> list[ArtifactRef]:
        with step(logger, _EVENT):
            store = ArtifactStore(Workdir(ctx.workdir))
            policy = source_policy(ctx, _max_age_days(ctx))
            explicit_url = ctx.params.get("icd10cm_url")
            prior = None if policy.force else _read_selection(store)
            if explicit_url:
                selection = _selection_from_url(str(explicit_url))
            elif policy.cached_only:
                if prior is None:
                    msg = (
                        "ICD-10-CM: cached-only acquisition has no icd10cm/release.json; "
                        "run once with DAKP_SOURCE_REFRESH=auto"
                    )
                    raise FileNotFoundError(msg)
                selection = prior
            elif prior is not None and policy.reusable(_selection_age_days(prior)):
                selection = prior
            else:
                selection = self.discover_latest()
            stats(
                logger,
                _EVENT,
                url=selection.url,
                release=selection.release,
                source_refresh=policy.mode,
            )

            filename = unquote(PurePosixPath(urlsplit(selection.url).path).name)
            alias = f"icd10cm/{filename}"
            cached = store.cached_ref(alias)
            manifest = store.read_manifest(cached.blake3) if cached is not None else None
            if (
                not policy.force
                and cached is not None
                and cached.uri.exists()
                and manifest is not None
                and manifest.source.url == selection.url
            ):
                ref = cached
                cache_hit = True
            else:
                if policy.cached_only:
                    ref = require_cached(cached, source="ICD-10-CM", alias=alias)
                    if manifest is None or manifest.source.url != selection.url:
                        msg = (
                            f"ICD-10-CM: cached-only artifact {alias!r} has no manifest "
                            f"matching {selection.url}; repopulate with DAKP_SOURCE_REFRESH=auto"
                        )
                        raise FileNotFoundError(msg)
                    cache_hit = True
                else:
                    with tempfile.NamedTemporaryFile(
                        prefix="icd10cm-", suffix=".zip", delete=False
                    ) as handle:
                        dest = Path(handle.name)
                    try:
                        started = time.monotonic()
                        download_icd10cm_zip(selection.url, dest)
                        stats(
                            logger,
                            _EVENT,
                            bytes=dest.stat().st_size,
                            elapsed_s=round(time.monotonic() - started, 3),
                        )
                        ref, cache_hit = store.ingest(
                            dest,
                            media_type="application/zip",
                            alias=alias,
                            source=SourceBlock(
                                url=selection.url, retrieved_at=datetime.now(UTC).isoformat()
                            ),
                        )
                    finally:
                        dest.unlink(missing_ok=True)
            _write_selection(store, replace(selection, artifact_id=ref.blake3))
            stats(logger, _EVENT, blake3=ref.blake3, cache_hit=cache_hit)
            return [ref]


def _max_age_days(ctx: TaskContext) -> float | None:
    value = ctx.params.get("icd10cm_max_age_days")
    if value is None:
        return _DEFAULT_MAX_AGE_DAYS
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    return None


def _cache_age_days(manifest: object | None) -> float | None:
    """Age of a cached manifest (kept for callers/tests using the original helper)."""
    retrieved_at = getattr(getattr(manifest, "source", None), "retrieved_at", None)
    if not retrieved_at:
        return None
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(retrieved_at)).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return None


def download_icd10cm_zip(
    url: str, dest: Path, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> Path:
    """Download ``url`` to ``dest`` (aria2c-accelerated, stdlib fallback)."""
    return download(url, dest, timeout=timeout)


fetch = ICD10CMFetcher().fetch

__all__ = [
    "ICD10CM_DRUG_TABLE_ZIP_URL",
    "ICD10CM_DRUG_XML_PREFIX",
    "ICD10CM_INDEX_URL",
    "ICD10CM_RELEASE",
    "ICD10CM_TABLES_URL",
    "ICD10CMFetcher",
    "ReleaseSelection",
    "default_release_selection",
    "discover_release_directory",
    "download_icd10cm_zip",
    "fetch",
    "select_table_zip",
    "selected_release",
]

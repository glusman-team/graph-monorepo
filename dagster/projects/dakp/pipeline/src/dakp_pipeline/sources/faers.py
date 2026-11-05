"""FAERS quarterly ASCII acquisition.

Discovers quarterly ASCII zips from the FDA exports index, downloads each into the BLAKE3
content-addressed store, and honors ``quarter_limit`` (process only the N most-recent
quarters) for bounded runs. Acquisition is cache-first: a quarter whose zip is already
content-addressed under its ``faers/faers_ascii_<Q>.zip`` alias is reused WITHOUT touching
the network (FAERS quarterly zips are immutable published snapshots, and fis.fda.gov sends
no ETag/Last-Modified, so alias reuse — not conditional GET — is the skip mechanism);
``force`` (run param) re-downloads everything. Unlike the legacy ``getLatest.pl``, this
fetcher never destructively renames or stashes files. Offline tests monkeypatch the
module-level :data:`fetch` (or the :meth:`FAERSFetcher.fetch_index` /
:meth:`FAERSFetcher.download_quarter` network boundaries).

Monkeypatchable boundaries:

* :func:`discover_quarters` — pure parser of the FDA index HTML -> most-recent-first quarters.
* :meth:`FAERSFetcher.fetch_index` / :meth:`FAERSFetcher.download_quarter` — the network
  calls; tests patch these (or replace the module-level :data:`fetch`) for offline coverage.
"""

from __future__ import annotations

import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dakp_pipeline.io.artifact_store import ArtifactStore
from dakp_pipeline.io.contracts import ArtifactRef, TaskContext
from dakp_pipeline.io.downloader import download
from dakp_pipeline.io.manifests import SourceBlock
from dakp_pipeline.logging_setup import logger, stats, step
from dakp_pipeline.paths import Workdir
from dakp_pipeline.sources.policy import require_cached, source_policy

# FDA quarterly-data-extract listing page (anchors discovery of the ASCII zips). The old
# ``https://fis.fda.gov/content/Exports`` index now 404s; the listing moved here. The quarterly
# ASCII zips themselves still live under ``https://fis.fda.gov/content/Exports/`` (the download
# base), named ``faers_ascii_<YYYY>q<N>.zip`` — except 2004Q1 through 2012Q3, which predate the
# AERS->FAERS rename and are published as ``aers_ascii_<YYYY>q<N>.zip`` (the legacy
# ``getLatest.pl`` regex ``f?aers_ascii_....[Qq]..zip`` matched both; the optional ``f`` here
# keeps those 35 quarters discoverable).
FDA_FAERS_INDEX_URL = "https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html"
#: Where the quarterly ASCII zips are downloaded from (the listing page only anchors discovery).
FDA_FAERS_DOWNLOAD_BASE = "https://fis.fda.gov/content/Exports"
_FAERS_ZIP_RE = re.compile(r"f?aers_ascii_(\d{4})q(\d)\.zip", re.IGNORECASE)
_DEFAULT_TIMEOUT = 120.0
_DEFAULT_MAX_AGE_DAYS = 7.0
_INDEX_ALIAS = "faers/discovery-index.html"
#: Max concurrent quarter downloads/cache checks within one FAERS acquisition.
_DOWNLOAD_CONCURRENCY = 4
#: Narration prefix for every log line this fetcher emits (one stat per line).
_EVENT = "acquire_faers"


@dataclass(frozen=True)
class QuarterSource:
    """A discovered FAERS quarter: a canonical quarter label and its zip URL."""

    quarter: str  # canonical label, e.g. "24Q3"
    url: str


def discover_quarters(
    index_html: str, *, base_url: str = FDA_FAERS_DOWNLOAD_BASE
) -> list[QuarterSource]:
    """Parse FDA index HTML into most-recent-first ``(quarter, url)`` entries.

    Pure function (no network). Quarter labels are canonicalized to ``YYQ<N>`` (e.g.
    ``2024q3`` -> ``24Q3``) so they line up with FAERS ASCII filenames and the legacy
    ``\\d\\dQ\\d`` quarter convention used by ``listCases.pl`` / ``drug2indi.pl``.
    """
    found: dict[str, str] = {}
    for match in _FAERS_ZIP_RE.finditer(index_html):
        filename = match.group(0)
        # The zip regex already captures (year, quarter); a match always has both groups.
        label = f"{match.group(1)[-2:]}Q{match.group(2)}"
        url = (
            filename
            if filename.lower().startswith("http")
            else f"{base_url.rstrip('/')}/{filename}"
        )
        found[label] = url
    # Lexicographic reverse sort == most-recent first for zero-padded 2-digit years
    # (covers FAERS quarters from 2004 through 2099).
    return [QuarterSource(label, found[label]) for label in sorted(found, reverse=True)]


def _apply_quarter_limit[T](quarters: list[T], ctx: TaskContext) -> list[T]:
    """Slice to the N most-recent quarters when ``quarter_limit`` is set (<=0 / None = all)."""
    limit = ctx.params.get("quarter_limit")
    if not isinstance(limit, int) or limit <= 0:
        return quarters
    return quarters[:limit]


class FAERSFetcher:
    """Acquire FAERS quarterly ASCII artifacts over the network."""

    def fetch(self, ctx: TaskContext) -> list[ArtifactRef]:
        with step(logger, _EVENT):
            policy = source_policy(ctx, _max_age_days(ctx))
            if policy.cached_only:
                discovered = self.cached_quarters(ctx)
            else:
                index_html = self._discovery_index(ctx, policy)
                discovered = discover_quarters(index_html)
            stats(logger, _EVENT, quarters_discovered=len(discovered), source_refresh=policy.mode)
            for source in discovered:
                stats(logger, _EVENT, level="DEBUG", quarter=source.quarter, url=source.url)
            quarters = _apply_quarter_limit(discovered, ctx)
            stats(logger, _EVENT, quarters_to_acquire=len(quarters))
            if not quarters:
                if policy.cached_only:
                    msg = (
                        "FAERS: cached-only acquisition found no faers/faers_ascii_<Q>.zip "
                        "aliases; run once with DAKP_SOURCE_REFRESH=auto"
                    )
                    raise FileNotFoundError(msg)
                logger.warning("{}: no FAERS quarters discovered from index", _EVENT)
                return []
            if policy.cached_only:
                store = ArtifactStore(Workdir(ctx.workdir))
                refs = [
                    require_cached(
                        store.cached_ref(f"faers/faers_ascii_{source.quarter}.zip"),
                        source="FAERS",
                        alias=f"faers/faers_ascii_{source.quarter}.zip",
                    )
                    for source in quarters
                ]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(_DOWNLOAD_CONCURRENCY, len(quarters))
                ) as pool:
                    futures = [
                        pool.submit(self.download_quarter, ctx, source) for source in quarters
                    ]
                    # Collect in discovery order even though downloads complete out of order.
                    refs = [future.result() for future in futures]
            stats(logger, _EVENT, artifacts=len(refs))
            return refs

    def cached_quarters(self, ctx: TaskContext) -> list[QuarterSource]:
        """Enumerate cached quarter aliases without any network access."""
        store = ArtifactStore(Workdir(ctx.workdir))
        alias_root = Workdir(ctx.workdir).aliases / "faers"
        if not alias_root.exists():
            return []
        found: dict[str, str] = {}
        for path in alias_root.glob("faers_ascii_*.zip"):
            match = re.fullmatch(r"faers_ascii_(\d{2}Q[1-4])\.zip", path.name)
            if match is None:
                continue
            quarter = match.group(1)
            cached = store.cached_ref(f"faers/{path.name}")
            if cached is None or not cached.uri.exists():
                continue
            manifest = store.read_manifest(cached.blake3)
            url = (
                manifest.source.url
                if manifest is not None and manifest.source.url
                else f"{FDA_FAERS_DOWNLOAD_BASE}/faers_ascii_20{quarter[:2]}q{quarter[-1]}.zip"
            )
            found[quarter] = url
        return [QuarterSource(label, found[label]) for label in sorted(found, reverse=True)]

    def _discovery_index(self, ctx: TaskContext, policy) -> str:
        """Reuse a fresh discovery index, otherwise fetch and content-address it."""
        store = ArtifactStore(Workdir(ctx.workdir))
        cached = store.cached_ref(_INDEX_ALIAS)
        manifest = store.read_manifest(cached.blake3) if cached is not None else None
        age = _cache_age_days(manifest)
        if cached is not None and cached.uri.exists() and policy.reusable(age):
            stats(logger, f"{_EVENT} index", cache_hit=True, age_days=round(age, 2))
            return cached.uri.read_text(encoding="utf-8", errors="replace")
        index_html = self.fetch_index(ctx)
        dest = Workdir(ctx.workdir).raw / "downloads" / "faers-discovery-index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(index_html, encoding="utf-8")
        store.ingest(
            dest,
            media_type="text/html",
            alias=_INDEX_ALIAS,
            source=SourceBlock(url=FDA_FAERS_INDEX_URL, retrieved_at=datetime.now(UTC).isoformat()),
        )
        dest.unlink(missing_ok=True)
        return index_html

    def fetch_index(self, ctx: TaskContext) -> str:
        """Fetch the FDA exports index HTML. Network boundary; monkeypatchable."""
        stats(logger, f"{_EVENT} index", url=FDA_FAERS_INDEX_URL)
        started = time.monotonic()
        index_html = _http_get_text(FDA_FAERS_INDEX_URL, timeout=_DEFAULT_TIMEOUT)
        stats(
            logger,
            f"{_EVENT} index",
            bytes=len(index_html),
            elapsed_s=round(time.monotonic() - started, 3),
        )
        return index_html

    def download_quarter(self, ctx: TaskContext, source: QuarterSource) -> ArtifactRef:
        """Acquire one quarter zip into the content-addressed store (cache-first).

        A quarter already present under its alias is reused WITHOUT a network call (FAERS
        quarterly zips are immutable published snapshots; fis.fda.gov sends no ETag or
        Last-Modified, so conditional GET is impossible and alias reuse is the skip).
        ``force`` re-downloads unconditionally. Downloads stream in 1 MiB chunks (a quarter
        zip never sits whole in memory) and the staged copy is removed once content-addressed.
        """
        wd = Workdir(ctx.workdir)
        store = ArtifactStore(wd)
        alias = f"faers/faers_ascii_{source.quarter}.zip"
        quarter_event = f"{_EVENT} quarter"
        policy = source_policy(ctx, None)
        if not policy.force:
            cached = store.cached_ref(alias)
            if cached is not None and cached.uri.exists():
                stats(
                    logger,
                    quarter_event,
                    quarter=source.quarter,
                    cache_hit=True,
                    blake3=cached.blake3,
                )
                return cached
        if policy.cached_only:
            return require_cached(store.cached_ref(alias), source="FAERS", alias=alias)
        stats(logger, quarter_event, quarter=source.quarter)
        stats(logger, quarter_event, level="DEBUG", url=source.url)
        dest = wd.raw / "downloads" / f"faers_ascii_{source.quarter}.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        _http_download(source.url, dest, timeout=_DEFAULT_TIMEOUT)
        stats(
            logger,
            quarter_event,
            bytes=dest.stat().st_size,
            elapsed_s=round(time.monotonic() - started, 3),
        )
        ref, cache_hit = store.ingest(
            dest,
            alias=alias,
            source=SourceBlock(url=source.url, retrieved_at=datetime.now(UTC).isoformat()),
        )
        dest.unlink(missing_ok=True)  # staged copy no longer needed once content-addressed
        stats(logger, quarter_event, blake3=ref.blake3, cache_hit=cache_hit)
        return ref


def _max_age_days(ctx: TaskContext) -> float | None:
    value = ctx.params.get("faers_max_age_days")
    if value is None:
        return _DEFAULT_MAX_AGE_DAYS
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    return None


def _cache_age_days(manifest: object | None) -> float | None:
    retrieved_at = getattr(getattr(manifest, "source", None), "retrieved_at", None)
    if not retrieved_at:
        return None
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(retrieved_at)).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return None


# --- HTTP helpers (index HTML via stdlib urllib; bulk downloads via io.downloader) -----


def _http_get_text(url: str, *, timeout: float) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _http_download(url: str, dest: Path, *, timeout: float) -> Path:
    """Download ``url`` to ``dest`` (aria2c-accelerated, stdlib fallback). Network boundary; monkeypatchable.

    FAERS quarterly zips are immutable multi-hundred-MB snapshots — exactly what aria2c's
    multi-connection downloads speed up. Tests replace this module-level seam to serve a fixture
    zip without network; the real HTTP path is covered by the offline prod-smoke test.
    """
    return download(url, dest, timeout=timeout)


fetch = FAERSFetcher().fetch

__all__ = [
    "FDA_FAERS_DOWNLOAD_BASE",
    "FDA_FAERS_INDEX_URL",
    "FAERSFetcher",
    "QuarterSource",
    "discover_quarters",
    "fetch",
]

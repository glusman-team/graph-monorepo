"""Shared source-cache policy for scheduled releases and on-demand builds.

``auto`` applies each source's freshness window. ``cached`` is a strict no-network
mode for warm manual builds: reuse cached artifacts regardless of age and fail
loudly when one is absent. ``force`` bypasses every acquisition cache. The global
``force`` run param (``DAKP_FORCE``) retains its existing meaning and also forces
source acquisition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dakp_pipeline.io.contracts import ArtifactRef, TaskContext

SourceRefresh = Literal["auto", "cached", "force"]
_VALID_MODES = frozenset({"auto", "cached", "force"})


@dataclass(frozen=True)
class SourcePolicy:
    mode: SourceRefresh
    max_age_days: float | None

    @property
    def cached_only(self) -> bool:
        return self.mode == "cached"

    @property
    def force(self) -> bool:
        return self.mode == "force"

    def reusable(self, age_days: float | None) -> bool:
        if self.cached_only:
            return True
        if self.force:
            return False
        # max_age_days None (a non-positive env value) means "no age-based reuse": every
        # auto run re-checks upstream, matching the fetchers' historical semantics.
        return (
            self.max_age_days is not None and age_days is not None and age_days < self.max_age_days
        )


def source_policy(ctx: TaskContext, max_age_days: float | None) -> SourcePolicy:
    raw = ctx.params.get("source_refresh", "auto")
    mode = str(raw).strip().lower()
    if mode not in _VALID_MODES:
        msg = f"source_refresh must be one of auto/cached/force, got {raw!r}"
        raise ValueError(msg)
    if bool(ctx.params.get("force", False)):
        mode = "force"
    return SourcePolicy(mode=mode, max_age_days=max_age_days)  # type: ignore[arg-type]


def cached_source_ref(
    store,
    *,
    alias: str,
    url: str,
    source: str,
    policy: SourcePolicy,
) -> ArtifactRef | None:
    """Resolve one fixed-URL source artifact under ``policy``.

    Cached-only mode validates provenance but deliberately ignores age. Auto mode
    requires a fresh, matching manifest. Force mode always returns ``None``.
    """
    cached = store.cached_ref(alias)
    if policy.cached_only:
        cached = require_cached(cached, source=source, alias=alias)
        manifest = store.read_manifest(cached.blake3)
        if manifest is None or manifest.source.url != url:
            msg = (
                f"{source}: cached-only artifact {alias!r} has no manifest matching {url}; "
                "repopulate it with DAKP_SOURCE_REFRESH=auto"
            )
            raise FileNotFoundError(msg)
        return cached
    if policy.force or cached is None or not cached.uri.exists():
        return None
    manifest = store.read_manifest(cached.blake3)
    if manifest is None or manifest.source.url != url:
        return None
    retrieved_at = manifest.source.retrieved_at
    if not retrieved_at:
        return None
    from datetime import UTC, datetime

    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(retrieved_at)).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return None
    return cached if policy.reusable(age) else None


def require_cached(
    cached: ArtifactRef | None,
    *,
    source: str,
    alias: str,
) -> ArtifactRef:
    """Return a usable cached ref or explain exactly what a cached-only run lacks."""
    if cached is None or not cached.uri.exists():
        msg = (
            f"{source}: cached-only acquisition has no usable artifact for alias {alias!r}; "
            "run once with DAKP_SOURCE_REFRESH=auto to populate the source cache"
        )
        raise FileNotFoundError(msg)
    return cached


__all__ = [
    "SourcePolicy",
    "SourceRefresh",
    "cached_source_ref",
    "require_cached",
    "source_policy",
]

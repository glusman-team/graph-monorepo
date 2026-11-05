"""Hugging Face tag-based DAKP patch versioning."""

from __future__ import annotations

import re
from typing import Any

_VERSION_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_version_tag(tag: str) -> tuple[int, int, int] | None:
    match = _VERSION_TAG.fullmatch(tag)
    return tuple(int(part) for part in match.groups()) if match else None


def next_patch_version(tags: list[str]) -> str:
    versions = [parsed for tag in tags if (parsed := parse_version_tag(tag)) is not None]
    if not versions:
        return "0.1.0"
    major, minor, patch = max(versions)
    return f"{major}.{minor}.{patch + 1}"


def next_hf_patch_version(api: Any, repo_id: str) -> str:
    refs = api.list_repo_refs(repo_id=repo_id, repo_type="dataset")
    return next_patch_version([tag.name for tag in refs.tags])


__all__ = ["next_hf_patch_version", "next_patch_version", "parse_version_tag"]

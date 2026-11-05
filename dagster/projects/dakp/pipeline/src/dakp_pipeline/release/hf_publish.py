"""Atomic, tag-preserving Hugging Face publication for one staged DAKP release."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dakp_pipeline.release.card import file_stat
from dakp_pipeline.release.versions import next_patch_version

_VERSION_DIR = re.compile(r"^\d+\.\d+\.\d+$")


class PublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishResult:
    version: str
    dry_run: bool
    actions: tuple[str, ...]
    superseded: tuple[str, ...]
    verified: bool


def _operations() -> tuple[type, type]:
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete

    return CommitOperationAdd, CommitOperationDelete


def publish_release(
    api: Any,
    *,
    repo_id: str,
    staging_root: Path,
    version: str,
    change_message: str,
    nodes: int,
    edges: int,
    dry_run: bool = False,
    operation_types: tuple[type, type] | None = None,
) -> PublishResult:
    """Publish in runbook order; every mutation is skipped in dry-run mode."""
    kw = {"repo_id": repo_id, "repo_type": "dataset"}
    tag = f"v{version}"
    version_dir = staging_root / version
    card_path = staging_root / "README.md"
    if not version_dir.is_dir() or not card_path.is_file():
        raise PublishError(
            f"incomplete release staging at {staging_root}: {version_dir} + README.md required"
        )
    files = [file_stat(path) for path in sorted(version_dir.iterdir()) if path.is_file()]
    expected_names = {
        f"DRUG_APPROVALS_KP_{version}.nodes.ndjson",
        f"DRUG_APPROVALS_KP_{version}.edges.ndjson",
        f"DRUG_APPROVALS_KP_{version}.RIG.yaml",
    }
    if {file.name for file in files} != expected_names:
        raise PublishError(f"staged files do not match the DAKP release contract: {expected_names}")

    refs = api.list_repo_refs(**kw)
    tags = {item.name for item in refs.tags}
    main_files = api.list_repo_files(**kw)
    if tag in tags:
        raise PublishError(f"tag already exists: {tag}")
    if any(path.startswith(f"{version}/") for path in main_files):
        raise PublishError(f"version directory already exists on main: {version}/")
    latest = next_patch_version(sorted(tags))
    if latest != version:
        raise PublishError(
            f"HF advanced while this release built: next patch is {latest}, staged version is {version}; rerun the release check"
        )

    old_versions = sorted(
        {
            path.split("/", 1)[0]
            for path in main_files
            if "/" in path and _VERSION_DIR.fullmatch(path.split("/", 1)[0])
        }
        - {version}
    )
    missing_tags = [old for old in old_versions if f"v{old}" not in tags]
    if missing_tags:
        raise PublishError(f"superseded directories have no pinning tag: {missing_tags}")
    for old in old_versions:
        if not api.list_repo_files(revision=f"v{old}", **kw):
            raise PublishError(f"pinning tag v{old} has no files; refusing to prune {old}/")

    summary = f"DAKP {version} KGX ({nodes} nodes / {edges} edges)"
    actions = [
        f"upload {version_dir} -> {repo_id}/{version} ({summary}; {change_message})",
        f"commit {card_path} as README.md",
        *(f"prune {old}/ (pinned by v{old})" for old in old_versions),
        "verify main file MD5 checksums",
        f"create tag {tag} and verify its tree",
    ]
    if dry_run:
        return PublishResult(version, True, tuple(actions), tuple(old_versions), False)

    add_type, delete_type = operation_types or _operations()
    api.upload_folder(
        folder_path=str(version_dir),
        path_in_repo=version,
        commit_message=f"Add {summary}. {change_message}",
        **kw,
    )
    api.create_commit(
        operations=[add_type(path_in_repo="README.md", path_or_fileobj=str(card_path))],
        commit_message=f"Point the dataset card at release {version}. {change_message}",
        **kw,
    )
    if old_versions:
        api.create_commit(
            operations=[
                delete_type(path_in_repo=f"{old}/", is_folder=True) for old in old_versions
            ],
            commit_message=(
                f"Prune superseded release(s) {', '.join(old_versions)} from main; "
                "all remain pinned by dataset tags"
            ),
            **kw,
        )

    for file in files:
        downloaded = api.hf_hub_download(
            repo_id=repo_id,
            filename=f"{version}/{file.name}",
            repo_type="dataset",
            revision="main",
            force_download=True,
        )
        actual = file_stat(Path(downloaded)).md5
        if actual != file.md5:
            raise PublishError(
                f"uploaded checksum mismatch for {version}/{file.name}: staged {file.md5}, downloaded {actual}"
            )
    api.create_tag(tag=tag, tag_message=f"{summary}. {change_message}", **kw)
    tagged_files = set(api.list_repo_files(revision=tag, **kw))
    missing = {f"{version}/{file.name}" for file in files} - tagged_files
    if missing:
        raise PublishError(f"new tag {tag} is missing release files: {sorted(missing)}")
    return PublishResult(version, False, tuple(actions), tuple(old_versions), True)


__all__ = ["PublishError", "PublishResult", "publish_release"]

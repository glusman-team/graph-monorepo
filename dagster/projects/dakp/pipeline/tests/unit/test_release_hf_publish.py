"""HF publication ordering, race checks, dry run, and checksum verification."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dakp_pipeline.release.hf_publish import PublishError, publish_release


class _Add:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Delete:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _HF:
    def __init__(self, root: Path):
        self.root = root
        self.tags = {"v1.27.0"}
        self.main = {
            "README.md",
            "1.27.0/DRUG_APPROVALS_KP_1.27.0.nodes.ndjson",
            "1.27.0/DRUG_APPROVALS_KP_1.27.0.edges.ndjson",
            "1.27.0/DRUG_APPROVALS_KP_1.27.0.RIG.yaml",
        }
        self.calls = []
        self.bad_download = False

    def list_repo_refs(self, **kwargs):
        return SimpleNamespace(tags=[SimpleNamespace(name=tag) for tag in sorted(self.tags)])

    def list_repo_files(self, revision="main", **kwargs):
        if revision == "main":
            return sorted(self.main)
        if revision == "v1.27.0":
            return sorted(path for path in self.main if path.startswith("1.27.0/")) or [
                "1.27.0/DRUG_APPROVALS_KP_1.27.0.nodes.ndjson"
            ]
        if revision == "v1.27.1":
            return sorted(path for path in self.main if path.startswith("1.27.1/"))
        return []

    def upload_folder(self, **kwargs):
        self.calls.append(("upload", kwargs))
        for path in (self.root / "staging" / "1.27.1").iterdir():
            self.main.add(f"1.27.1/{path.name}")
        return SimpleNamespace(oid="upload-commit")

    def create_commit(self, operations, **kwargs):
        if isinstance(operations[0], _Add):
            self.calls.append(("card", kwargs))
        else:
            self.calls.append(("prune", kwargs))
            for operation in operations:
                old = operation.kwargs["path_in_repo"].strip("/")
                self.main = {path for path in self.main if not path.startswith(f"{old}/")}
        return SimpleNamespace(oid="commit")

    def hf_hub_download(self, filename, **kwargs):
        self.calls.append(("download", filename))
        if self.bad_download:
            bad = self.root / "bad.bin"
            bad.write_bytes(b"bad")
            return str(bad)
        return str(self.root / "staging" / filename)

    def create_tag(self, tag, **kwargs):
        self.calls.append(("tag", tag))
        self.tags.add(tag)


def _staging(root: Path) -> Path:
    staging = root / "staging"
    version_dir = staging / "1.27.1"
    version_dir.mkdir(parents=True)
    for kind in ("nodes.ndjson", "edges.ndjson", "RIG.yaml"):
        (version_dir / f"DRUG_APPROVALS_KP_1.27.1.{kind}").write_bytes(kind.encode())
    (staging / "README.md").write_text("new card", encoding="utf-8")
    return staging


def _publish(api: _HF, staging: Path, dry_run: bool = False):
    return publish_release(
        api,
        repo_id="SkyeAv/drug-approvals-kp",
        staging_root=staging,
        version="1.27.1",
        change_message="New data for 1 source on 2026-11-04",
        nodes=2,
        edges=1,
        dry_run=dry_run,
        operation_types=(_Add, _Delete),
    )


def test_publish_runbook_order_and_checksum_verification(tmp_path: Path) -> None:
    api = _HF(tmp_path)
    result = _publish(api, _staging(tmp_path))
    assert result.verified
    assert not result.dry_run
    assert [name for name, _ in api.calls if name != "download"] == [
        "upload",
        "card",
        "prune",
        "tag",
    ]
    assert sum(name == "download" for name, _ in api.calls) == 3
    assert not any(path.startswith("1.27.0/") for path in api.main)
    assert "v1.27.1" in api.tags


def test_dry_run_makes_no_mutations_or_downloads(tmp_path: Path) -> None:
    api = _HF(tmp_path)
    result = _publish(api, _staging(tmp_path), dry_run=True)
    assert result.dry_run
    assert not result.verified
    assert api.calls == []
    assert "v1.27.1" not in api.tags
    assert len(result.actions) == 5


def test_checksum_mismatch_prevents_tag(tmp_path: Path) -> None:
    api = _HF(tmp_path)
    api.bad_download = True
    with pytest.raises(PublishError, match="checksum mismatch"):
        _publish(api, _staging(tmp_path))
    assert "v1.27.1" not in api.tags


def test_remote_version_race_fails_before_mutation(tmp_path: Path) -> None:
    api = _HF(tmp_path)
    api.tags.add("v1.27.2")
    with pytest.raises(PublishError, match="advanced"):
        _publish(api, _staging(tmp_path))
    assert api.calls == []


def test_missing_old_tag_prevents_prune_and_upload(tmp_path: Path) -> None:
    api = _HF(tmp_path)
    api.main.add("1.26.0/DRUG_APPROVALS_KP_1.26.0.nodes.ndjson")
    with pytest.raises(PublishError, match="no pinning tag"):
        _publish(api, _staging(tmp_path))
    assert api.calls == []

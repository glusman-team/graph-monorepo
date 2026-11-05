"""HF release tags determine the next patch version."""

from types import SimpleNamespace

from dakp_pipeline.release.versions import (
    next_hf_patch_version,
    next_patch_version,
    parse_version_tag,
)


def test_next_patch_ignores_non_release_tags_and_prerelease_names() -> None:
    assert next_patch_version(["v1.25.1", "latest", "v1.27.0", "v1.27.0-rc1", "v2.0"]) == "1.27.1"
    assert next_patch_version(["v1.9.9", "v1.10.0"]) == "1.10.1"
    assert next_patch_version([]) == "0.1.0"
    assert parse_version_tag("1.27.0") is None


def test_hf_version_resolver_reads_dataset_tags() -> None:
    class API:
        def list_repo_refs(self, **kwargs):
            assert kwargs == {"repo_id": "SkyeAv/drug-approvals-kp", "repo_type": "dataset"}
            return SimpleNamespace(tags=[SimpleNamespace(name="v1.27.0")])

    assert next_hf_patch_version(API(), "SkyeAv/drug-approvals-kp") == "1.27.1"

"""Protect model semantics that dataclass replacement could silently change."""

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from dakp_pipeline.assertions.observed_uses import ApprovedTreatsIndex
from dakp_pipeline.extract.spl_xml import DocumentRecord
from dakp_pipeline.io.contracts import ArtifactRef, TaskContext
from dakp_pipeline.ner.lexical import Mention
from dakp_pipeline.paths import Workdir
from dakp_pipeline.release.gpu import GPUDevice, GPUStatus


def test_frozen_value_records_validate_and_hash(tmp_path: Path) -> None:
    """Immutable artifact and mention keys must still deduplicate by value."""
    ref = ArtifactRef(uri=tmp_path / "data", blake3="b3:aa", media_type="text/plain")
    assert isinstance(ref, BaseModel)
    assert len({ref, ArtifactRef(**ref.model_dump())}) == 1
    mention = Mention(text="pain", start=0, end=4, type="disease", score=1.0)
    assert len({mention, Mention(**mention.model_dump())}) == 1
    with pytest.raises(ValidationError, match="frozen_instance"):
        ref.rows = 1
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ArtifactRef(**ref.model_dump(), typo=True)
    with pytest.raises(ValidationError):
        Mention(text="pain", start="invalid", end=4, type="disease", score=1.0)


def test_mutable_defaults_remain_independent() -> None:
    """One parsed document's warnings must never contaminate the next document."""
    first = DocumentRecord(set_id="first")
    second = DocumentRecord(set_id="second")
    first.warnings.append("missing section")
    first.label_type = "human"
    assert second.warnings == []
    assert second.label_type == ""


def test_path_normalization_and_context_copy(tmp_path: Path) -> None:
    """Normalize path strings without resolving symlinks or losing context values."""
    root = tmp_path / "link" / ".." / "work"
    workdir = Workdir(root=str(root))
    assert workdir.root == root
    assert not root.exists()
    ctx = TaskContext(workdir=tmp_path, fixture_root=None, params={"force": False})
    changed = ctx.model_copy(update={"params": {"force": True}})
    assert changed.params == {"force": True}
    assert ctx.params == {"force": False}
    assert changed.workdir == ctx.workdir


def test_approved_index_remains_read_only() -> None:
    """Pydantic mapping validation must not expose the shared lookup for mutation."""
    source = {"NDA000001": frozenset({"pain"})}
    index = ApprovedTreatsIndex(objects_by_approval=source)
    source["NDA000002"] = frozenset({"fever"})
    assert "NDA000002" not in index.objects_by_approval
    with pytest.raises(TypeError):
        index.objects_by_approval["NDA000001"] = frozenset()
    with pytest.raises(TypeError):
        ApprovedTreatsIndex().objects_by_approval["NDA000001"] = frozenset()


def test_nested_release_serialization_preserves_wire_shape() -> None:
    """GPU reports must keep their existing list-of-dicts contract after migration."""
    status = GPUStatus(devices=(GPUDevice(index=0, name="gpu", used_mib=0, total_mib=16),))
    assert status.to_dict() == {
        "devices": [{"index": 0, "name": "gpu", "used_mib": 0, "total_mib": 16}],
        "processes": [],
    }

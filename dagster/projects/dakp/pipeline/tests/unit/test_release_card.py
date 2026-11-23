"""Release verification and dataset-card generation from actual KGX/RIG outputs."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from dakp_pipeline.release import card as card_module
from dakp_pipeline.release.card import (
    build_dataset_card,
    compute_kg_stats,
    stage_release_files,
)
from dakp_pipeline.release.delta import SourceChange, SourceDelta
from dakp_pipeline.release.state import SourceFingerprint
from dakp_pipeline.release.verify import ReleaseVerificationError, verify_release


def _write_outputs(root: Path, *, screened: bool = False):
    nodes = [
        {"id": "MONDO:0005148", "name": "diabetes", "category": ["biolink:Disease"]},
        {"id": "CHEBI:6801", "name": "metformin", "category": ["biolink:ChemicalEntity"]},
        {"id": "NCBITaxon:9606", "name": "human", "category": ["biolink:OrganismTaxon"]},
    ]
    if screened:
        nodes.append({"id": "CHEBI:15377", "name": "water", "category": ["biolink:ChemicalEntity"]})
    edges = [
        {
            "subject": "CHEBI:6801",
            "predicate": "biolink:treats",
            "object": "MONDO:0005148",
            "publications": ["dailymed:study"],
            "species_context_qualifier": "NCBITaxon:9606",
        },
        {
            "subject": "CHEBI:6801",
            "predicate": "biolink:contraindicated_in",
            "object": "MONDO:0005148",
            "species_context_qualifier": "NCBITaxon:9606",
        },
    ]
    nodes_path = root / "kg.nodes.ndjson"
    edges_path = root / "kg.edges.ndjson"
    nodes_path.write_text("".join(json.dumps(row) + "\n" for row in nodes), encoding="utf-8")
    edges_path.write_text("".join(json.dumps(row) + "\n" for row in edges), encoding="utf-8")
    rig_path = root / "release.RIG.yaml"
    rig_path.write_text(
        "version: 1.27.1\nbiolink_version: 4.4.6\nsources:\n  - infores:icd10cm\n",
        encoding="utf-8",
    )
    return nodes_path, edges_path, rig_path


def _verification(tmp_path: Path):
    nodes, edges, rig = _write_outputs(tmp_path)
    return (
        nodes,
        edges,
        rig,
        verify_release(
            version="1.27.1",
            nodes_path=nodes,
            edges_path=edges,
            rig_path=rig,
            runner=lambda command, cwd: subprocess.CompletedProcess(
                command, 0, "KGX output is Biolink-compliant\n", ""
            ),
        ),
    )


def test_release_verify_runs_validator_and_screen(tmp_path: Path) -> None:
    *_, verification = _verification(tmp_path)
    assert verification.ok
    assert verification.screened_hits == ()
    assert "Biolink-compliant" in verification.validate_kgx_stdout


def test_release_verify_rejects_screened_output_and_failed_validator(tmp_path: Path) -> None:
    nodes, edges, rig = _write_outputs(tmp_path, screened=True)
    with pytest.raises(ReleaseVerificationError, match="screened"):
        verify_release(
            version="1.27.1",
            nodes_path=nodes,
            edges_path=edges,
            rig_path=rig,
            runner=lambda command, cwd: subprocess.CompletedProcess(command, 0, "ok", ""),
        )
    clean = _write_outputs(tmp_path)
    with pytest.raises(ReleaseVerificationError, match="exited 2"):
        verify_release(
            version="1.27.1",
            nodes_path=clean[0],
            edges_path=clean[1],
            rig_path=clean[2],
            runner=lambda command, cwd: subprocess.CompletedProcess(command, 2, "bad kgx", ""),
        )


def _card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    previous_card: str | None = None,
    previous_version: str | None = "1.27.0",
):
    nodes, edges, rig, verification = _verification(tmp_path)
    files = stage_release_files(
        staging_root=tmp_path / "staging",
        version="1.27.1",
        nodes_path=nodes,
        edges_path=edges,
        rig_path=rig,
    )
    fingerprint = SourceFingerprint(
        fingerprint="b3:new",
        artifact_count=2,
        upstream_dates=("2026-11-04",),
        retrieved_dates=("2026-11-06",),
    )
    delta = SourceDelta(
        current={"DailyMed": fingerprint},
        changes=(SourceChange(source="DailyMed", current=fingerprint, previous=None),),
        previous_version=previous_version,
        created_at="2026-11-06T17:00:00-08:00",
        check_run_id="check-1",
    )
    previous = (
        previous_card
        or """---
license: apache-2.0
pretty_name: Drug Approvals Knowledge Provider (DAKP)
---
# old card
| Version | Nodes | Edges | File size | Files |
|---|---:|---:|---:|---|
| v1.27.0 | 2 | 1 | 1.0 MB | [nodes](old) |
"""
    )
    monkeypatch.setattr(card_module, "tablassert_version", lambda: "22.2.0-test")
    return build_dataset_card(
        version="1.27.1",
        files=files,
        stats=compute_kg_stats(nodes, edges),
        rig_path=rig,
        delta=delta,
        previous_card=previous,
        verification=verification,
        built_at=datetime(2026, 11, 7, tzinfo=UTC),
    )


def test_card_computes_real_stats_checksums_and_preserves_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _card(tmp_path, monkeypatch)
    assert result.stats.nodes == 3
    assert result.stats.edges == 2
    assert result.stats.human_contraindication_edges == 1
    assert result.stats.publication_edges == 1
    assert result.stats.node_namespaces == {"CHEBI": 1, "MONDO": 1, "NCBITaxon": 1}
    assert "New data for 1 source on 2026-11-04" in result.markdown
    assert "| v1.27.0 | 2 | 1 |" in result.markdown
    assert "DRUG_APPROVALS_KP_1.27.1.nodes.ndjson" in result.markdown
    assert "+1 nodes / +1 edges vs v1.27.0" in result.markdown
    assert "Friday 17:00 America/Los_Angeles" in result.markdown
    assert "resolve/v1.27.0/1.27.0/DRUG_APPROVALS_KP_1.27.0.nodes.ndjson" in result.markdown
    for file in result.files:
        assert file.md5 in result.markdown


def test_card_updates_front_matter_to_the_new_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = """---
license: apache-2.0
configs:
  - config_name: 1.27.0_edges
    data_files:
      - 1.27.0/DRUG_APPROVALS_KP_1.27.0.edges.ndjson
    default: true
  - config_name: 1.27.0_nodes
    data_files:
      - 1.27.0/DRUG_APPROVALS_KP_1.27.0.nodes.ndjson
---
# old card
"""
    markdown = _card(tmp_path, monkeypatch, previous_card=previous).markdown
    metadata = yaml.safe_load(markdown.split("---", 2)[1])
    assert [config["config_name"] for config in metadata["configs"]] == [
        "1.27.1_edges",
        "1.27.1_nodes",
    ]
    assert [config["data_files"][0] for config in metadata["configs"]] == [
        "1.27.1/DRUG_APPROVALS_KP_1.27.1.edges.ndjson",
        "1.27.1/DRUG_APPROVALS_KP_1.27.1.nodes.ndjson",
    ]
    assert metadata["configs"][0]["default"] is True


def test_card_rejects_partial_front_matter_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = """---
configs:
  - config_name: 1.27.0_edges
    data_files:
      - wrong/path.ndjson
---
"""
    with pytest.raises(ValueError, match="front matter"):
        _card(tmp_path, monkeypatch, previous_card=previous)


def test_card_migrates_legacy_release_history_and_infers_previous_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = """# old card
| Version | Tag | Nodes | Edges | Biolink |
| --- | --- | --- | --- | --- |
| 1.27.0 | `v1.27.0` | 2 | 1 | 4.4.6 |
| 1.26.0 | `v1.26.0` | 1,500 | 100,000 | 4.4.6 |
"""
    markdown = _card(tmp_path, monkeypatch, previous_card=previous, previous_version=None).markdown
    assert "Previous published version: **1.27.0**" in markdown
    assert "+1 nodes / +1 edges vs v1.27.0" in markdown
    assert "| v1.27.0 | 2 | 1 | see tag |" in markdown
    assert "| v1.26.0 | 1500 | 100000 | see tag |" in markdown
    assert "resolve/v1.26.0/1.26.0/DRUG_APPROVALS_KP_1.26.0.RIG.yaml" in markdown


def test_card_does_not_duplicate_current_release_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = """# old card
| Version | Nodes | Edges | File size | Files |
|---|---:|---:|---:|---|
| v1.27.1 | 3 | 2 | 0.6 KB | [nodes](old) |
| v1.27.0 | 2 | 1 | 1.0 MB | [nodes](old) |
"""
    markdown = _card(tmp_path, monkeypatch, previous_card=previous).markdown
    assert markdown.count("| v1.27.1 |") == 1
    assert markdown.count("| v1.27.0 |") == 1


def test_kg_stats_excludes_non_human_and_non_contraindication_edges(tmp_path: Path) -> None:
    nodes, edges, _ = _write_outputs(tmp_path)
    with edges.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "subject": "CHEBI:6801",
                    "predicate": "biolink:contraindicated_in",
                    "object": "MONDO:0005148",
                    "species_context_qualifier": "NCBITaxon:10090",
                    "has_supporting_studies": [{"id": "not-a-publication-field"}],
                }
            )
            + "\n"
        )
    stats = compute_kg_stats(nodes, edges)
    assert stats.human_contraindication_edges == 1
    assert stats.publication_edges == 1


def test_card_links_likes_and_stars_badges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    markdown = _card(tmp_path, monkeypatch).markdown
    assert "[![Hugging Face likes]" in markdown
    assert "query=%24.likes&label=likes" in markdown
    assert "(https://huggingface.co/datasets/SkyeAv/drug-approvals-kp)" in markdown
    assert "[![GitHub stars]" in markdown
    assert "img.shields.io/github/stars/glusman-team/graph-monorepo" in markdown
    assert "(https://github.com/glusman-team/graph-monorepo)" in markdown


def test_dataset_card_golden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _card(tmp_path, monkeypatch)
    golden = Path(__file__).parents[1] / "fixtures" / "release" / "dataset_card.md"
    if os.environ.get("DAKP_UPDATE_CARD_GOLDEN") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(result.markdown, encoding="utf-8")
    assert result.markdown == golden.read_text(encoding="utf-8")

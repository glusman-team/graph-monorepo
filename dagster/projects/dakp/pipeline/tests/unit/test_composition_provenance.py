"""Unit tests for the post-build composition provenance rescope pass.

Why this exists: Tablassert stamps composition ``sources`` from a static per-table
template, so Drugs@FDA-only pairs (issue #59's Demulen case) ship naming DailyMed even
when the DailyMed release contains no SPL for the product. These tests pin the contract
that the pass rescopes each edge to exactly the FDA sources its assertion row recorded,
leaves every other byte alone, fails loudly on graph/table disagreement, and is
idempotent -- the properties that make it safe to run between the build and the
contract gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dakp_pipeline.composition_provenance import align_composition_provenance

DAILEMED_ONLY_TSV = "subject_text\tobject_text\tupstream_resource_ids\nDemulen 1/50-28\tETHYNODIOL DIACETATE\tinfores:dailymed\n"
DRUGSFDA_ONLY_TSV = "subject_text\tobject_text\tupstream_resource_ids\nDemulen 1/50-28\tETHYNODIOL DIACETATE\tinfores:drugs-at-fda\n"
BOTH_TSV = "subject_text\tobject_text\tupstream_resource_ids\nDemulen 1/50-28\tETHYNODIOL DIACETATE\tinfores:dailymed|infores:drugs-at-fda\n"

PRIMARY = {
    "resource_id": "infores:drugapprovals-kp",
    "resource_role": "primary_knowledge_source",
    "upstream_resource_ids": ["infores:dailymed", "infores:drugs-at-fda"],
    "source_record_urls": ["https://linkouts.skyelanegoetz.com/edges/abc"],
}
SUPPORTING_DAILYMED = {"resource_id": "infores:dailymed", "resource_role": "supporting_data_source"}
SUPPORTING_DRUGSFDA = {
    "resource_id": "infores:drugs-at-fda",
    "resource_role": "supporting_data_source",
}


def composition_edge(**overrides: object) -> dict[str, object]:
    edge: dict[str, object] = {
        "id": "536d3561-8c9d-39e2-a314-1aa20dee2f1c",
        "subject": "UMLS:C5235109",
        "original_subject": "DEMULEN 1/50-28",
        "object": "CHEBI:31580",
        "original_object": "ETHYNODIOL DIACETATE",
        "predicate": "biolink:has_active_ingredient",
        "sources": [dict(PRIMARY), dict(SUPPORTING_DAILYMED), dict(SUPPORTING_DRUGSFDA)],
    }
    edge.update(overrides)
    return edge


def treats_edge() -> dict[str, object]:
    return {
        "id": "treats-1",
        "subject": "UMLS:1",
        "object": "UMLS:2",
        "predicate": "biolink:treats",
        "sources": [dict(PRIMARY), dict(SUPPORTING_DAILYMED)],
    }


def write_edges(path: Path, *edges: dict[str, object]) -> None:
    path.write_text(
        "".join(json.dumps(edge, ensure_ascii=False) + "\n" for edge in edges), encoding="utf-8"
    )


def read_edges(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_drugsfda_only_edge_stops_naming_dailymed(tmp_path: Path) -> None:
    """The issue-#59 case: a Drugs@FDA-only pair must not cite DailyMed anywhere."""
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, composition_edge())
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(DRUGSFDA_ONLY_TSV, encoding="utf-8")

    report = align_composition_provenance(edges, tsv)

    assert report == {"edges_checked": 1, "edges_rescoped": 1}
    sources = read_edges(edges)[0]["sources"]
    assert sources[0]["upstream_resource_ids"] == ["infores:drugs-at-fda"]
    # The primary's edge-linkout record URL survives the rewrite.
    assert sources[0]["source_record_urls"] == [PRIMARY["source_record_urls"][0]]
    # Only the contributing supporting entry survives.
    assert [s["resource_id"] for s in sources[1:]] == ["infores:drugs-at-fda"]


def test_dailymed_only_edge_stops_naming_drugsfda(tmp_path: Path) -> None:
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, composition_edge())
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(DAILEMED_ONLY_TSV, encoding="utf-8")

    assert align_composition_provenance(edges, tsv) == {"edges_checked": 1, "edges_rescoped": 1}

    sources = read_edges(edges)[0]["sources"]
    assert sources[0]["upstream_resource_ids"] == ["infores:dailymed"]
    assert [s["resource_id"] for s in sources[1:]] == ["infores:dailymed"]


def test_both_source_edge_is_byte_identical(tmp_path: Path) -> None:
    """A pair fed by both sources already matches the template: no rewrite, no replace."""
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, composition_edge())
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(BOTH_TSV, encoding="utf-8")
    before = edges.read_bytes()

    assert align_composition_provenance(edges, tsv) == {"edges_checked": 1, "edges_rescoped": 0}
    assert edges.read_bytes() == before


def test_pass_is_idempotent(tmp_path: Path) -> None:
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, composition_edge())
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(DRUGSFDA_ONLY_TSV, encoding="utf-8")

    first = align_composition_provenance(edges, tsv)
    once = edges.read_bytes()
    second = align_composition_provenance(edges, tsv)

    assert first["edges_rescoped"] == 1
    assert second == {"edges_checked": 1, "edges_rescoped": 0}
    assert edges.read_bytes() == once


def test_non_composition_edges_and_blank_lines_keep_exact_bytes(tmp_path: Path) -> None:
    treats = treats_edge()
    edges = tmp_path / "kg.edges.ndjson"
    edges.write_text(
        json.dumps(treats, ensure_ascii=False)
        + "\n\n"
        + json.dumps(composition_edge(), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(DRUGSFDA_ONLY_TSV, encoding="utf-8")
    treats_line = json.dumps(treats, ensure_ascii=False) + "\n"

    assert align_composition_provenance(edges, tsv) == {"edges_checked": 1, "edges_rescoped": 1}
    lines = edges.read_text(encoding="utf-8").splitlines(keepends=True)
    assert lines[0] == treats_line
    assert lines[1] == "\n"


def test_pair_key_ignores_case_and_whitespace(tmp_path: Path) -> None:
    """TSV and edge spellings differ in case/whitespace, like real DailyMed vs Drugs@FDA text."""
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(
        edges,
        composition_edge(
            original_subject="demulen 1/50-28 ", original_object="ethynodiol diacetate"
        ),
    )
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(
        "subject_text\tobject_text\tupstream_resource_ids\nDEMULEN  1/50-28\t ETHYNODIOL DIACETATE\tinfores:drugs-at-fda\n",
        encoding="utf-8",
    )

    assert align_composition_provenance(edges, tsv)["edges_rescoped"] == 1
    assert read_edges(edges)[0]["sources"][0]["upstream_resource_ids"] == ["infores:drugs-at-fda"]


def test_merged_rows_union_their_contributors(tmp_path: Path) -> None:
    """Collision-folded spellings: the merged edge cites the union of its rows' sources."""
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(
        "subject_text\tobject_text\tupstream_resource_ids\n"
        "Demulen 1/50-28\tETHYNODIOL DIACETATE\tinfores:drugs-at-fda\n"
        "demulen 1/50-28\tethynodiol diacetate\tinfores:dailymed\n",
        encoding="utf-8",
    )
    contributors = align_composition_provenance
    # contributors_by_pair is exercised through the pass: the edge must keep both sources.
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, composition_edge())
    assert contributors(edges, tsv) == {"edges_checked": 1, "edges_rescoped": 0}
    assert read_edges(edges)[0]["sources"][0]["upstream_resource_ids"] == [
        "infores:dailymed",
        "infores:drugs-at-fda",
    ]


def test_edge_without_original_texts_fails_loudly(tmp_path: Path) -> None:
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, composition_edge(original_subject=None, original_object=None))
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(BOTH_TSV, encoding="utf-8")

    with pytest.raises(RuntimeError, match="original_subject"):
        align_composition_provenance(edges, tsv)


def test_edge_without_assertion_row_fails_loudly(tmp_path: Path) -> None:
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, composition_edge(original_object="MYSTERY INGREDIENT"))
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(BOTH_TSV, encoding="utf-8")

    with pytest.raises(RuntimeError, match="no assertion-table row"):
        align_composition_provenance(edges, tsv)


def test_unknown_tsv_upstream_fails_loudly(tmp_path: Path) -> None:
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(
        "subject_text\tobject_text\tupstream_resource_ids\nA\tB\tinfores:somewhere-else\n",
        encoding="utf-8",
    )
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(
        edges,
        composition_edge(
            subject="UMLS:A", original_subject="A", object="UMLS:B", original_object="B"
        ),
    )

    with pytest.raises(RuntimeError, match="non-FDA upstream"):
        align_composition_provenance(edges, tsv)


def test_edge_without_dakp_primary_fails_loudly(tmp_path: Path) -> None:
    edge = composition_edge(
        sources=[{"resource_id": "infores:elsewhere", "resource_role": "primary_knowledge_source"}]
    )
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, edge)
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(BOTH_TSV, encoding="utf-8")

    with pytest.raises(RuntimeError, match="primary knowledge source"):
        align_composition_provenance(edges, tsv)


def test_missing_provenance_columns_fail_loudly(tmp_path: Path) -> None:
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text("subject_text\tobject_text\nA\tB\n", encoding="utf-8")
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, composition_edge())

    with pytest.raises(RuntimeError, match="provenance columns"):
        align_composition_provenance(edges, tsv)


def test_empty_contributor_cell_fails_when_an_edge_matches(tmp_path: Path) -> None:
    """An edge-backed row with no upstream cell is a contract break, never a silent drop."""
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(
        "subject_text\tobject_text\tupstream_resource_ids\nDemulen 1/50-28\tETHYNODIOL DIACETATE\t\n",
        encoding="utf-8",
    )
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, composition_edge())

    with pytest.raises(RuntimeError, match="names no FDA upstream"):
        align_composition_provenance(edges, tsv)


def test_unrelated_pairs_in_the_tsv_are_ignored(tmp_path: Path) -> None:
    """TSV rows whose edges were dropped downstream must not break the pass."""
    tsv = tmp_path / "has_active_ingredient_assertions.tsv"
    tsv.write_text(
        "subject_text\tobject_text\tupstream_resource_ids\n"
        "Gone Product\tGONE INGREDIENT\tinfores:dailymed\n"
        "Demulen 1/50-28\tETHYNODIOL DIACETATE\tinfores:drugs-at-fda\n",
        encoding="utf-8",
    )
    edges = tmp_path / "kg.edges.ndjson"
    write_edges(edges, composition_edge())

    assert align_composition_provenance(edges, tsv) == {"edges_checked": 1, "edges_rescoped": 1}

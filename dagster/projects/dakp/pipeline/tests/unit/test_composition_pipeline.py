"""Composition (has_active_ingredient) config shape + post-build self-loop removal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dakp_pipeline import tablassert as tablassert_configs
from dakp_pipeline.selfloops import drop_screened_edges, drop_self_loop_edges

_TABLE = "has_active_ingredient_assertions"


def test_composition_config_shape() -> None:
    pytest.importorskip("tablassert")
    config = tablassert_configs.table_config(_TABLE)
    statement = config["statement"]

    assert statement["predicate"] == "has_active_ingredient"
    # No qualifiers at all: composition is a property of the product.
    assert "qualifiers" not in statement
    assert tablassert_configs._TABLE_QUALIFIERS[_TABLE] == ()

    # Chemical subject AND object; no disease allow-list anywhere.
    assert statement["subject"]["prioritize"] == list(
        tablassert_configs.COMPOSITION_SUBJECT_PRIORITIZE
    )
    assert statement["object"]["prioritize"] == list(
        tablassert_configs.COMPOSITION_OBJECT_PRIORITIZE
    )
    assert "Disease" not in statement["object"]["prioritize"]
    assert "PhenotypicFeature" not in statement["object"]["prioritize"]
    assert "Disease" in statement["object"]["avoid"]
    assert "Protein" in statement["object"]["avoid"]
    # Every allowed object category pins the chemical-to-chemical class.
    assert set(statement["category_override"]) == set(
        tablassert_configs.COMPOSITION_OBJECT_PRIORITIZE
    )
    assert set(statement["category_override"].values()) == {
        "ChemicalEntityToChemicalEntityAssociation"
    }

    # No disease-wording row filters on this table.
    assert "reindex" not in config["source"]
    assert statement["object"]["exclude_regex"] == []
    assert statement["subject"]["exclude_regex"] == list(tablassert_configs._SUBJECT_EXCLUDE_REGEX)

    # Only the SPL set CURIEs are annotated; application numbers stay in the TSV.
    assert [(a["annotation"], a["encoding"]) for a in config["annotations"]] == [
        ("publications", "G")
    ]
    annotated = {a["annotation"] for a in config["annotations"]}
    assert "regulatory_approvals" not in annotated

    sources = config["provenance"]["override"]["sources"]
    primary = next(s for s in sources if s["resource_role"] == "primary_knowledge_source")
    assert primary["resource_id"] == "infores:drugapprovals-kp"
    assert primary["upstream_resource_ids"] == ["infores:dailymed", "infores:drugs-at-fda"]
    assert {
        s["resource_id"] for s in sources if s["resource_role"] == "supporting_data_source"
    } == {
        "infores:dailymed",
        "infores:drugs-at-fda",
    }
    assert tablassert_configs._TABLE_SPECS[_TABLE][2] == "knowledge_assertion"
    assert tablassert_configs._TABLE_SPECS[_TABLE][3] == tablassert_configs.AGENT_TYPE


def test_composition_association_class_accepts_the_predicate() -> None:
    biolink = pytest.importorskip("tablassert.biolink")
    cls = biolink.resolve_association_class(
        "biolink:ChemicalEntityToChemicalEntityAssociation", "biolink:has_active_ingredient"
    )
    assert cls.__name__ == "ChemicalEntityToChemicalEntityAssociation"
    fields = biolink.class_fields(cls)
    assert "publications" in fields
    # The reason FDA_regulatory_approvals is not annotated on this table.
    assert "regulatory_approvals" not in fields


def test_composition_config_validates_against_the_section_model() -> None:
    models = pytest.importorskip("tablassert.models")
    import yaml

    document = yaml.safe_load(tablassert_configs.table_yaml(_TABLE))
    models.Section.model_validate(document["template"])


def test_other_tables_keep_the_disease_guards() -> None:
    pytest.importorskip("tablassert")
    for table in (
        "approved_treats_assertions",
        "faers_applied_to_treat_assertions",
        "contraindication_assertions",
    ):
        statement = tablassert_configs.table_config(table)["statement"]
        assert statement["object"]["prioritize"] == list(tablassert_configs.OBJECT_PRIORITIZE)
        assert statement["category_override"] == dict(tablassert_configs.OBJECT_CATEGORY_OVERRIDE)
        assert "qualifiers" in statement


# --- post-build self-loop removal -------------------------------------------------


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    nodes = tmp_path / "g.nodes.ndjson"
    edges = tmp_path / "g.edges.ndjson"
    nodes.write_text(
        "\n".join(
            [
                json.dumps({"id": "UMLS:C0286650", "name": "atorvastatin calcium"}),
                json.dumps({"id": "CHEBI:39548", "name": "Atorvastatin"}),
                json.dumps({"id": "UMLS:C1527845", "name": "Vytorin"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    edges.write_text(
        "\n".join(
            [
                # The tautology: a product resolving to its own ingredient.
                json.dumps(
                    {
                        "id": "e1",
                        "subject": "UMLS:C0286650",
                        "predicate": "biolink:has_active_ingredient",
                        "object": "UMLS:C0286650",
                    }
                ),
                json.dumps(
                    {
                        "id": "e2",
                        "subject": "UMLS:C0286650",
                        "predicate": "biolink:has_active_ingredient",
                        "object": "CHEBI:39548",
                    }
                ),
                json.dumps(
                    {
                        "id": "e3",
                        "subject": "UMLS:C1527845",
                        "predicate": "biolink:has_active_ingredient",
                        "object": "CHEBI:39548",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return nodes, edges


def test_drop_self_loop_edges_removes_the_loop_and_its_orphans(tmp_path: Path) -> None:
    nodes, edges = _write_pair(tmp_path)
    report = drop_self_loop_edges(nodes, edges)
    assert report == {"self_loop_edges_dropped": 1, "orphan_nodes_dropped": 0}
    kept = [json.loads(line) for line in edges.read_text(encoding="utf-8").splitlines()]
    assert [e["id"] for e in kept] == ["e2", "e3"]
    # Vytorin is still referenced; nothing was orphaned.
    assert len(nodes.read_text(encoding="utf-8").splitlines()) == 3


def test_drop_self_loop_edges_drops_newly_orphaned_nodes(tmp_path: Path) -> None:
    nodes, edges = _write_pair(tmp_path)
    # Keep ONLY the self-loop: both its endpoints become unreferenced once it is dropped.
    edges.write_text(edges.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    report = drop_self_loop_edges(nodes, edges)
    assert report == {"self_loop_edges_dropped": 1, "orphan_nodes_dropped": 3}
    assert edges.read_text(encoding="utf-8") == ""
    assert nodes.read_text(encoding="utf-8") == ""


def test_drop_self_loop_edges_is_a_noop_without_loops(tmp_path: Path) -> None:
    nodes, edges = _write_pair(tmp_path)
    edges.write_text(
        "\n".join(edges.read_text(encoding="utf-8").splitlines()[1:]) + "\n", encoding="utf-8"
    )
    before = (nodes.read_bytes(), edges.read_bytes())
    report = drop_self_loop_edges(nodes, edges)
    assert report == {"self_loop_edges_dropped": 0, "orphan_nodes_dropped": 0}
    assert (nodes.read_bytes(), edges.read_bytes()) == before


def test_drop_self_loop_edges_ignores_other_predicates_and_bad_lines(tmp_path: Path) -> None:
    nodes, edges = _write_pair(tmp_path)
    treats_loop = json.dumps(
        {"id": "e9", "subject": "X:1", "predicate": "biolink:treats", "object": "X:1"}
    )
    edges.write_text(treats_loop + "\nnull\nnot json\n", encoding="utf-8")
    report = drop_self_loop_edges(nodes, edges)
    assert report["self_loop_edges_dropped"] == 0
    # Untouched: another family's self-loop (should not exist) and corrupt lines are the
    # contract/study gates' business, not this filter's.
    assert edges.read_text(encoding="utf-8") == treats_loop + "\nnull\nnot json\n"


def test_drop_screened_edges_covers_both_endpoints_and_subject_names(tmp_path: Path) -> None:
    nodes = tmp_path / "nodes.ndjson"
    edges = tmp_path / "edges.ndjson"
    nodes.write_text(
        "\n".join(
            json.dumps({"id": curie, "name": name})
            for curie, name in [
                ("CHEBI:15377", "Water"),
                ("RXCUI:1", "ethanol 0.7 ML/ML Topical Spray"),
                ("RXCUI:2", "Alcohol"),  # explicitly label-backed exception
                ("CHEBI:1", "Safe ingredient"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rows = [
        {"id": "water-object", "subject": "CHEBI:1", "object": "CHEBI:15377"},
        {"id": "water-subject", "subject": "CHEBI:15377", "object": "CHEBI:1"},
        {"id": "poison-name", "subject": "RXCUI:1", "object": "CHEBI:1"},
        {"id": "exception", "subject": "RXCUI:2", "object": "CHEBI:1"},
        {"id": "safe", "subject": "CHEBI:1", "object": "RXCUI:2"},
    ]
    edges.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert drop_screened_edges(nodes, edges) == {
        "screened_edges_dropped": 3,
        "screened_orphan_nodes_dropped": 2,
    }
    assert [json.loads(line)["id"] for line in edges.read_text().splitlines()] == [
        "exception",
        "safe",
    ]
    assert {json.loads(line)["id"] for line in nodes.read_text().splitlines()} == {
        "RXCUI:2",
        "CHEBI:1",
    }


def test_drop_screened_edges_noop_preserves_bytes(tmp_path: Path) -> None:
    nodes, edges = _write_pair(tmp_path)
    before = (nodes.read_bytes(), edges.read_bytes())
    assert drop_screened_edges(nodes, edges) == {
        "screened_edges_dropped": 0,
        "screened_orphan_nodes_dropped": 0,
    }
    assert (nodes.read_bytes(), edges.read_bytes()) == before

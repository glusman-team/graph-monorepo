"""Unit tests for the has_active_ingredient (drug composition) shaper.

Covers: substance + moiety rows, the Drugs@FDA semicolon split (parenthesized combinations
stay whole), the self-name guard, cross-source aggregation, provenance columns, animal
application citations, determinism, and the end-to-end TSV contract.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from dakp_pipeline.assertions.active_ingredients import (
    HasActiveIngredientShaper,
    build_has_active_ingredient_rows,
    split_active_ingredients,
)
from dakp_pipeline.io import schemas
from dakp_pipeline.io.contracts import ArtifactRef, TaskContext
from dakp_pipeline.paths import Workdir


def _ctx(tmp_path: Path) -> TaskContext:
    workdir = tmp_path / "work"
    Workdir(workdir).create()
    return TaskContext(workdir=workdir, fixture_root=None, params={})


def _ref(tmp_path: Path, name: str, rows: list[dict[str, str]]) -> ArtifactRef:
    path = tmp_path / name
    if name.endswith(".parquet"):
        pl.DataFrame(rows, infer_schema_length=None).write_parquet(path)
        media = schemas.PARQUET_MEDIA_TYPE
    else:
        raise ValueError(name)
    return ArtifactRef(
        uri=path, blake3="b3:" + name.encode().hex()[:60].ljust(64, "0"), media_type=media
    )


def _spl_products(rows: list[tuple[str, str, str, str, str]]) -> list[dict[str, str]]:
    """(set_id, product, ingredient, kind, unii) -> spl_products rows."""
    return [
        {
            "source_record_id": f"b3:{i:064d}",
            "spl_set_id": set_id,
            "product_code": "00001-0001",
            "product_name": product,
            "generic_name": ingredient,
            "ingredient_name": ingredient,
            "ingredient_unii": unii,
            "ingredient_kind": kind,
            "release_file": "dm_spl_release_human_rx_part1.zip",
            "xml_path": "x.xml",
        }
        for i, (set_id, product, ingredient, kind, unii) in enumerate(rows)
    ]


def _drugsfda(rows: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    """(appl_no, drug_name, active_ingredient) -> products.parquet rows."""
    return [
        {
            "source_record_id": f"drugsfda:product:{i}",
            "appl_no_raw": appl,
            "appl_type": "NDA",
            "appl_no": appl,
            "appl_no_stripped": appl.lstrip("0"),
            "product_no": "001",
            "drug_name": name,
            "active_ingredient": ingredient,
        }
        for i, (appl, name, ingredient) in enumerate(rows)
    ]


def test_split_active_ingredients_top_level_only() -> None:
    assert split_active_ingredients("EZETIMIBE; SIMVASTATIN") == ["EZETIMIBE", "SIMVASTATIN"]
    assert split_active_ingredients("IBUPROFEN") == ["IBUPROFEN"]
    assert split_active_ingredients(
        "TRIPLE SULFA (SULFABENZAMIDE;SULFACETAMIDE;SULFATHIAZOLE)"
    ) == ["TRIPLE SULFA (SULFABENZAMIDE;SULFACETAMIDE;SULFATHIAZOLE)"]
    assert split_active_ingredients("") == []
    assert split_active_ingredients(None) == []


def test_dailymed_substance_and_moiety_both_emit(tmp_path: Path) -> None:
    refs = [
        _ref(
            tmp_path,
            "spl_products.parquet",
            _spl_products(
                [
                    (
                        "set-1",
                        "Lipitor",
                        "ATORVASTATIN CALCIUM TRIHYDRATE",
                        "substance",
                        "UNII:48A5M73F4Q",
                    ),
                    ("set-1", "Lipitor", "ATORVASTATIN", "moiety", "UNII:A0JWA85V8F"),
                ]
            ),
        )
    ]
    rows = build_has_active_ingredient_rows(refs)
    assert [(r["subject_text"], r["object_text"]) for r in rows] == [
        ("Lipitor", "ATORVASTATIN"),
        ("Lipitor", "ATORVASTATIN CALCIUM TRIHYDRATE"),
    ]
    assert {r["ingredient_kind"] for r in rows} == {"substance", "moiety"}
    for row in rows:
        assert row["predicate"] == "biolink:has_active_ingredient"
        assert row["edge_evidence"] == "dailymed:set-1"
        assert row["supporting_spl_sets"].endswith("setid=set-1")
        assert row["primary_knowledge_source"] == "infores:drugapprovals-kp"
        assert row["knowledge_level"] == "knowledge_assertion"
        assert row["agent_type"] == "manual_validation_of_automated_agent"
        assert row["upstream_resource_ids"] == "infores:dailymed|infores:drugs-at-fda"


def test_self_named_products_are_dropped(tmp_path: Path) -> None:
    refs = [
        _ref(
            tmp_path,
            "spl_products.parquet",
            _spl_products(
                [
                    ("set-1", "Ibuprofen", "IBUPROFEN", "substance", "UNII:WK2XYI10QM"),
                    ("set-1", "Advil", "IBUPROFEN", "substance", "UNII:WK2XYI10QM"),
                ]
            ),
        ),
        _ref(tmp_path, "products.parquet", _drugsfda([("017977", "IBUPROFEN", "IBUPROFEN")])),
    ]
    rows = build_has_active_ingredient_rows(refs)
    assert [(r["subject_text"], r["object_text"]) for r in rows] == [("Advil", "IBUPROFEN")]


def test_drugsfda_rows_split_and_cite_the_application(tmp_path: Path) -> None:
    refs = [
        _ref(
            tmp_path,
            "products.parquet",
            _drugsfda([("021687", "VYTORIN", "EZETIMIBE; SIMVASTATIN")]),
        ),
        _ref(
            tmp_path,
            "applications.parquet",
            [{"appl_no_raw": "021687", "appl_type": "NDA", "appl_no": "021687"}],
        ),
    ]
    rows = build_has_active_ingredient_rows(refs)
    assert [(r["subject_text"], r["object_text"]) for r in rows] == [
        ("VYTORIN", "EZETIMIBE"),
        ("VYTORIN", "SIMVASTATIN"),
    ]
    for row in rows:
        assert row["FDA_regulatory_approvals"] == "NDA021687"
        # No SPL backing: nothing to cite on publications.
        assert row["edge_evidence"] == ""
        assert row["supporting_spl_sets"] == ""


def test_dailymed_and_drugsfda_merge_into_one_row(tmp_path: Path) -> None:
    refs = [
        _ref(
            tmp_path,
            "spl_products.parquet",
            _spl_products([("set-9", "Vytorin", "EZETIMIBE", "moiety", "UNII:EOR26LQQ24")]),
        ),
        _ref(tmp_path, "products.parquet", _drugsfda([("021687", "VYTORIN", "EZETIMIBE")])),
        _ref(
            tmp_path,
            "applications.parquet",
            [{"appl_no_raw": "021687", "appl_type": "NDA", "appl_no": "021687"}],
        ),
    ]
    rows = build_has_active_ingredient_rows(refs)
    assert len(rows) == 1
    row = rows[0]
    # Casing is the lexicographic minimum, independent of which source supplied it.
    assert row["subject_text"] == "VYTORIN"
    assert row["object_text"] == "EZETIMIBE"
    assert row["edge_evidence"] == "dailymed:set-9"
    assert row["FDA_regulatory_approvals"] == "NDA021687"
    # Both kinds survive the merge (DailyMed said moiety, Drugs@FDA is substance-granular).
    assert row["ingredient_kind"] == "moiety|substance"


def test_animal_set_cites_the_animal_application(tmp_path: Path) -> None:
    refs = [
        _ref(
            tmp_path,
            "spl_products.parquet",
            _spl_products([("set-a", "FleaWash", "FIPRONIL", "substance", "UNII:WQ96L45A7P")]),
        ),
        _ref(
            tmp_path,
            "spl_approvals.parquet",
            [{"spl_set_id": "set-a", "approval_id": "NADA141459", "approval_type": "NADA"}],
        ),
    ]
    rows = build_has_active_ingredient_rows(refs)
    assert rows[0]["FDA_regulatory_approvals"] == "NADA141459"


def test_rows_are_deterministic_under_input_reordering(tmp_path: Path) -> None:
    products = _spl_products(
        [
            ("set-2", "Vytorin", "SIMVASTATIN", "substance", "UNII:AGG2FN16EV"),
            ("set-1", "Vytorin", "EZETIMIBE", "substance", "UNII:EOR26LQQ24"),
        ]
    )
    fda = _drugsfda([("021687", "VYTORIN", "EZETIMIBE; SIMVASTATIN")])
    first = build_has_active_ingredient_rows(
        [
            _ref(tmp_path, "spl_products.parquet", products),
            _ref(tmp_path, "products.parquet", fda),
        ]
    )
    second = build_has_active_ingredient_rows(
        [
            _ref(tmp_path, "spl_products.parquet", list(reversed(products))),
            _ref(tmp_path, "products.parquet", fda),
        ]
    )
    assert first == second


def test_empty_inputs_produce_no_rows(tmp_path: Path) -> None:
    assert build_has_active_ingredient_rows([]) == []


def test_shaper_writes_tsv_with_no_curie_or_qualifier_columns(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    refs = [
        _ref(
            tmp_path,
            "spl_products.parquet",
            _spl_products([("set-1", "Lipitor", "ATORVASTATIN", "moiety", "UNII:A0JWA85V8F")]),
        )
    ]
    out = HasActiveIngredientShaper().transform(refs, ctx)
    assert len(out) == 1
    assert out[0].uri.name == "has_active_ingredient_assertions.tsv"
    frame = schemas.read_table(out[0].uri)
    assert frame.columns == schemas.HAS_ACTIVE_INGREDIENT_COLUMNS
    assert frame.height == 1
    # Text-only: identifiers come from fullmap resolution downstream.
    assert not any(c.endswith("_curie") for c in frame.columns)
    # No qualifier columns at all.
    assert not any(
        c.endswith("_text") and c not in ("subject_text", "object_text") for c in frame.columns
    )
    assert out[0].uri.read_bytes().startswith(b"subject_text\t")

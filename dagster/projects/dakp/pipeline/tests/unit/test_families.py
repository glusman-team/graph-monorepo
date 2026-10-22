"""Tests for the edge-family registry (the new-predicate extensibility seam).

These pin the wiring contract, not the shaper internals: every family table is
a registered assertion table with the exact shipped schema, the registry order
keeps observed-uses after approved-treats (it consumes that table), and the
metadata constants match what the shapers actually emit (a registry/shaper
drift would silently mislabel every edge of a family in the built KG).
"""

from __future__ import annotations

import pytest

from dakp_pipeline import families
from dakp_pipeline.io import schemas


def test_registry_tables_match_the_shipped_schemas() -> None:
    """Every family table is a declared assertion table with its shipped column order."""
    shipped = {
        "approved_treats_assertions",
        "faers_applied_to_treat_assertions",
        "contraindication_assertions",
    }
    assert {family.table for family in families.all_families()} == shipped
    for family in families.all_families():
        assert schemas.ASSERTION_TABLES[family.table]  # registered with a real column list


def test_registry_order_keeps_dependencies_before_consumers() -> None:
    """faers_applied_to_treat consumes the approved_treats table: it must follow it."""
    names = [family.name for family in families.all_families()]
    assert names.index("approved_treats") < names.index("faers_applied_to_treat")


def test_family_metadata_matches_the_shaper_constants() -> None:
    """The registry carries the same predicate/table constants the shapers emit."""
    from dakp_pipeline.assertions import approved_treats, contraindications, observed_uses

    assert families.get_family("approved_treats").predicate == approved_treats._PREDICATE
    assert families.get_family("approved_treats").table == approved_treats._TABLE
    assert families.get_family("faers_applied_to_treat").predicate == observed_uses._PREDICATE
    assert families.get_family("faers_applied_to_treat").table == observed_uses._TABLE
    assert families.get_family("faers_applied_to_treat").ner_profile is None  # FAERS bypasses NER
    assert families.get_family("contraindications").predicate == contraindications._PREDICATE
    assert families.get_family("contraindications").table == contraindications._TABLE


def test_get_family_names_the_missing_family() -> None:
    """A bad lookup fails loudly and lists the registered families."""
    with pytest.raises(KeyError, match="no edge family"):
        families.get_family("does_not_exist")


def test_shapers_satisfy_the_protocol() -> None:
    """Every registered shaper exposes transform(inputs, ctx) -> refs."""
    for family in families.all_families():
        shaper = family.shaper()
        assert callable(shaper.transform)

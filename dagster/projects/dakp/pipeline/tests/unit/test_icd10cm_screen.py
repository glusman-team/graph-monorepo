"""The in-DAG ICD-10-CM screen derivation (dakp_pipeline.icd10cm_screen).

The DAG derives the poison screen at build time (derive_icd10cm_screen /
derive_icd10cm_curies assets); these tests pin the derivation logic itself: the exemption
vocabulary, the tier screen, the wording expansion, the fixed-point CURIE resolution, and
the JSON-ref contract the tablassert_configs stage consumes. The checked-in snapshots stay
the offline default -- the derive stage's ok/deferred JSON is what switches between them.
"""

import json
import zipfile
from pathlib import Path

import polars as pl
import pytest

from dakp_pipeline import icd10cm_screen
from dakp_pipeline.extract.icd10cm import ICD10CM_SUBJECT_TIERS_FILENAME, SubjectTier
from dakp_pipeline.icd10cm_screen import (
    denied_wordings,
    derive_curies,
    derive_denylist,
    name_spans,
    screen,
    screen_params,
    screened_curies,
    vocabulary,
)
from dakp_pipeline.io.content_hash import hash_file
from dakp_pipeline.io.contracts import ArtifactRef, TaskContext


def _ref(path: Path) -> ArtifactRef:
    return ArtifactRef(uri=path, blake3=hash_file(path), media_type="text/plain", rows=None)


def _ctx(tmp_path: Path, **params) -> TaskContext:
    return TaskContext(workdir=tmp_path, fixture_root=None, params=params)


# --- exemption vocabulary -------------------------------------------------------------


def test_name_spans_covers_every_contiguous_word_run():
    assert name_spans("lithium") == {"lithium"}
    assert name_spans("lithium carbonate") == {"lithium", "carbonate", "lithium carbonate"}


def test_vocabulary_mines_exempt_terms_from_both_label_corpora():
    exempt = vocabulary(["LITHIUM CARBONATE", "aspirin"], ["Warfarin;HYDROXYUREA", ""])
    assert exempt["lithium"] == "approved-treats subject: LITHIUM CARBONATE"
    assert exempt["warfarin"] == "Drugs@FDA active ingredient: Warfarin;HYDROXYUREA"
    # the medicinal exceptions ride along (diamorphine is pharma heroin, not the street entry)
    assert "diamorphine" in exempt
    # evidence is deterministic in which corpus member supplied a shared span
    assert exempt["aspirin"] == "approved-treats subject: aspirin"


# --- the tier screen -------------------------------------------------------------------


def _tier(name: str, tier: str) -> SubjectTier:
    return SubjectTier(name=name, icd10cm_code="T99.99", tier=tier)


def test_screen_keeps_only_non_therapeutic_tiers_and_drops_fragments_and_exempt_names():
    rows = [
        _tier("Warfarin", "toxic_effect"),
        _tier("Ethanol", "therapeutic"),  # carries an Adverse-effect code: never screened
        _tier("Gas", "toxic_effect"),  # a 3-char fragment, not a substance name
        _tier("Lithium", "no_accepted_medical_use"),  # label-backed: exempt
        _tier("heroin", "no_accepted_medical_use"),
    ]
    exempt = {"lithium": "approved-treats subject: LITHIUM CARBONATE"}
    entries, spared = screen(rows, exempt)
    assert entries == [
        ("heroin", "no_accepted_medical_use", "heroin"),
        ("warfarin", "toxic_effect", "Warfarin"),
    ]
    assert spared == [("Lithium", "approved-treats subject: LITHIUM CARBONATE")]


def test_screen_toxic_effect_wins_over_no_accepted_medical_use_on_one_key():
    entries, _ = screen(
        [_tier("Foo Bar", "no_accepted_medical_use"), _tier("FOO BAR", "toxic_effect")],
        {},
    )
    assert entries == [("foo bar", "toxic_effect", "FOO BAR")]


def test_denied_wordings_ship_the_key_and_the_table_spelling():
    assert denied_wordings([("p dichlorobenzene", "toxic_effect", "P-Dichlorobenzene")]) == (
        "p dichlorobenzene",
        "P-Dichlorobenzene",
    )
    # duplicate renderings deduplicate preserving first-seen order
    assert denied_wordings([("warfarin", "toxic_effect", "Warfarin")]).count("warfarin") == 1


# --- fixed-point CURIE resolution -------------------------------------------------------


class FakeResolver:
    """Scripted resolution: each call records its exclusions and returns the mapped hits."""

    def __init__(self, rounds: list[dict[str, list[tuple[str, str]]]]):
        self.rounds = rounds
        self.seen_exclusions: list[list[str]] = []

    def __call__(self, words, exclude_regex):
        self.seen_exclusions.append([p for p in exclude_regex if p.startswith("^CHEBI")])
        return self.rounds.pop(0)


def test_screened_curies_iterates_until_no_new_screened_curie_appears():
    # limonene first resolves to the racemate; once that CURIE is excluded, the (+) form
    # appears but its preferred name normalizes to a DIFFERENT (non-screened) key: the loop
    # must stop after recording only the screened identity.
    resolver = FakeResolver(
        [[("CHEBI:15384", "Limonene, (+/-)-")], [("CHEBI:15382", "(4R)-limonene")]]
    )
    screened = {"limonene"}
    entries = screened_curies(
        ["LIMONENE, (+)-"],
        resolve=resolver,
        screened_keys=screened,
        exempt_keys=set(),
        base_exclude_regex=[],
        prioritize=["Drug"],
        avoid=[],
    )
    assert entries == [("CHEBI:15384", "Limonene, (+/-)-", "limonene")]
    # round two ran with the first find excluded; the fall-through name was not screened, so
    # the loop stopped after two resolutions (fixed point)
    assert resolver.seen_exclusions[1] == ["^CHEBI:15384$"]
    assert len(resolver.seen_exclusions) == 2


def test_screened_curies_never_exempts_a_label_backed_medicine():
    resolver = FakeResolver([[("CHEBI:16236", "Ethanol")], []])
    entries = screened_curies(
        ["ethanol"],
        resolve=resolver,
        screened_keys={"ethanol"},
        exempt_keys={"ethanol"},
        base_exclude_regex=[],
        prioritize=[],
        avoid=[],
    )
    assert entries == []  # exempt: the medicine identity stays resolvable


# --- the DAG stage entry points ---------------------------------------------------------


@pytest.fixture
def screen_inputs(tmp_path: Path) -> dict[str, Path]:
    """The three upstream artifacts as real files: tiers TSV, Drugs@FDA zip, approved-treats TSV."""
    tiers = tmp_path / ICD10CM_SUBJECT_TIERS_FILENAME
    pl.DataFrame(
        {
            "name": ["Warfarin", "Lithium"],
            "icd10cm_code": ["T60.4X4", "T43.592"],
            "tier": ["toxic_effect", "no_accepted_medical_use"],
        }
    ).write_csv(tiers, separator="\t")

    products = tmp_path / "drugsfda.zip"
    with zipfile.ZipFile(products, "w") as zf:
        zf.writestr("Products.txt", "ActiveIngredient\tApplNo\nLITHIUM CARBONATE\t208566\n")

    treats = tmp_path / "approved_treats_assertions.tsv"
    treats.write_text(
        "subject_text\tobject_text\nLITHIUM CARBONATE\tbipolar disorder\n", encoding="utf-8"
    )
    return {"tiers": tiers, "zip": products, "treats": treats}


def test_derive_denylist_publishes_the_screen_json(tmp_path, screen_inputs):
    inputs = [_ref(p) for p in screen_inputs.values()]
    refs = derive_denylist(inputs, _ctx(tmp_path))
    assert len(refs) == 1
    payload = json.loads(refs[0].uri.read_text(encoding="utf-8"))
    # lithium is spared (the approved-treats subject/ingredient corpus), warfarin is denied
    assert [row[0] for row in payload["screened"]] == ["warfarin"]
    assert [row[0] for row in payload["exempt"]] == ["Lithium"]
    assert set(payload["wordings"]) == {"warfarin", "Warfarin"}


def test_derive_denylist_is_loud_about_missing_inputs(tmp_path):
    with pytest.raises(ValueError, match="missing inputs"):
        derive_denylist([], _ctx(tmp_path))


def test_derive_curies_defers_without_a_fullmap(tmp_path, screen_inputs):
    refs = derive_denylist([_ref(p) for p in screen_inputs.values()], _ctx(tmp_path))
    out = derive_curies(refs, _ctx(tmp_path))  # no params: no fullmap
    payload = json.loads(out[0].uri.read_text(encoding="utf-8"))
    assert payload["mode"] == "deferred"
    assert screen_params(refs + out) is None  # deferred => configs use the snapshot


def test_derive_curies_resolves_through_the_configured_resolver(
    tmp_path, screen_inputs, monkeypatch
):
    # the ok-path reuses the build's subject settings, which live in the tablassert package:
    # gated like the other tablassert-dependent tests (ungated CI covers the deferred path).
    pytest.importorskip("tablassert")
    refs = derive_denylist([_ref(p) for p in screen_inputs.values()], _ctx(tmp_path))
    calls = []

    def fake_resolver(fullmap: str):
        def resolve(words, exclude_regex):
            calls.append((list(words), list(exclude_regex)))
            return [("CHEBI:10033", "Warfarin")] if "warfarin" in words else []

        return resolve

    monkeypatch.setattr(icd10cm_screen, "quick_map_resolver", fake_resolver)
    out = derive_curies(refs, _ctx(tmp_path, fullmap="/fake/fullmap"))
    payload = json.loads(out[0].uri.read_text(encoding="utf-8"))
    assert payload["mode"] == "ok"
    assert payload["curies"] == [["CHEBI:10033", "Warfarin", "warfarin"]]
    assert screen_params(refs + out) == {
        "denylist": ["warfarin", "Warfarin"],
        "curies": ["CHEBI:10033"],
    }
    # the resolution runs the curated CURIE anchors as its base exclusions
    assert calls[0][1] == ["^UMLS:C1314429$"] or any(
        pattern.startswith("^UMLS:") for pattern in calls[0][1]
    )

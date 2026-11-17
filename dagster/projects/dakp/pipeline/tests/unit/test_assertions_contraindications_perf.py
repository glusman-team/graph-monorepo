"""Equivalence tests for the contraindication aggregate performance path.

The aggregate tail of :mod:`dakp_pipeline.assertions.contraindications` localizes every mined
mention against the work item's evidence spans. The performance rewrite must keep the exact
first-overlapping-span semantics, per-sentence qualifier grouping, and row output. These tests
pin: first-overlap precedence with overlapping spans; duplicate/equal mention values mapping
identically through the shared per-item memo; unknown offsets falling back (and memoizing the
miss); adjacent split spans mapping into their own source sentence; and end-to-end row output
with duplicate mentions plus per-sentence qualifier attachment.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from dakp_pipeline.assertions.contraindications import (
    ContraWorkItem,
    EvidenceSpan,
    _mention_local_span,
    _work_item_evidence,
    build_contraindication_rows,
)
from dakp_pipeline.io.content_hash import hash_file
from dakp_pipeline.io.contracts import ArtifactRef
from dakp_pipeline.ner.ner import DiseaseNER, Mention

CONTRA_LOINC = "34070-3"


def _ref(path: Path) -> ArtifactRef:
    return ArtifactRef(uri=path, blake3=hash_file(path), media_type="application/octet-stream")


def _sections(tmp_path: Path, rows: list[tuple[str, str, str]]) -> ArtifactRef:
    """spl_sections.parquet from (spl_set_id, spl_document_id, text) rows (all contraindication)."""
    frame = pl.DataFrame(
        {
            "spl_set_id": [r[0] for r in rows],
            "spl_document_id": [r[1] for r in rows],
            "clean_text": [r[2] for r in rows],
            "loinc_code": [CONTRA_LOINC for _ in rows],
        }
    )
    path = tmp_path / "spl_sections.parquet"
    frame.write_parquet(path)
    return _ref(path)


def _ingredients(tmp_path: Path, rows: list[tuple[str, str, str, str]]) -> ArtifactRef:
    """spl_ingredients.parquet from (role, spl_set_id, ingredient_name, ingredient_unii) rows."""
    frame = pl.DataFrame(
        {
            "role": [r[0] for r in rows],
            "spl_set_id": [r[1] for r in rows],
            "ingredient_name": [r[2] for r in rows],
            "ingredient_unii": [r[3] for r in rows],
        }
    )
    path = tmp_path / "spl_ingredients.parquet"
    frame.write_parquet(path)
    return _ref(path)


# --- first-overlap precedence ----------------------------------------------------


def test_first_overlapping_span_wins_for_overlapping_spans() -> None:
    mention = Mention(text="asthma", start=10, end=16, type="Disease", score=1.0)
    # Both spans overlap the mention; the FIRST one (source offset 100) must win, exactly as
    # the original linear scan did.
    spans = (
        EvidenceSpan(
            mined_start=8, mined_end=21, source_start=100, source_end=113, text="severe asthma"
        ),
        EvidenceSpan(
            mined_start=12, mined_end=24, source_start=200, source_end=212, text="asthma attack"
        ),
    )
    item = ContraWorkItem(
        set_id="SET",
        doc_id="DOC",
        text="mined text",
        source_text="source text",
        evidence_spans=spans,
    )
    assert _mention_local_span(item, mention) == ("severe asthma", 2, 8, 100)
    assert _work_item_evidence(item, mention) == "severe asthma"
    memo: dict[Mention, EvidenceSpan | None] = {}
    assert _mention_local_span(item, mention, memo) == ("severe asthma", 2, 8, 100)
    assert _work_item_evidence(item, mention, memo) == "severe asthma"


# --- duplicate / equal mention values --------------------------------------------


def test_equal_mention_values_share_one_span_answer() -> None:
    item = ContraWorkItem(
        set_id="SET",
        doc_id="DOC",
        text="asthma risk noted",
        source_text="asthma risk noted",
        evidence_spans=(
            EvidenceSpan(
                mined_start=0, mined_end=17, source_start=0, source_end=17, text="asthma risk noted"
            ),
        ),
    )
    first = Mention(text="asthma", start=0, end=6, type="Disease", score=1.0)
    duplicate = Mention(
        text="asthma", start=0, end=6, type="Disease", score=1.0
    )  # equal value, distinct instance
    memo: dict[Mention, EvidenceSpan | None] = {}
    assert _mention_local_span(item, first, memo) == _mention_local_span(item, duplicate, memo)
    assert _mention_local_span(item, duplicate, memo) == _mention_local_span(item, duplicate)
    assert _work_item_evidence(item, duplicate, memo) == _work_item_evidence(item, first)


def test_mention_outside_all_spans_falls_back_and_memoizes_the_miss() -> None:
    item = ContraWorkItem(
        set_id="SET",
        doc_id="DOC",
        text="joined text",
        source_text="  source text  ",
        evidence_spans=(
            EvidenceSpan(
                mined_start=0, mined_end=11, source_start=5, source_end=16, text="joined text"
            ),
        ),
    )
    stray = Mention(text="rash", start=40, end=44, type="Disease", score=0.7)
    assert _mention_local_span(item, stray) is None
    assert _work_item_evidence(item, stray) == "source text"
    memo: dict[Mention, EvidenceSpan | None] = {}
    assert _mention_local_span(item, stray, memo) is None
    assert _work_item_evidence(item, stray, memo) == "source text"
    assert memo[stray] is None  # misses are memoized too: no repeated scans


# --- adjacent split spans ---------------------------------------------------------


def test_adjacent_split_spans_map_into_their_own_sentence() -> None:
    text = "It is contraindicated in asthma. Avoid in hypotension."
    first_text = "It is contraindicated in asthma."
    second_text = "Avoid in hypotension."
    second_start = text.index(second_text)
    spans = (
        EvidenceSpan(
            mined_start=0,
            mined_end=len(first_text),
            source_start=0,
            source_end=len(first_text),
            text=first_text,
        ),
        EvidenceSpan(
            mined_start=second_start,
            mined_end=second_start + len(second_text),
            source_start=second_start,
            source_end=second_start + len(second_text),
            text=second_text,
        ),
    )
    item = ContraWorkItem(
        set_id="SET", doc_id="DOC", text=text, source_text=text, evidence_spans=spans
    )
    second_mention = Mention(
        text="hypotension",
        start=text.index("hypotension"),
        end=text.index("hypotension") + 11,
        type="Disease",
        score=1.0,
    )
    mapped = _mention_local_span(item, second_mention)
    assert mapped is not None
    sentence, start, end, source_start = mapped
    assert (sentence, source_start) == (second_text, second_start)
    assert sentence[start:end] == "hypotension"
    assert _work_item_evidence(item, second_mention) == second_text
    # The first sentence's mention still maps to the first span.
    first_mention = Mention(
        text="asthma",
        start=text.index("asthma"),
        end=text.index("asthma") + 6,
        type="Disease",
        score=1.0,
    )
    assert _work_item_evidence(item, first_mention) == first_text


# --- end-to-end aggregate equivalence ---------------------------------------------


def test_duplicate_mentions_and_per_sentence_qualifiers_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Duplicate object mentions aggregate once; qualifiers attach only within their sentence."""
    import dakp_pipeline.assertions.contraindications as contra_mod

    text = "It is contraindicated in asthma. Do not use in women with hypotension."
    sections = _sections(tmp_path, [("SET-Q", "SET-Q#d", text)])
    ingredients = _ingredients(tmp_path, [("active", "SET-Q", "DrugQ", "UNII:Q")])

    asthma = (text.index("asthma"), text.index("asthma") + 6)
    women = (text.index("women"), text.index("women") + 5)
    hypo = (text.index("hypotension"), text.index("hypotension") + 11)
    # The duplicate asthma mention is value-equal to the first (distinct instance), so the
    # object membership short-circuit must drop exactly one of the two.
    mentions = [
        Mention(text="asthma", start=asthma[0], end=asthma[1], type="Disease", score=0.9),
        Mention(text="asthma", start=asthma[0], end=asthma[1], type="Disease", score=0.9),
        Mention(text="women", start=women[0], end=women[1], type="BiologicalSex", score=0.8),
        Mention(text="hypotension", start=hypo[0], end=hypo[1], type="Disease", score=0.95),
    ]
    monkeypatch.setattr(
        contra_mod, "extract_contraindication_diseases", lambda _text, _ner: list(mentions)
    )

    ner = DiseaseNER(gazetteer={"asthma": "disease"})
    rows = build_contraindication_rows([sections, ingredients], ner)
    by_object = {r["object_text"]: r for r in rows}
    assert set(by_object) == {"asthma", "hypotension"}
    assert by_object["asthma"].get("sex_text", "") == ""
    assert by_object["hypotension"]["sex_text"] == "women"
    assert by_object["asthma"]["ner_confidence_score"] == "0.9"
    assert by_object["hypotension"]["ner_confidence_score"] == "0.95"
    # Deterministic, ordered output; a second run is byte-equal.
    again = build_contraindication_rows([sections, ingredients], ner)
    assert rows == again
    assert [r["object_text"] for r in rows] == ["asthma", "hypotension"]

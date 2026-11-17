"""Reaction descriptions must not inherit their section's contraindication semantics."""

import pytest

from dakp_pipeline.assertions.contraindications import (
    ContraWorkItem,
    EvidenceSpan,
    _classify_mention,
)
from dakp_pipeline.ner.ner import Mention


def _decision(text: str, term: str, section: str = "contraindications"):
    """Use real section offsets so the classifier cannot rely on a term denylist."""
    start = text.lower().index(term.lower())
    item = ContraWorkItem(
        set_id="asenapine",
        doc_id="label",
        text=text,
        source_text=text,
        section_kind=section,
        evidence_spans=(
            EvidenceSpan(
                mined_start=0,
                mined_end=len(text),
                source_start=0,
                source_end=len(text),
                text=text,
            ),
        ),
    )
    mention = Mention(text=term, start=start, end=start + len(term), type="disease", score=1.0)
    return _classify_mention(item, mention)


@pytest.mark.parametrize("section", ["contraindications", "warnings", "boxed_warning"])
@pytest.mark.parametrize(
    "prefix", ["Reactions have included", "These hypersensitivity reactions included:"]
)
def test_asenapine_reactions_are_not_contraindications(section: str, prefix: str) -> None:
    """Sections 4 and 5.6 describe reactions, not patient conditions prohibiting use."""
    text = f"{prefix} anaphylaxis, angioedema, hypotension, tachycardia, swollen tongue, dyspnea, wheezing and rash."
    decision = _decision(text, "swollen tongue", section)
    assert not decision.accepted
    assert decision.trigger == "described_reaction"
    assert decision.evidence_text == text


@pytest.mark.parametrize(
    "text",
    [
        "Contraindicated in patients with swollen tongue.",
        "Swollen tongue.",
        "Contraindicated in patients with a history of hypersensitivity reactions including swollen tongue.",
        "Reactions have included rash, but use is contraindicated in patients with swollen tongue.",
    ],
)
def test_reaction_wording_does_not_blacklist_genuine_contraindications(text: str) -> None:
    """The same phenotype remains a valid contraindication under independent evidence."""
    assert _decision(text, "swollen tongue").accepted


def test_negated_contraindication_still_wins() -> None:
    """Reaction recognition must not override the existing explicit-negation guard."""
    decision = _decision("Not contraindicated in patients with swollen tongue.", "swollen tongue")
    assert not decision.accepted
    assert decision.trigger == "explicit_negation"

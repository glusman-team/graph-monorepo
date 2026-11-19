"""Regression tests for the restored NER gold corpus and its executable scorer.

The historical-content hash prevents a future edit from silently replacing the original
benchmark while keeping its case count. Scorer tests protect exact offsets, duplicate
handling, negative controls, and the boundary between object and qualifier channels.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from dakp_pipeline.ner.dictionary import TYPE_DISEASE, TYPE_PHENOTYPE
from dakp_pipeline.ner.eval_dataset import NERCase, NERDataset
from dakp_pipeline.ner.lexical import Mention

_EVAL_PATH = Path(__file__).resolve().parents[1] / "eval" / "ner_gold_eval.py"
_SPEC = importlib.util.spec_from_file_location("ner_gold_eval", _EVAL_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
ner_gold_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ner_gold_eval)

_HISTORICAL_CASES_SHA256 = "8324051b1c1b5cf20b06fb74604d4e4cd002f564f004d7d0ed9bcd2ff7fb7ca7"


def _case(**changes: Any) -> dict[str, Any]:
    case = {
        "id": "case-asthma",
        "source": "dailymed",
        "loinc": "34070-3",
        "text": "Contraindicated in patients with asthma.",
        "mentions": [{"surface": "asthma", "type": "Disease"}],
    }
    case.update(changes)
    return case


def _gold(cases: list[dict[str, Any]] | None = None) -> NERDataset:
    rows = [_case()] if cases is None else cases
    return NERDataset(cases=tuple(NERCase.model_validate_json(json.dumps(row)) for row in rows))


def _default_gold() -> NERDataset:
    return NERDataset.load(ner_gold_eval.DEFAULT_GOLD, ner_gold_eval.DEFAULT_ISSUES)


def test_committed_gold_preserves_history_and_adds_issue_57() -> None:
    """Preserve canonical content while allowing either corpus to grow independently."""
    cases = NERDataset.load(ner_gold_eval.DEFAULT_GOLD).cases[:34]
    assert sum(len(case.mentions) for case in cases) == 42
    rows = [case.model_dump(mode="json", exclude_none=True) for case in cases]
    historical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(historical).hexdigest() == _HISTORICAL_CASES_SHA256
    issues = {case.id: case for case in NERDataset.load(ner_gold_eval.DEFAULT_ISSUES).cases}
    for case_id, surface, entity_type in [
        ("dailymed-losartan-aliskiren-diabetes", "diabetes", TYPE_DISEASE),
        ("dailymed-losartan-pregnancy", "pregnancy", TYPE_PHENOTYPE),
    ]:
        case = issues[case_id]
        assert [(m.surface, m.type) for m in case.mentions] == [(surface, entity_type)]
        assert case.origin is not None
        assert case.origin.endswith("/issues/57")


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"source": "ema"}, "source"),
        ({"loinc": None}, "requires a supported LOINC"),
        ({"loinc": "34069-5"}, "loinc"),
        ({"source": "faers"}, "must not declare a LOINC"),
        ({"text": " "}, "text"),
        ({"id": " "}, "id"),
        ({"mentions": {}}, "mentions"),
        ({"mentions": ["asthma"]}, "mentions"),
        ({"mentions": [{"surface": "", "type": "Disease"}]}, "surface"),
        ({"mentions": [{"surface": "asthma", "type": "BiologicalSex"}]}, "type"),
        ({"text": "dyspnea"}, "must occur exactly once"),
        ({"text": "asthma or asthma"}, "must occur exactly once"),
        (
            {"text": "aaa", "mentions": [{"surface": "aa", "type": "Disease"}]},
            "must occur exactly once",
        ),
        (
            {"mentions": [{"surface": "asthma", "type": "Disease", "start": 0}]},
            "does not match start",
        ),
        ({"mentions": [{"surface": "asthma", "type": "Disease", "start": True}]}, "start"),
        (
            {
                "text": "severe asthma",
                "mentions": [
                    {"surface": "severe asthma", "type": "Disease"},
                    {"surface": "asthma", "type": "Disease"},
                ],
            },
            "overlapping surfaces",
        ),
        ({"typo": 1}, "Extra inputs"),
    ],
)
def test_invalid_annotation_is_loud(changes: dict[str, Any], match: str) -> None:
    """Annotation mistakes must fail before they can change the meaning of a score."""
    with pytest.raises(ValueError, match=match):
        _gold([_case(**changes)])


def test_repeat_surfaces_can_use_character_offsets() -> None:
    """Repeated surfaces are legitimate when offsets name each specific occurrence."""
    gold = _gold(
        [
            _case(
                text="é asthma or asthma",
                mentions=[
                    {"surface": "asthma", "type": "Disease", "start": 2},
                    {"surface": "asthma", "type": "Disease", "start": 12},
                ],
            )
        ]
    )
    assert gold.cases[0].spans() == ((2, 8, "Disease"), (12, 18, "Disease"))


def test_ndjson_error_identifies_file_and_line(tmp_path: Path) -> None:
    """A malformed second line should lead annotators directly to the problem."""
    path = tmp_path / "gold.ndjson"
    path.write_text(json.dumps(_case()) + "\n{", encoding="utf-8")
    with pytest.raises(ValueError, match=r"gold\.ndjson:2:"):
        NERDataset.load(path)


def test_ndjson_rejects_empty_and_cross_file_duplicates(tmp_path: Path) -> None:
    """Prevent accidental empty runs and ID collisions between corpus splits."""
    path = tmp_path / "gold.ndjson"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="at least 1"):
        NERDataset.load(path)
    path.write_text(json.dumps(_case()) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicated: case-asthma"):
        NERDataset.load(path, path)


def _prediction(surface: str, text: str, entity_type: str = TYPE_DISEASE) -> dict[str, Any]:
    start = text.index(surface)
    return {
        "surface": surface,
        "start": start,
        "end": start + len(surface),
        "type": entity_type,
    }


def test_score_counts_duplicate_predictions_as_false_positives() -> None:
    """Set-based scoring would hide duplicate emissions and overstate precision."""
    gold = _gold()
    prediction = _prediction("asthma", gold.cases[0].text)
    report = ner_gold_eval.score_gold(
        gold, {"case-asthma": [prediction, copy.deepcopy(prediction)]}
    )
    assert report["summary"] == {
        "cases": 1,
        "expected": 1,
        "predicted": 2,
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 0,
        "precision": 0.5,
        "recall": 1.0,
        "f1": pytest.approx(2 / 3),
        "perfect_cases": 0,
    }
    assert report["rows"][0]["extra"] == [prediction]


def test_score_reports_missing_and_extra_with_exact_types() -> None:
    """A correct surface with a wrong canonical type is both a miss and an extra."""
    text = "asthma and headache"
    case = _case(
        text=text,
        mentions=[
            {"surface": "asthma", "type": "Disease"},
            {"surface": "headache", "type": "PhenotypicFeature"},
        ],
    )
    gold = _gold([case])
    predicted = [
        _prediction("asthma", text),
        _prediction("headache", text, TYPE_DISEASE),
    ]
    report = ner_gold_eval.score_gold(gold, {"case-asthma": predicted})
    assert report["summary"]["true_positive"] == 1
    assert report["summary"]["false_positive"] == 1
    assert report["summary"]["false_negative"] == 1
    assert report["rows"][0]["missing"][0]["surface"] == "headache"
    assert report["rows"][0]["extra"][0]["type"] == TYPE_DISEASE


def test_score_ignores_qualifier_channel() -> None:
    """A valid qualifier must not become an object false positive in this object-only metric."""
    text = "male patients with asthma"
    case = _case(text=text)
    gold = _gold([case])
    predictions = [
        _prediction("asthma", text),
        _prediction("male", text, "BiologicalSex"),
    ]
    report = ner_gold_eval.score_gold(gold, {"case-asthma": predictions})
    assert report["summary"]["false_positive"] == 0
    assert report["summary"]["perfect_cases"] == 1


def test_score_empty_gold_mentions_has_defined_zero_metrics() -> None:
    """Negative-control cases must not create NaN metrics or count as failures when empty."""
    case = _case(text="product used for unknown indication", mentions=[])
    report = ner_gold_eval.score_gold(_gold([case]), {"case-asthma": []})
    assert report["summary"]["precision"] == 0.0
    assert report["summary"]["recall"] == 0.0
    assert report["summary"]["f1"] == 0.0
    assert report["summary"]["perfect_cases"] == 1


def test_score_requires_exact_prediction_case_ids() -> None:
    """Missing predictions indicate failed extraction and must not be treated as empty output."""
    with pytest.raises(ValueError, match="prediction case ids differ"):
        ner_gold_eval.score_gold(_gold(), {})


@pytest.mark.parametrize(
    "prediction",
    [
        {"surface": "asthma", "start": -1, "end": 6, "type": "Disease"},
        {"surface": "asthma", "start": 0, "end": 500, "type": "Disease"},
        {"surface": "dyspnea", "start": 0, "end": 6, "type": "Disease"},
        {"surface": "asthma", "start": True, "end": 6, "type": "Disease"},
    ],
)
def test_invalid_prediction_span_is_loud(prediction: dict[str, Any]) -> None:
    """Invalid model offsets must fail rather than silently becoming false positives."""
    with pytest.raises(ValueError, match="Prediction"):
        ner_gold_eval.score_gold(_gold(), {"case-asthma": [prediction]})


class _FakeNER:
    """Profile-selection fake; no torch or model import is involved."""

    def __init__(self, profile: str, offline: bool, calls: list[tuple[str, bool]]) -> None:
        self.profile = profile
        self.offline = offline
        self.calls = calls

    def __enter__(self) -> _FakeNER:
        self.calls.append((self.profile, self.offline))
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def extract(self, text: str) -> list[Mention]:
        start = text.index("asthma")
        return [
            Mention(text="asthma", start=start, end=start + 6, type=TYPE_DISEASE, score=1.0),
            Mention(text="asthma", start=start, end=start + 6, type="BiologicalSex", score=1.0),
        ]


def test_extraction_uses_source_profiles_and_filters_qualifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The evaluator must exercise the same DailyMed and FAERS acceptance profiles as mining."""
    calls: list[tuple[str, bool]] = []

    def fake_factory(profile: str):
        return lambda *, offline: _FakeNER(profile, offline, calls)

    monkeypatch.setattr(
        ner_gold_eval.DiseaseNER,
        "for_contraindications",
        staticmethod(fake_factory("contraindications")),
    )
    monkeypatch.setattr(
        ner_gold_eval.DiseaseNER,
        "for_indications",
        staticmethod(fake_factory("indications")),
    )
    cases = [
        _case(),
        _case(id="faers-asthma", source="faers", loinc=None),
    ]
    predictions = ner_gold_eval.extract_predictions(_gold(cases).cases, offline=True)
    assert calls == [("contraindications", True), ("indications", True)]
    assert set(predictions) == {"case-asthma", "faers-asthma"}
    assert all(
        mention["type"] == TYPE_DISEASE for mentions in predictions.values() for mention in mentions
    )


def test_production_model_failure_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checkpoint denial must remain visible instead of becoming a passing offline evaluation."""

    def denied(*, offline: bool):
        raise RuntimeError(f"checkpoint denied (offline={offline})")

    monkeypatch.setattr(ner_gold_eval.DiseaseNER, "for_contraindications", staticmethod(denied))
    with pytest.raises(RuntimeError, match="checkpoint denied"):
        ner_gold_eval.extract_predictions(_gold().cases, offline=False)


def test_committed_gold_passes_offline_embedded_gazetteer() -> None:
    """The full offline regression must include every gold-backed gazetteer repair.

    This also proves the losartan clause emits diabetes alone: aliskiren, losartan, and
    coadministration must never become disease/phenotype objects through the embedded lexicon.
    """
    gold = _default_gold()
    predictions = ner_gold_eval.extract_predictions(gold.cases, offline=True)
    report = ner_gold_eval.score_gold(gold, predictions)
    assert report["summary"]["false_positive"] == 0
    assert report["summary"]["false_negative"] == 0
    assert report["summary"]["perfect_cases"] == len(gold.cases)


def test_offline_cli_writes_report_and_accepts_perfect_gold(tmp_path: Path) -> None:
    """The lightweight CLI must execute the real gazetteer and honor a perfect result."""
    gold_path = tmp_path / "gold.ndjson"
    output_path = tmp_path / "report.json"
    gold_path.write_text(json.dumps(_case()) + "\n", encoding="utf-8")
    result = ner_gold_eval.main(
        [
            "--gold",
            str(gold_path),
            "--offline",
            "--require-perfect",
            "--output",
            str(output_path),
        ]
    )
    assert result == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["mode"] == "offline"
    assert report["model_id"] == "embedded-gazetteer"
    assert report["summary"]["perfect_cases"] == 1


def test_offline_cli_require_perfect_rejects_extra_mentions(tmp_path: Path) -> None:
    """The quality gate must return nonzero when a negative-control case has an extra span."""
    case = _case(mentions=[])
    gold_path = tmp_path / "gold.ndjson"
    gold_path.write_text(json.dumps(case) + "\n", encoding="utf-8")
    result = ner_gold_eval.main(["--gold", str(gold_path), "--offline", "--require-perfect"])
    assert result == 1

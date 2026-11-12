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


def _gold(cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "dakp.ner.gold.v1",
        "annotation_policy": "Exact, unique disease and phenotype object surfaces.",
        "cases": [_case()] if cases is None else cases,
    }


def test_committed_gold_preserves_history_and_adds_issue_57() -> None:
    """The original 34-case benchmark and both source-derived issue cases must survive."""
    gold = ner_gold_eval.load_gold()
    assert len(gold["cases"]) == 36
    assert sum(len(case["mentions"]) for case in gold["cases"][:34]) == 42
    historical = json.dumps(gold["cases"][:34], sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(historical).hexdigest() == _HISTORICAL_CASES_SHA256
    assert gold["cases"][34:] == [
        {
            "id": "dailymed-losartan-aliskiren-diabetes",
            "source": "dailymed",
            "loinc": "34070-3",
            "text": "For coadministration with aliskiren in patients with diabetes.",
            "mentions": [{"surface": "diabetes", "type": "Disease"}],
        },
        {
            "id": "dailymed-losartan-pregnancy",
            "source": "dailymed",
            "loinc": "34066-1",
            "text": "When pregnancy is detected, discontinue losartan potassium as soon as possible.",
            "mentions": [{"surface": "pregnancy", "type": "PhenotypicFeature"}],
        },
    ]


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (None, "JSON object"),
        ({"schema_version": "wrong"}, "schema_version"),
        ({"schema_version": "dakp.ner.gold.v1"}, "annotation_policy"),
        (
            {
                "schema_version": "dakp.ner.gold.v1",
                "annotation_policy": "policy",
                "cases": [],
            },
            "non-empty list",
        ),
    ],
)
def test_invalid_gold_root_is_loud(payload: Any, match: str) -> None:
    """Malformed roots must stop evaluation instead of producing an empty success."""
    with pytest.raises(ValueError, match=match):
        ner_gold_eval.validate_gold(payload)


def test_duplicate_case_id_is_loud() -> None:
    """Duplicate IDs would overwrite predictions and hide one case's result."""
    with pytest.raises(ValueError, match="duplicated"):
        ner_gold_eval.validate_gold(_gold([_case(), _case()]))


@pytest.mark.parametrize(
    ("case_changes", "mention_changes", "match"),
    [
        ({"source": "ema"}, None, "unsupported source"),
        ({"loinc": "34069-5"}, None, "unsupported LOINC"),
        (
            {"source": "faers", "loinc": "34070-3", "mentions": []},
            None,
            "must not declare a LOINC",
        ),
        ({"text": " "}, None, "non-empty text"),
        ({"mentions": {}}, None, "mentions must be a list"),
        ({"mentions": ["asthma"]}, None, "mention must be an object"),
        ({}, {"surface": ""}, "non-empty surface"),
        ({}, {"type": "BiologicalSex"}, "non-object type"),
        ({}, {"surface": "dyspnea"}, "occurs 0 times"),
        ({"text": "asthma or asthma"}, None, "occurs 2 times"),
        (
            {
                "text": "severe asthma",
                "mentions": [
                    {"surface": "severe asthma", "type": "Disease"},
                    {"surface": "asthma", "type": "Disease"},
                ],
            },
            None,
            "overlapping surfaces",
        ),
    ],
)
def test_invalid_case_or_mention_is_loud(
    case_changes: dict[str, Any], mention_changes: dict[str, Any] | None, match: str
) -> None:
    """Ambiguous or non-object annotations must fail before offsets are scored."""
    case = _case(**case_changes)
    if mention_changes:
        case["mentions"][0].update(mention_changes)
    with pytest.raises(ValueError, match=match):
        ner_gold_eval.validate_gold(_gold([case]))


def test_load_gold_rejects_malformed_json(tmp_path: Path) -> None:
    """A truncated benchmark is an input error, not an empty benchmark."""
    path = tmp_path / "gold.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        ner_gold_eval.load_gold(path)


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
    prediction = _prediction("asthma", gold["cases"][0]["text"])
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

    def extract_batch(self, texts: list[str]) -> list[list[Mention]]:
        output = []
        for text in texts:
            start = text.index("asthma")
            output.append(
                [
                    Mention("asthma", start, start + 6, TYPE_DISEASE, 1.0),
                    Mention("asthma", start, start + 6, "BiologicalSex", 1.0),
                ]
            )
        return output


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
    predictions = ner_gold_eval.extract_predictions(cases, offline=True)
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
        ner_gold_eval.extract_predictions([_case()], offline=False)


def test_committed_gold_passes_offline_embedded_gazetteer() -> None:
    """The full offline regression must include every gold-backed gazetteer repair.

    This also proves the losartan clause emits diabetes alone: aliskiren, losartan, and
    coadministration must never become disease/phenotype objects through the embedded lexicon.
    """
    gold = ner_gold_eval.load_gold()
    predictions = ner_gold_eval.extract_predictions(gold["cases"], offline=True)
    report = ner_gold_eval.score_gold(gold, predictions)
    assert report["summary"]["false_positive"] == 0
    assert report["summary"]["false_negative"] == 0
    assert report["summary"]["perfect_cases"] == len(gold["cases"])


def test_offline_cli_writes_report_and_accepts_perfect_gold(tmp_path: Path) -> None:
    """The lightweight CLI must execute the real gazetteer and honor a perfect result."""
    gold_path = tmp_path / "gold.json"
    output_path = tmp_path / "report.json"
    gold_path.write_text(json.dumps(_gold()), encoding="utf-8")
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
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(_gold([case])), encoding="utf-8")
    result = ner_gold_eval.main(["--gold", str(gold_path), "--offline", "--require-perfect"])
    assert result == 1


def test_committed_issue_mentions_use_canonical_types() -> None:
    """Issue 57 must remain Disease/PhenotypicFeature, not medication or population labels."""
    gold = ner_gold_eval.load_gold()
    issue_mentions = [case["mentions"][0] for case in gold["cases"][34:]]
    assert [mention["type"] for mention in issue_mentions] == [
        TYPE_DISEASE,
        TYPE_PHENOTYPE,
    ]
    assert all(mention["surface"] not in {"aliskiren", "losartan"} for mention in issue_mentions)

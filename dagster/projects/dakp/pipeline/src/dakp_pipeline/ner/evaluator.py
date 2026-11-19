"""Exact object-span metrics and timed NER evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from time import perf_counter
from typing import Any

from dakp_pipeline.ner.dictionary import OBJECT_TYPES
from dakp_pipeline.ner.eval_dataset import NERCase, NERDataset
from dakp_pipeline.ner.ner import DiseaseNER, default_model_id

SpanKey = tuple[int, int, str]
Prediction = dict[str, Any]


def _expected(case: NERCase) -> list[Prediction]:
    return [
        {"surface": case.text[start:end], "start": start, "end": end, "type": entity_type}
        for start, end, entity_type in case.spans()
    ]


def _key(mention: Prediction) -> SpanKey:
    return (mention["start"], mention["end"], mention["type"])


def _predicted_objects(case: NERCase, mentions: Any) -> list[Prediction]:
    """Validate prediction offsets, then remove the separate qualifier channel."""
    if not isinstance(mentions, list):
        raise ValueError(f"Predictions for {case.id} must be a list")
    text = case.text
    objects = []
    for mention in mentions:
        if not isinstance(mention, dict):
            raise ValueError(f"Prediction for {case.id} must be an object")
        surface = mention.get("surface")
        start = mention.get("start")
        end = mention.get("end")
        entity_type = mention.get("type")
        if not isinstance(surface, str):
            raise ValueError(f"Prediction for {case.id} requires a string surface")
        if type(start) is not int or type(end) is not int:
            raise ValueError(f"Prediction for {case.id} requires integer offsets")
        if start < 0 or end <= start or end > len(text) or text[start:end] != surface:
            raise ValueError(f"Prediction for {case.id} has invalid span {surface!r}:{start}-{end}")
        if not isinstance(entity_type, str):
            raise ValueError(f"Prediction for {case.id} requires a string type")
        if entity_type not in OBJECT_TYPES:
            continue
        normalized = {
            "surface": surface,
            "start": start,
            "end": end,
            "type": entity_type,
        }
        if "score" in mention:
            normalized["score"] = mention["score"]
        objects.append(normalized)
    return objects


def _score(gold: NERDataset, predictions: Mapping[str, list[Prediction]]) -> dict[str, Any]:
    """Score exact start/end/type matches with duplicate-aware micro counts."""
    if not isinstance(predictions, Mapping):
        raise ValueError("NER predictions must be a mapping keyed by case id")
    case_ids = {case.id for case in gold.cases}
    if set(predictions) != case_ids:
        missing = sorted(case_ids - set(predictions))
        extra = sorted(set(predictions) - case_ids)
        raise ValueError(f"NER prediction case ids differ; missing={missing}, extra={extra}")

    rows = []
    for case in gold.cases:
        expected = _expected(case)
        predicted = _predicted_objects(case, predictions[case.id])
        expected_counts = Counter(_key(mention) for mention in expected)
        predicted_counts = Counter(_key(mention) for mention in predicted)
        matched = expected_counts & predicted_counts
        unmatched_predictions = predicted_counts - matched
        missing = [mention for mention in expected if matched[_key(mention)] == 0]
        extra = []
        for mention in predicted:
            key = _key(mention)
            if unmatched_predictions[key]:
                extra.append(mention)
                unmatched_predictions[key] -= 1
        rows.append(
            {
                "id": case.id,
                "source": case.source,
                "origin": case.origin,
                "expected": expected,
                "predicted": predicted,
                "missing": missing,
                "extra": extra,
            }
        )

    return {
        "summary": _case_metrics(rows),
        "by_source": {
            source: _case_metrics([r for r in rows if r["source"] == source])
            for source in sorted({r["source"] for r in rows})
        },
        "by_type": {kind: _span_metrics(rows, kind) for kind in OBJECT_TYPES},
        "rows": rows,
    }


def _span_metrics(rows: list[dict[str, Any]], kind: str | None = None) -> dict[str, Any]:
    def count(field: str) -> int:
        return sum(kind is None or span["type"] == kind for row in rows for span in row[field])

    expected, predicted = count("expected"), count("predicted")
    fp, fn = count("extra"), count("missing")
    tp = expected - fn
    return {
        "expected": expected,
        "predicted": predicted,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": tp / predicted if predicted else 0.0,
        "recall": tp / expected if expected else 0.0,
        "f1": 2 * tp / (expected + predicted) if expected + predicted else 0.0,
    }


def _case_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    perfect = sum(not r["missing"] and not r["extra"] for r in rows)
    negatives = [r for r in rows if not r["expected"]]
    clean = sum(not r["extra"] for r in negatives)
    return {
        **_span_metrics(rows),
        "cases": len(rows),
        "perfect_cases": perfect,
        "exact_case_rate": perfect / len(rows),
        "negative_cases": len(negatives),
        "negative_cases_clean": clean,
        "negative_case_accuracy": clean / len(negatives) if negatives else None,
    }


def _extract_predictions(cases: Sequence[NERCase], *, offline: bool) -> dict[str, list[Prediction]]:
    """Run the two pipeline profiles and return object mentions keyed by case id."""
    predictions: dict[str, list[Prediction]] = {}
    for source in ("dailymed", "faers"):
        selected = [case for case in cases if case.source == source]
        if not selected:
            continue
        factory = (
            DiseaseNER.for_contraindications if source == "dailymed" else DiseaseNER.for_indications
        )
        with factory(offline=offline) as ner:
            batches = [ner.extract(case.text) for case in selected]
        for case, mentions in zip(selected, batches, strict=True):
            predictions[case.id] = [
                {
                    "surface": mention.text,
                    "start": mention.start,
                    "end": mention.end,
                    "type": mention.type,
                    "score": mention.score,
                }
                for mention in mentions
                if mention.type in OBJECT_TYPES
            ]
    return predictions


class NEREvaluator:
    """Run both NER profiles on a validated dataset or score stored predictions.

    ``clock`` must be a monotonic seconds counter. Injection makes timing boundaries
    testable without sleeps. The timed run includes lazy model loading and teardown.
    """

    def __init__(self, dataset: NERDataset, *, clock: Callable[[], float] = perf_counter) -> None:
        self.dataset = dataset
        self.clock = clock

    def score(self, predictions: Mapping[str, list[Prediction]]) -> dict[str, Any]:
        """Compare exact character offsets and types, retaining duplicate false positives."""
        return _score(self.dataset, predictions)

    def run(self, *, offline: bool) -> dict[str, Any]:
        """Return model identity, quality, and wall time; never fall back to offline mode."""
        start = self.clock()
        predictions = _extract_predictions(self.dataset.cases, offline=offline)
        extracted = self.clock()
        report = self.score(predictions)
        finished = self.clock()
        return {
            "mode": "offline" if offline else "production",
            "model_id": "embedded-gazetteer" if offline else default_model_id(),
            "timing": {
                "elapsed_seconds": finished - start,
                "extraction_seconds": extracted - start,
                "scoring_seconds": finished - extracted,
            },
            **report,
        }

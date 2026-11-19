"""Evaluate exact disease/phenotype object spans against ``ner_gold.ndjson``.

Run offline with the embedded gazetteer, or with the real configured production checkpoint::

    uv run python tests/eval/ner_gold_eval.py --offline --output /tmp/ner-gold-offline.json
    uv run --group ner python tests/eval/ner_gold_eval.py --require-perfect \
        --output /tmp/ner-gold-production.json

DailyMed cases use the contraindication profile and FAERS cases use the indication
profile. Qualifier-channel mentions are deliberately excluded from this object-only
metric. Production mode never falls back to offline extraction or another checkpoint.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dakp_pipeline.ner.dictionary import OBJECT_TYPES
from dakp_pipeline.ner.eval_dataset import NERCase, NERDataset
from dakp_pipeline.ner.ner import DiseaseNER, default_model_id

DEFAULT_GOLD = Path(__file__).resolve().with_name("ner_gold.ndjson")
DEFAULT_ISSUES = DEFAULT_GOLD.with_name("ner_gold_issues.ndjson")

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


def score_gold(gold: NERDataset, predictions: Mapping[str, list[Prediction]]) -> dict[str, Any]:
    """Score exact start/end/type matches with duplicate-aware micro counts."""
    if not isinstance(predictions, Mapping):
        raise ValueError("NER predictions must be a mapping keyed by case id")
    case_ids = {case.id for case in gold.cases}
    if set(predictions) != case_ids:
        missing = sorted(case_ids - set(predictions))
        extra = sorted(set(predictions) - case_ids)
        raise ValueError(f"NER prediction case ids differ; missing={missing}, extra={extra}")

    rows = []
    true_positive = 0
    false_positive = 0
    false_negative = 0
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
        case_tp = sum(matched.values())
        true_positive += case_tp
        false_positive += len(predicted) - case_tp
        false_negative += len(expected) - case_tp
        rows.append(
            {
                "id": case.id,
                "expected": expected,
                "predicted": predicted,
                "missing": missing,
                "extra": extra,
            }
        )

    expected_total = true_positive + false_negative
    predicted_total = true_positive + false_positive
    precision = true_positive / predicted_total if predicted_total else 0.0
    recall = true_positive / expected_total if expected_total else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "summary": {
            "cases": len(gold.cases),
            "expected": expected_total,
            "predicted": predicted_total,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "perfect_cases": sum(not row["missing"] and not row["extra"] for row in rows),
        },
        "rows": rows,
    }


def extract_predictions(cases: Sequence[NERCase], *, offline: bool) -> dict[str, list[Prediction]]:
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold",
        type=Path,
        action="append",
        help="override default core and issue files; repeat to combine",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="use only the embedded gazetteer instead of the real production checkpoint",
    )
    parser.add_argument("--require-perfect", action="store_true")
    parser.add_argument("--output", type=Path, help="also write the complete JSON report here")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    gold = NERDataset.load(*(args.gold or (DEFAULT_GOLD, DEFAULT_ISSUES)))
    predictions = extract_predictions(gold.cases, offline=args.offline)
    report = {
        "mode": "offline" if args.offline else "production",
        "model_id": "embedded-gazetteer" if args.offline else default_model_id(),
        **score_gold(gold, predictions),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    for row in report["rows"]:
        if row["missing"] or row["extra"]:
            print(json.dumps({"id": row["id"], "missing": row["missing"], "extra": row["extra"]}))
    summary = report["summary"]
    perfect = summary["false_positive"] == 0 and summary["false_negative"] == 0
    return 1 if args.require_perfect and not perfect else 0


if __name__ == "__main__":
    raise SystemExit(main())

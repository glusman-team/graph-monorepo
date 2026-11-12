"""Evaluate exact disease/phenotype object spans against ``ner_gold.json``.

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
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

from dakp_pipeline.ner.dictionary import OBJECT_TYPES
from dakp_pipeline.ner.ner import DiseaseNER, default_model_id

SCHEMA_VERSION = "dakp.ner.gold.v1"
# Corpus drift guard only. Source selects the profile; all DailyMed sections currently
# use the contraindication acceptance profile, including boxed warnings.
DAILYMED_LOINCS = {"34066-1", "34070-3"}
SOURCES = {"dailymed", "faers"}
DEFAULT_GOLD = Path(__file__).resolve().with_name("ner_gold.json")

SpanKey = tuple[int, int, str]
Gold = dict[str, Any]
Case = dict[str, Any]
Prediction = dict[str, Any]


def validate_gold(payload: Any) -> Gold:
    """Return a valid gold document or raise a case-specific ``ValueError``.

    Strict validation protects the metric from silent annotation failures: a repeated
    surface makes offsets ambiguous, an overlapping mention breaks exact-span counting,
    and a non-object mention would mix the qualifier and object channels.
    """
    if not isinstance(payload, dict):
        raise ValueError("NER gold must be a JSON object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"NER gold schema_version must be {SCHEMA_VERSION!r}")
    policy = payload.get("annotation_policy")
    if not isinstance(policy, str) or not policy.strip():
        raise ValueError("NER gold annotation_policy must be a non-empty string")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("NER gold cases must be a non-empty list")

    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"NER gold case {index} must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"NER gold case {index} requires a non-empty id")
        if case_id in seen_ids:
            raise ValueError(f"NER gold case id is duplicated: {case_id}")
        seen_ids.add(case_id)
        source = case.get("source")
        if source not in SOURCES:
            raise ValueError(f"NER gold case {case_id} has unsupported source {source!r}")
        text = case.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"NER gold case {case_id} requires non-empty text")
        loinc = case.get("loinc")
        if source == "dailymed" and loinc not in DAILYMED_LOINCS:
            raise ValueError(f"NER gold case {case_id} has unsupported LOINC {loinc!r}")
        if source == "faers" and loinc is not None:
            raise ValueError(f"NER gold FAERS case {case_id} must not declare a LOINC")
        mentions = case.get("mentions")
        if not isinstance(mentions, list):
            raise ValueError(f"NER gold case {case_id} mentions must be a list")

        spans: list[tuple[int, int, str]] = []
        for mention in mentions:
            if not isinstance(mention, dict):
                raise ValueError(f"NER gold case {case_id} mention must be an object")
            surface = mention.get("surface")
            if not isinstance(surface, str) or not surface:
                raise ValueError(f"NER gold case {case_id} requires a non-empty surface")
            entity_type = mention.get("type")
            if entity_type not in OBJECT_TYPES:
                raise ValueError(
                    f"NER gold case {case_id} surface {surface!r} has non-object type "
                    f"{entity_type!r}"
                )
            occurrences = text.count(surface)
            if occurrences != 1:
                raise ValueError(
                    f"NER gold case {case_id} surface {surface!r} occurs {occurrences} "
                    "times; exactly one is required"
                )
            start = text.index(surface)
            spans.append((start, start + len(surface), surface))

        ordered = sorted(spans)
        for previous, current in pairwise(ordered):
            if current[0] < previous[1]:
                raise ValueError(
                    f"NER gold case {case_id} has overlapping surfaces "
                    f"{previous[2]!r} and {current[2]!r}"
                )
    return cast(Gold, payload)


def load_gold(path: Path = DEFAULT_GOLD) -> Gold:
    """Load and validate one gold file; missing or malformed input fails loudly."""
    return validate_gold(json.loads(path.read_text(encoding="utf-8")))


def _expected(case: Case) -> list[Prediction]:
    text = case["text"]
    expected = []
    for mention in case["mentions"]:
        start = text.index(mention["surface"])
        expected.append(
            {
                "surface": mention["surface"],
                "start": start,
                "end": start + len(mention["surface"]),
                "type": mention["type"],
            }
        )
    return expected


def _key(mention: Prediction) -> SpanKey:
    return (mention["start"], mention["end"], mention["type"])


def _predicted_objects(case: Case, mentions: Any) -> list[Prediction]:
    """Validate prediction offsets, then remove the separate qualifier channel."""
    if not isinstance(mentions, list):
        raise ValueError(f"Predictions for {case['id']} must be a list")
    text = case["text"]
    objects = []
    for mention in mentions:
        if not isinstance(mention, dict):
            raise ValueError(f"Prediction for {case['id']} must be an object")
        surface = mention.get("surface")
        start = mention.get("start")
        end = mention.get("end")
        entity_type = mention.get("type")
        if not isinstance(surface, str):
            raise ValueError(f"Prediction for {case['id']} requires a string surface")
        if type(start) is not int or type(end) is not int:
            raise ValueError(f"Prediction for {case['id']} requires integer offsets")
        if start < 0 or end <= start or end > len(text) or text[start:end] != surface:
            raise ValueError(
                f"Prediction for {case['id']} has invalid span {surface!r}:{start}-{end}"
            )
        if not isinstance(entity_type, str):
            raise ValueError(f"Prediction for {case['id']} requires a string type")
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


def score_gold(gold: Gold, predictions: Mapping[str, list[Prediction]]) -> dict[str, Any]:
    """Score exact start/end/type matches with duplicate-aware micro counts."""
    validate_gold(gold)
    if not isinstance(predictions, Mapping):
        raise ValueError("NER predictions must be a mapping keyed by case id")
    case_ids = {case["id"] for case in gold["cases"]}
    if set(predictions) != case_ids:
        missing = sorted(case_ids - set(predictions))
        extra = sorted(set(predictions) - case_ids)
        raise ValueError(f"NER prediction case ids differ; missing={missing}, extra={extra}")

    rows = []
    true_positive = 0
    false_positive = 0
    false_negative = 0
    for case in gold["cases"]:
        expected = _expected(case)
        predicted = _predicted_objects(case, predictions[case["id"]])
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
                "id": case["id"],
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
            "cases": len(gold["cases"]),
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


def extract_predictions(cases: Sequence[Case], *, offline: bool) -> dict[str, list[Prediction]]:
    """Run the two pipeline profiles and return object mentions keyed by case id."""
    predictions: dict[str, list[Prediction]] = {}
    for source in ("dailymed", "faers"):
        selected = [case for case in cases if case["source"] == source]
        if not selected:
            continue
        factory = (
            DiseaseNER.for_contraindications if source == "dailymed" else DiseaseNER.for_indications
        )
        with factory(offline=offline) as ner:
            batches = [ner.extract(case["text"]) for case in selected]
        for case, mentions in zip(selected, batches, strict=True):
            predictions[case["id"]] = [
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
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
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
    gold = load_gold(args.gold)
    predictions = extract_predictions(gold["cases"], offline=args.offline)
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

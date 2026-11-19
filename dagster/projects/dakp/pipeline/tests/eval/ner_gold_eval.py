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
from collections.abc import Sequence
from math import isfinite
from pathlib import Path

from dakp_pipeline.ner.eval_dataset import NERDataset
from dakp_pipeline.ner.evaluator import NEREvaluator

DEFAULT_GOLD = Path(__file__).resolve().with_name("ner_gold.ndjson")
DEFAULT_ISSUES = DEFAULT_GOLD.with_name("ner_gold_issues.ndjson")


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
    parser.add_argument("--min-f1", type=float, help="minimum exact micro F1 (0 to 1)")
    parser.add_argument("--max-elapsed-seconds", type=float, help="maximum timed NER run")
    parser.add_argument("--output", type=Path, help="also write the complete JSON report here")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.min_f1 is not None and not 0 <= args.min_f1 <= 1:
        parser.error("--min-f1 must be between 0 and 1")
    budget = args.max_elapsed_seconds
    if budget is not None and (not isfinite(budget) or budget <= 0):
        parser.error("--max-elapsed-seconds must be finite and positive")
    gold = NERDataset.load(*(args.gold or (DEFAULT_GOLD, DEFAULT_ISSUES)))
    report = NEREvaluator(gold).run(offline=args.offline)
    summary = report["summary"]
    elapsed = report["timing"]["elapsed_seconds"]
    failures = []
    if args.require_perfect and (summary["false_positive"] or summary["false_negative"]):
        failures.append("expected zero false positives and false negatives")
    if args.min_f1 is not None and summary["f1"] < args.min_f1:
        failures.append(f"F1 {summary['f1']:.6f} below {args.min_f1:g}")
    if budget is not None and elapsed > budget:
        failures.append(f"elapsed {elapsed:.6f}s exceeded budget {budget:g}s")
    report["gate_failures"] = failures
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "rows"}, indent=2, sort_keys=True
        )
    )
    for row in report["rows"]:
        if row["missing"] or row["extra"]:
            print(json.dumps({"id": row["id"], "missing": row["missing"], "extra": row["extra"]}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())

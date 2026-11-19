"""Stage issue gold candidates or record AI-agent silver cases without changing gold."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path

from dakp_pipeline.ner.eval_dataset import NERCase, NERDataset

EVAL_DIR = Path(__file__).resolve().parent
ISSUE_TITLE_PREFIX = "[DAKP NER]:"
_ISSUE_BLOCK = re.compile(r"^```ner-gold[ \t]*\n(.+?)\n```[ \t]*$", re.MULTILINE | re.DOTALL)
_ISSUE_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/([1-9][0-9]*)", re.ASCII)


def issue_cases(event_path: Path) -> NERDataset:
    """Read structured annotations from a GitHub issue event without executing its text."""
    event = json.loads(event_path.read_text(encoding="utf-8"))
    issue = event.get("issue") if isinstance(event, dict) else None
    if not isinstance(issue, dict) or not str(issue.get("title", "")).startswith(
        ISSUE_TITLE_PREFIX
    ):
        raise ValueError(f"expected an issue with title prefix {ISSUE_TITLE_PREFIX!r}")
    url = issue.get("html_url")
    match = _ISSUE_URL.fullmatch(url) if isinstance(url, str) else None
    if match is None:
        raise ValueError("expected a GitHub issue URL")
    body = issue.get("body")
    blocks = _ISSUE_BLOCK.findall(body.replace("\r\n", "\n")) if isinstance(body, str) else []
    if len(blocks) != 1:
        raise ValueError("expected exactly one fenced ner-gold block with NDJSON cases")
    cases = []
    for number, line in enumerate(blocks[0].split("\n"), 1):
        if not line.strip():
            continue
        try:
            raw = NERCase.model_validate_json(line)
        except ValueError as exc:
            raise ValueError(f"ner-gold block line {number}: {exc}") from exc
        if raw.id == "replace-this-example":
            raise ValueError("replace the issue template example before submitting")
        cases.append(raw.model_copy(update={"id": f"issue-{match[1]}-{raw.id}", "origin": url}))
    return NERDataset(cases=tuple(cases))


def _ndjson(cases: Sequence[NERCase]) -> str:
    return "".join(case.model_dump_json(exclude_none=True) + "\n" for case in cases)


def record_silver(candidate: Path, silver: Path, gold: NERDataset) -> int:
    """Record validated agent candidates; exact replays are no-ops and conflicts fail.

    The silver file is edited by one agent at a time per checkout. Candidates need
    provenance identifying the agent/model and a run, source, or issue reference.
    """
    incoming = NERDataset.load(candidate)
    existing = (
        NERDataset.load(silver).cases
        if silver.exists() and silver.read_text(encoding="utf-8").strip()
        else ()
    )
    gold_ids = {case.id for case in gold.cases}
    seen = {case.id: case for case in existing}
    additions = []
    for case in incoming.cases:
        if case.origin is None:
            raise ValueError(f"silver case {case.id} requires agent/model origin")
        if case.id in gold_ids:
            raise ValueError(f"silver case {case.id} conflicts with gold")
        if case.id in seen:
            if case != seen[case.id]:
                raise ValueError(f"silver case {case.id} conflicts with existing silver")
        else:
            additions.append(case)
    if additions:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=silver.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(_ndjson((*existing, *additions)))
            os.replace(temporary, silver)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return len(additions)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a GitHub event or import an agent's NDJSON into the silver corpus."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue", help="stage issue-derived gold for human review")
    issue.add_argument("--event", type=Path, required=True)
    issue.add_argument("--output", type=Path, required=True)
    agent = commands.add_parser("agent", help="record AI-agent findings in silver")
    agent.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "issue":
            cases = issue_cases(args.event).cases
            # Exclusive creation prevents this command from replacing a tracked corpus.
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(_ndjson(cases))
            print(f"Staged {len(cases)} issue gold candidate(s) for review")
        else:
            gold = NERDataset.load(
                EVAL_DIR / "ner_gold.ndjson", EVAL_DIR / "ner_gold_issues.ndjson"
            )
            added = record_silver(args.input, EVAL_DIR / "ner_silver.ndjson", gold)
            print(f"Recorded {added} silver case(s)")
    except (OSError, ValueError) as exc:
        # Do not echo untrusted payload values in Actions logs (workflow commands).
        if args.command == "issue":
            parser.error(
                "issue intake failed: check the template, annotations, and new output path"
            )
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

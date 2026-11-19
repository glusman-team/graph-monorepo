"""Protect the trust boundary between submitted issue cases and agent findings."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from dakp_pipeline.ner.eval_dataset import NERDataset

_EVAL = Path(__file__).resolve().parents[1] / "eval"
_SPEC = importlib.util.spec_from_file_location("ner_intake", _EVAL / "ner_intake.py")
assert _SPEC is not None
assert _SPEC.loader is not None
intake = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(intake)

_CASE = {"id": "migraine", "source": "faers", "text": "Migraine", "mentions": []}
_URL = "https://github.com/glusman-team/graph-monorepo/issues/123"


def _event(path: Path, **changes: object) -> Path:
    issue = {
        "title": "[DAKP NER]: unwanted mention",
        "body": f"Other issue text\r\n```ner-gold\r\n{json.dumps(_CASE)}\r\n```\r\n",
        "html_url": _URL,
        **changes,
    }
    path.write_text(json.dumps({"issue": issue}), encoding="utf-8")
    return path


def test_issue_stages_review_artifact_without_overwriting(tmp_path: Path) -> None:
    """GitHub owns the provenance; reruns cannot replace an existing output file."""
    output = tmp_path / "review.ndjson"
    event = _event(tmp_path / "event.json")
    args = ["issue", "--event", str(event), "--output", str(output)]
    assert intake.main(args) == 0
    (case,) = NERDataset.load(output).cases
    assert case.id == "issue-123-migraine"
    assert case.origin == _URL
    assert case.mentions == ()
    with pytest.raises(SystemExit) as error:
        intake.main(args)
    assert error.value.code == 2
    assert NERDataset.load(output).cases == (case,)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"title": "Other issue"}, "title prefix"),
        ({"html_url": "https://example.com/issues/123"}, "GitHub issue URL"),
        ({"body": None}, "exactly one fenced"),
        ({"body": "```ner-gold\n{\n```"}, "block line 1"),
        (
            {"body": f"```ner-gold\n{json.dumps(dict(_CASE, id='replace-this-example'))}\n```"},
            "replace the issue template example",
        ),
        ({"body": "```ner-gold\n{}\n```\n```ner-gold\n{}\n```"}, "exactly one fenced"),
        ({"body": f"```ner-gold\n{json.dumps(_CASE)}\n{json.dumps(_CASE)}\n```"}, "duplicated"),
    ],
)
def test_issue_rejects_invalid_submissions(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    """Invalid issues are visible but never become committed gold cases."""
    event = _event(tmp_path / "event.json", **changes)
    with pytest.raises(ValueError, match=message):
        intake.issue_cases(event)


def test_agent_records_silver_only_and_handles_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The agent CLI routes to silver and leaves both gold files byte-identical."""
    monkeypatch.setattr(intake, "EVAL_DIR", tmp_path)
    gold_paths = [tmp_path / name for name in ("ner_gold.ndjson", "ner_gold_issues.ndjson")]
    for number, path in enumerate(gold_paths):
        path.write_text(json.dumps(dict(_CASE, id=f"gold-{number}")) + "\n", encoding="utf-8")
    before = [path.read_bytes() for path in gold_paths]
    candidate = tmp_path / "candidate.ndjson"
    candidate.write_text(json.dumps(dict(_CASE, origin="model/run/abc")) + "\n", encoding="utf-8")
    args = ["agent", "--input", str(candidate)]
    assert intake.main(args) == 0
    assert intake.main(args) == 0
    (case,) = NERDataset.load(tmp_path / "ner_silver.ndjson").cases
    assert case.id == "migraine"
    assert case.origin == "model/run/abc"
    assert [path.read_bytes() for path in gold_paths] == before


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"origin": None}, "requires agent/model origin"),
        ({"id": "trusted"}, "conflicts with gold"),
        ({"text": "other"}, "conflicts with existing silver"),
    ],
)
def test_bad_silver_batch_leaves_prior_rows_unchanged(
    tmp_path: Path, changes: dict[str, object], match: str
) -> None:
    """A bad second candidate must not partially apply an otherwise valid batch."""
    gold_path, silver, candidate = (
        tmp_path / f"{name}.ndjson" for name in ("gold", "silver", "in")
    )
    gold_path.write_text(json.dumps(dict(_CASE, id="trusted")), encoding="utf-8")
    gold = NERDataset.load(gold_path)
    row = dict(_CASE, origin="model/run/abc")
    silver.write_text(json.dumps(row), encoding="utf-8")
    before = silver.read_bytes()
    candidate.write_text(
        json.dumps(dict(row, id="new")) + "\n" + json.dumps(row | changes), encoding="utf-8"
    )
    with pytest.raises(ValueError, match=match):
        intake.record_silver(candidate, silver, gold)
    assert silver.read_bytes() == before

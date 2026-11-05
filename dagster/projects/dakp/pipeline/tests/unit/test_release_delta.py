"""Release fingerprints, persisted state, and no-change/delta messages."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dakp_pipeline.io.artifact_store import ArtifactStore
from dakp_pipeline.io.manifests import SourceBlock
from dakp_pipeline.paths import Workdir
from dakp_pipeline.release.delta import (
    compute_delta,
    load_pending_delta,
    write_pending_delta,
)
from dakp_pipeline.release.state import (
    ReleaseState,
    SourceFingerprint,
    load_state,
    state_path,
    write_state,
)


def _refs(workdir: Path, source: str, contents: list[bytes], date: str) -> list:
    store = ArtifactStore(Workdir(workdir))
    refs = []
    for index, content in enumerate(contents):
        path = workdir / "downloads" / f"{source}-{index}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        ref, _ = store.ingest(
            path,
            media_type="application/octet-stream",
            alias=f"{source}/{index}.bin",
            source=SourceBlock(
                url=f"https://example.test/{source}/{index}",
                last_modified=date,
                retrieved_at="2026-11-06T17:00:00+00:00",
            ),
        )
        refs.append(ref)
    return refs


def test_fingerprint_is_order_independent_and_collects_dates(tmp_path: Path) -> None:
    refs = _refs(tmp_path, "faers", [b"one", b"two"], "Wed, 28 Oct 2026 20:28:53 GMT")
    forward = SourceFingerprint.from_refs(refs)
    reverse = SourceFingerprint.from_refs(list(reversed(refs)))
    assert forward.fingerprint == reverse.fingerprint
    assert forward.artifact_count == 2
    assert forward.upstream_dates == ("2026-10-28",)
    assert forward.retrieved_dates == ("2026-11-06",)
    assert forward.latest_retrieved_at == "2026-11-06T17:00:00+00:00"


def test_delta_only_reports_changed_sources_and_pregenerates_message(tmp_path: Path) -> None:
    daily = SourceFingerprint.from_refs(_refs(tmp_path, "daily", [b"same"], "2026-11-04"))
    old_faers = SourceFingerprint.from_refs(_refs(tmp_path, "faers", [b"old"], "2026-07-30"))
    new_faers = SourceFingerprint.from_refs(_refs(tmp_path, "faers-new", [b"new"], "2026-10-28"))
    previous = ReleaseState(
        "1.27.0", "2026-11-01T00:00:00+00:00", {"DailyMed": daily, "FAERS": old_faers}
    )
    delta = compute_delta({"DailyMed": daily, "FAERS": new_faers}, previous, check_run_id="run-1")
    assert delta.changed
    assert [change.source for change in delta.changes] == ["FAERS"]
    assert delta.message == "New data for 1 source on 2026-10-28: FAERS (2026-10-28)."


def test_no_delta_message_never_implies_a_release(tmp_path: Path) -> None:
    current = {
        "DailyMed": SourceFingerprint.from_refs(_refs(tmp_path, "daily", [b"same"], "2026-11-04"))
    }
    previous = ReleaseState("1.27.0", "2026-11-01T00:00:00+00:00", current)
    delta = compute_delta(current, previous)
    assert not delta.changed
    assert delta.message.startswith("New data for 0 sources")
    assert "no build, version bump, or upload" in delta.message


def test_missing_state_makes_every_source_changed(tmp_path: Path) -> None:
    current = {
        "DailyMed": SourceFingerprint.from_refs(_refs(tmp_path, "daily", [b"same"], "2026-11-04"))
    }
    delta = compute_delta(current, None)
    assert len(delta.changes) == 1
    assert delta.changes[0].previous is None
    assert delta.previous_version is None


def test_state_and_pending_delta_round_trip(tmp_path: Path) -> None:
    current = {
        "DailyMed": SourceFingerprint.from_refs(_refs(tmp_path, "daily", [b"same"], "2026-11-04"))
    }
    delta = compute_delta(current, None)
    write_pending_delta(tmp_path, delta, check_run_id="check-run")
    loaded_delta = load_pending_delta(tmp_path)
    assert loaded_delta.check_run_id == "check-run"
    assert loaded_delta.current == current
    assert loaded_delta.message == delta.message

    state = ReleaseState("1.27.1", "2026-11-07T00:00:00+00:00", current, {"nodes": 10, "edges": 20})
    write_state(state_path(tmp_path), state)
    assert load_state(state_path(tmp_path)) == state
    assert load_state(tmp_path / "missing.json") is None


def test_corrupt_state_fails_loudly(tmp_path: Path) -> None:
    path = state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_state(path)


def test_empty_or_vanished_sources_fail_instead_of_publishing(tmp_path: Path) -> None:
    daily = SourceFingerprint.from_refs(_refs(tmp_path, "daily", [b"same"], "2026-11-04"))
    with pytest.raises(ValueError, match="zero artifacts"):
        compute_delta({"DailyMed": SourceFingerprint.from_refs([])}, None)
    previous = ReleaseState(
        "1.27.0", "2026-11-01T00:00:00+00:00", {"DailyMed": daily, "FAERS": daily}
    )
    with pytest.raises(ValueError, match="absent"):
        compute_delta({"DailyMed": daily}, previous)

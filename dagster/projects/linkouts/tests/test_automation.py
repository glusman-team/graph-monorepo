from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import dagster as dg
import pytest

from linkouts_dag.automation import (
    LinkoutsAutomationSettings,
    automation_settings,
    build_dakp_load_request,
    dakp_release_tick,
)


def _settings(tmp_path: Path, target: Path) -> LinkoutsAutomationSettings:
    dictionary = tmp_path / "drugapprovals.dict"
    dictionary.write_bytes(b"dictionary")
    return LinkoutsAutomationSettings(
        enabled=True,
        target_store=f"file:{target}",
        dictionary_path=dictionary,
        zstd_level=3,
        sample_size=4,
        sample_seed=42,
        concurrency=2,
        threads=1,
        engine="fake",
        progress=False,
    )


def _metadata(tmp_path: Path, version: str = "1.27.1", **overrides) -> dict:
    nodes = tmp_path / f"DRUG_APPROVALS_KP_{version}.nodes.ndjson"
    edges = tmp_path / f"DRUG_APPROVALS_KP_{version}.edges.ndjson"
    nodes.write_text('{"id":"CHEBI:1"}\n', encoding="utf-8")
    edges.write_text('{"id":"e1"}\n', encoding="utf-8")
    payload = {
        "version": version,
        "dry_run": False,
        "verified": True,
        "linkouts_key": f"infores:drugapprovals-kp-{version}",
        "linkouts_nodes_path": str(nodes),
        "linkouts_edges_path": str(edges),
    }
    payload.update(overrides)
    return {
        key: value if key in {"dry_run", "verified"} else SimpleNamespace(value=value)
        for key, value in payload.items()
    }


def _event(metadata: dict) -> SimpleNamespace:
    return SimpleNamespace(
        dagster_event=SimpleNamespace(
            event_specific_data=SimpleNamespace(materialization=SimpleNamespace(metadata=metadata))
        )
    )


def test_build_dakp_load_request_uses_only_public_metadata(tmp_path: Path) -> None:
    target = tmp_path / "live.ndjson"
    settings = _settings(tmp_path, target)
    metadata = _metadata(tmp_path)

    request = build_dakp_load_request(settings, metadata)
    assert isinstance(request, dg.RunRequest)
    assert request.run_key == "linkouts-dakp-release-1.27.1"
    assert request.tags == {"linkouts/dakp_release": "1.27.1", "linkouts/ingest": "dakp"}
    config = request.run_config["ops"]["load_release"]["config"]
    assert config["key"] == "infores:drugapprovals-kp-1.27.1"
    assert config["nodes_path"] == metadata["linkouts_nodes_path"].value
    assert config["edges_path"] == metadata["linkouts_edges_path"].value
    assert config["dictionary_path"] == str(settings.dictionary_path)
    assert config["target_store"] == f"file:{target}"
    assert config["sample_size"] == 4
    assert config["sample_seed"] == 42
    assert config["concurrency"] == 2
    assert config["threads"] == 1
    assert config["engine"] == "fake"
    assert config["progress"] is False
    again = build_dakp_load_request(settings, metadata)
    assert isinstance(again, dg.RunRequest)
    assert again.run_key == request.run_key
    assert again.run_config == request.run_config


def test_dakp_release_tick_reads_asset_materialization_event(tmp_path: Path) -> None:
    settings = _settings(tmp_path, tmp_path / "live.ndjson")
    result = dakp_release_tick(None, _event(_metadata(tmp_path)), settings)
    assert isinstance(result, dg.RunRequest)
    assert result.run_key == "linkouts-dakp-release-1.27.1"


def test_sensor_skips_disabled_dry_run_unverified_and_incomplete_paths(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, tmp_path / "live.ndjson")
    assert "disabled" in skip_text(
        dakp_release_tick(None, _event(_metadata(tmp_path)), LinkoutsAutomationSettings())
    )
    dry_run = _metadata(tmp_path, dry_run=True)
    assert "dry run" in skip_text(dakp_release_tick(None, _event(dry_run), settings))
    unverified = _metadata(tmp_path, verified=False)
    assert "not verified" in skip_text(dakp_release_tick(None, _event(unverified), settings))
    incomplete = _metadata(tmp_path)
    Path(incomplete["linkouts_nodes_path"].value).unlink()
    assert "not complete" in skip_text(dakp_release_tick(None, _event(incomplete), settings))


def skip_text(result: dg.RunRequest | dg.SkipReason) -> str:
    assert isinstance(result, dg.SkipReason)
    return str(result)


def test_sensor_rejects_contract_mismatch_and_ephemeral_target(tmp_path: Path) -> None:
    settings = _settings(tmp_path, tmp_path / "live.ndjson")
    wrong_key = _metadata(tmp_path, linkouts_key="infores:other-kp-1.27.1")
    with pytest.raises(ValueError, match="does not match"):
        build_dakp_load_request(settings, wrong_key)
    ephemeral = LinkoutsAutomationSettings(
        enabled=True,
        target_store="mem://",
        dictionary_path=settings.dictionary_path,
    )
    with pytest.raises(ValueError, match="survive"):
        build_dakp_load_request(ephemeral, _metadata(tmp_path))


def test_automation_settings_resolve_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dictionary = tmp_path / "custom.dict"
    dictionary.write_bytes(b"dictionary")
    monkeypatch.setenv("LINKOUTS_DAKP_INGEST_ENABLED", "1")
    monkeypatch.setenv("LINKOUTS_DAKP_TARGET_STORE", "file:/tmp/live.ndjson")
    monkeypatch.setenv("LINKOUTS_DAKP_DICTIONARY_PATH", str(dictionary))
    monkeypatch.setenv("LINKOUTS_DAKP_ZSTD_LEVEL", "3")
    monkeypatch.setenv("LINKOUTS_DAKP_SAMPLE_SIZE", "16")
    monkeypatch.setenv("LINKOUTS_DAKP_SAMPLE_SEED", "42")
    monkeypatch.setenv("LINKOUTS_DAKP_CONCURRENCY", "2")
    monkeypatch.setenv("LINKOUTS_DAKP_THREADS", "1")
    monkeypatch.setenv("LINKOUTS_DAKP_ENGINE", "fake")
    monkeypatch.setenv("LINKOUTS_DAKP_PROGRESS", "0")

    settings = automation_settings()
    assert settings.enabled is True
    assert settings.target_store == "file:/tmp/live.ndjson"
    assert settings.dictionary_path == dictionary
    assert settings.zstd_level == 3
    assert settings.sample_size == 16
    assert settings.sample_seed == 42
    assert settings.concurrency == 2
    assert settings.threads == 1
    assert settings.engine == "fake"
    assert settings.progress is False

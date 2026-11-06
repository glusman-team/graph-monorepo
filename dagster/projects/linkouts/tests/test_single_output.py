from __future__ import annotations

import json
from pathlib import Path

import dagster as dg
from linkouts_pipeline.refs import load_refs, refs_path
from linkouts_pipeline.staging import LoadReport, PublicationReport, StageManifest

from linkouts_dag.config import PROJECT_ROOT
from linkouts_dag.definitions import build_definitions
from tests.conftest import (
    CRASH_STUB,
    PIPES_STUB,
    executed_stages,
    failure_text,
    first_edge_id,
    materialization,
    snapshot,
    write_worker,
)


def run_job(name: str, run_config: dict | None = None, *, enable_destructive: bool = False):
    defs = build_definitions()
    if enable_destructive:
        assert defs.resolve_job_def("linkouts_purge")
    return defs.resolve_job_def(name).execute_in_process(
        run_config=run_config or {}, raise_on_error=False
    )


def upload_config(nodes: Path, edges: Path, target: Path) -> dict:
    return {
        "ops": {
            "stage_release": {
                "config": {
                    "key": "infores:drugapprovals-kp-1.23.3",
                    "nodes_path": str(nodes),
                    "edges_path": str(edges),
                    "sample_size": 4,
                    "sample_seed": 42,
                    "sampled_at": "2026-01-01T00:00:00Z",
                    "concurrency": 2,
                    "engine": "fake",
                    "progress": False,
                }
            },
            "push_staged_release": {
                "config": {
                    "expected_key": "infores:drugapprovals-kp-1.23.3",
                    "target_store": f"file:{target}",
                    "concurrency": 2,
                }
            },
            "status_report": {"config": {"check_pools": True}},
            "probe_release": {"config": {"count": 3, "seed": 42}},
        }
    }


def load_config(
    nodes: Path, edges: Path, target: Path, key: str = "infores:drugapprovals-kp-1.23.3"
) -> dict:
    return {
        "ops": {
            "load_release": {
                "config": {
                    "key": key,
                    "nodes_path": str(nodes),
                    "edges_path": str(edges),
                    "target_store": f"file:{target}",
                    "sample_size": 4,
                    "sample_seed": 42,
                    "sampled_at": "2026-01-01T00:00:00Z",
                    "concurrency": 2,
                    "engine": "fake",
                    "progress": False,
                }
            }
        }
    }


def test_real_worker_upload_is_idempotent_and_publishes_only_upload_outputs(
    dag_env, monkeypatch, real_worker, kgx_inputs
) -> None:
    monkeypatch.setenv("LINKOUTS_WORKER_BIN", str(real_worker))
    nodes, edges = kgx_inputs
    target = dag_env.tmp / "published-store.ndjson"
    result = run_job("linkouts_upload", upload_config(nodes, edges, target))
    assert result.success, failure_text(result)
    assert set(executed_stages(result)) == {
        "stage_release",
        "push_staged_release",
        "status_report",
        "probe_release",
    }
    stage_ref = load_refs(dag_env.published, "stage_release")[0]
    manifest = StageManifest.read(Path(stage_ref.uri)).validate_for_publication()
    push_ref = load_refs(dag_env.published, "push_staged_release")[0]
    report = PublicationReport.model_validate_json(Path(push_ref.uri).read_text())
    assert report.source_digest == manifest.digest
    assert report.output_path == target
    assert target.is_file()
    assert materialization(result, "probe_release")["rows"] == 3

    before = target.read_bytes()
    again = run_job("linkouts_upload", upload_config(nodes, edges, target))
    assert again.success, failure_text(again)
    assert target.read_bytes() == before
    assert materialization(again, "stage_release")["cache_hit"] is True
    assert materialization(again, "push_staged_release")["cache_hit"] is True


def test_narrow_direct_load_preserves_history_and_publishes_report(
    dag_env, monkeypatch, real_worker, kgx_inputs
) -> None:
    monkeypatch.setenv("LINKOUTS_WORKER_BIN", str(real_worker))
    nodes, edges = kgx_inputs
    target = dag_env.tmp / "live-store.ndjson"
    first = run_job("linkouts_load_release", load_config(nodes, edges, target))
    assert first.success, failure_text(first)
    assert executed_stages(first) == ["load_release"]
    first_ref = load_refs(dag_env.published, "load_release")[0]
    first_report = LoadReport.model_validate_json(
        Path(first_ref.uri).read_text()
    ).validate_complete()
    assert first_report.store == f"file:{target}"
    assert first_report.created > 0
    assert first_report.sampled == 4
    assert target.is_file()
    assert materialization(first, "load_release")["target_store"] == f"file:{target}"

    second = run_job(
        "linkouts_load_release",
        load_config(nodes, edges, target, key="infores:drugapprovals-kp-1.23.4"),
    )
    assert second.success, failure_text(second)
    second_ref = load_refs(dag_env.published, "load_release")[0]
    second_report = LoadReport.model_validate_json(
        Path(second_ref.uri).read_text()
    ).validate_complete()
    assert second_report.merged > 0
    assert second_report.versions > first_report.versions


def test_narrow_push_reads_published_stage_and_does_not_rewrite_it(
    dag_env, monkeypatch, real_worker, kgx_inputs
) -> None:
    monkeypatch.setenv("LINKOUTS_WORKER_BIN", str(real_worker))
    nodes, edges = kgx_inputs
    first_target = dag_env.tmp / "first.ndjson"
    result = run_job("linkouts_upload", upload_config(nodes, edges, first_target))
    assert result.success, failure_text(result)
    stage_ref = refs_path(dag_env.published, "stage_release")
    stage_before = stage_ref.read_bytes()
    second_target = dag_env.tmp / "second.ndjson"
    narrow = run_job(
        "linkouts_push_staged_release",
        {
            "ops": {
                "push_staged_release": {
                    "config": {
                        "expected_key": "infores:drugapprovals-kp-1.23.3",
                        "target_store": f"file:{second_target}",
                        "concurrency": 2,
                    }
                }
            }
        },
    )
    assert narrow.success, failure_text(narrow)
    assert executed_stages(narrow) == ["push_staged_release"]
    assert stage_ref.read_bytes() == stage_before
    assert second_target.is_file()


def test_tampered_stage_manifest_is_never_published(
    dag_env, monkeypatch, real_worker, kgx_inputs
) -> None:
    monkeypatch.setenv("LINKOUTS_WORKER_BIN", str(real_worker))
    nodes, edges = kgx_inputs
    result = run_job("linkouts_upload", upload_config(nodes, edges, dag_env.tmp / "first.ndjson"))
    assert result.success, failure_text(result)
    stage = StageManifest.read(Path(load_refs(dag_env.published, "stage_release")[0].uri))
    stage.key = "infores:tampered-kp-9.9.9"
    stage.write(Path(load_refs(dag_env.published, "stage_release")[0].uri))
    result = run_job(
        "linkouts_push_staged_release",
        {
            "ops": {
                "push_staged_release": {
                    "config": {"target_store": f"file:{dag_env.tmp / 'second.ndjson'}"}
                }
            }
        },
    )
    assert not result.success
    assert "changed after stage_release publication" in failure_text(result)


def test_narrow_push_fails_loudly_without_published_stage(dag_env, monkeypatch) -> None:
    monkeypatch.setenv(
        "LINKOUTS_WORKER_BIN", str(write_worker(dag_env.tmp / "stub.py", PIPES_STUB))
    )
    result = run_job(
        "linkouts_push_staged_release",
        {"ops": {"push_staged_release": {"config": {"target_store": "file:/tmp/out"}}}},
    )
    assert not result.success
    assert "linkouts_stage_release" in failure_text(result)


def test_worker_crash_does_not_publish_refs(dag_env, monkeypatch, kgx_inputs) -> None:
    monkeypatch.setenv(
        "LINKOUTS_WORKER_BIN", str(write_worker(dag_env.tmp / "crash.py", CRASH_STUB))
    )
    nodes, edges = kgx_inputs
    before = snapshot(dag_env.tmp)
    result = run_job(
        "linkouts_stage_release",
        {
            "ops": {
                "stage_release": {
                    "config": {
                        "key": "infores:test-kp-1.0.0",
                        "nodes_path": str(nodes),
                        "edges_path": str(edges),
                        "engine": "fake",
                    }
                }
            }
        },
    )
    assert not result.success
    assert not refs_path(dag_env.published, "stage_release").exists()
    assert snapshot(dag_env.tmp) == before


def test_status_job_uses_strict_pipes_extras(dag_env, monkeypatch) -> None:
    monkeypatch.setenv(
        "LINKOUTS_WORKER_BIN", str(write_worker(dag_env.tmp / "stub.py", PIPES_STUB))
    )
    result = run_job(
        "linkouts_status_report",
        {"ops": {"status_report": {"config": {"store": "mem://"}}}},
    )
    assert result.success, failure_text(result)
    ref = load_refs(dag_env.published, "status_report")[0]
    assert json.loads(Path(ref.uri).read_text())["operation"] == "status"


def test_destructive_job_requires_both_definition_and_runtime_opt_in(dag_env, monkeypatch) -> None:
    assert not any(
        job.name == "linkouts_purge"
        for job in build_definitions().get_repository_def().get_all_jobs()
    )
    monkeypatch.setenv("LINKOUTS_ENABLE_DESTRUCTIVE_JOBS", "1")
    monkeypatch.setenv(
        "LINKOUTS_WORKER_BIN", str(write_worker(dag_env.tmp / "stub.py", PIPES_STUB))
    )
    result = run_job(
        "linkouts_purge",
        {"ops": {"purge_store": {"config": {"store": "mem://", "scope": "all"}}}},
        enable_destructive=True,
    )
    assert result.success, failure_text(result)
    assert load_refs(dag_env.published, "purge_store")


def test_default_paths_are_not_used_by_tests(dag_env) -> None:
    assert dag_env.published != PROJECT_ROOT / "published"
    assert isinstance(dg.Definitions, type)


def test_inspection_jobs_execute_against_real_worker(
    dag_env, monkeypatch, real_worker, kgx_inputs
) -> None:
    monkeypatch.setenv("LINKOUTS_WORKER_BIN", str(real_worker))
    nodes, edges = kgx_inputs
    result = run_job(
        "linkouts_inspect_kgx",
        {"ops": {"inspect_kgx": {"config": {"edges_path": str(edges), "limit": 5}}}},
    )
    assert result.success, failure_text(result)
    assert materialization(result, "inspect_kgx")["rows"] == 5

    target = dag_env.tmp / "store.ndjson"
    upload = run_job("linkouts_upload", upload_config(nodes, edges, target))
    assert upload.success, failure_text(upload)
    edge = run_job(
        "linkouts_inspect_edge",
        {
            "ops": {
                "inspect_edge": {
                    "config": {"edge_id": first_edge_id(edges), "store": f"file:{target}"}
                }
            }
        },
    )
    assert edge.success, failure_text(edge)
    assert materialization(edge, "inspect_edge")["edge_id"] == first_edge_id(edges)

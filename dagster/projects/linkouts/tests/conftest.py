from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT = Path(__file__).resolve().parents[1]
GO_MODULE = PROJECT / "workers" / "go"
FIXTURES = PROJECT / "fixtures" / "kgx"

PIPES_STUB = """#!{python}
import json
from pathlib import Path

import dagster_pipes

with dagster_pipes.open_dagster_pipes() as context:
    extras = context.extras
    assert set(extras) == {{"operation", "config_json"}}, extras
    config = json.loads(extras["config_json"])
    if extras["operation"] in {{"status", "probe", "init", "purge"}}:
        output = Path(config["output_path"])
        output.write_text(json.dumps({{"stub": True, "operation": extras["operation"]}}) + "\\n")
        context.report_asset_materialization(
            metadata={{"operation": extras["operation"], "rows": 1, "report_path": str(output)}}
        )
    else:
        raise RuntimeError(f"stub does not implement {{extras['operation']}}")
"""

CRASH_STUB = """#!{python}
import sys

sys.exit(3)
"""


def write_worker(path: Path, source: str) -> Path:
    path.write_text(source.format(python=sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes().hex()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def materialization(result, stage: str) -> dict:
    events = [
        event
        for event in result.get_asset_materialization_events()
        if event.event_specific_data.materialization.asset_key.to_user_string() == stage
    ]
    assert len(events) == 1
    return {
        key: getattr(value, "value", value)
        for key, value in events[0].event_specific_data.materialization.metadata.items()
    }


def executed_stages(result) -> list[str]:
    return [event.step_key for event in result.get_step_success_events()]


def failure_text(result) -> str:
    return "\n".join(
        str(getattr(event.event_specific_data, "error", event))
        for event in result.get_step_failure_events()
    )


@pytest.fixture
def dag_env(tmp_path, monkeypatch) -> SimpleNamespace:
    published = tmp_path / "published"
    workdir = tmp_path / "work"
    monkeypatch.setenv("MO_PUBLISHED_ROOT", str(published))
    monkeypatch.setenv("LINKOUTS_WORKDIR", str(workdir))
    monkeypatch.delenv("LINKOUTS_ENABLE_DESTRUCTIVE_JOBS", raising=False)
    monkeypatch.delenv("COSMOS_PRIMARY_CONNECTION_STRING_RW", raising=False)
    monkeypatch.delenv("COSMOS_PRIMARY_CONNECTION_STRING_RO", raising=False)
    return SimpleNamespace(tmp=tmp_path, published=published, workdir=workdir)


@pytest.fixture(scope="session")
def real_worker(tmp_path_factory) -> Path:
    if shutil.which("go") is None:
        pytest.skip("Go is required for the real worker integration test")
    out = tmp_path_factory.mktemp("worker") / "linkouts-worker"
    env = {
        **os.environ,
        "GOTOOLCHAIN": "local",
        "GOMAXPROCS": os.environ.get("GOMAXPROCS", "4"),
        "GOFLAGS": os.environ.get("GOFLAGS", "-p=4"),
    }
    subprocess.run(
        ["go", "build", "-o", str(out), "./cmd/linkouts-worker"],
        cwd=GO_MODULE,
        env=env,
        check=True,
    )
    return out


@pytest.fixture
def kgx_inputs(tmp_path) -> tuple[Path, Path]:
    nodes = tmp_path / "nodes.ndjson"
    edges = tmp_path / "edges.ndjson"
    shutil.copyfile(FIXTURES / "nodes.ndjson", nodes)
    shutil.copyfile(FIXTURES / "edges.ndjson", edges)
    return nodes, edges


def first_edge_id(edges: Path) -> str:
    with edges.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                return json.loads(line)["id"]
    raise AssertionError(f"no edges in {edges}")

"""Edge-case tests for the ``dakp_pipeline.tablassert`` runner.

Covers the real ``run_subprocess`` + ``stream_subprocess`` bodies (genuine subprocesses,
including non-zero exits that must NOT raise), the streamed-line noise filter
(``_clean_stream_line``), and the ``_find_graph`` conventional-path fallback when no
``graph.yaml`` ref is present.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dakp_pipeline.io.contracts import ArtifactRef, TaskContext
from dakp_pipeline.paths import Workdir
from dakp_pipeline.tablassert import (
    _clean_stream_line,
    _find_graph,
    run_subprocess,
    stream_subprocess,
)


def _ctx(workdir: Path) -> TaskContext:
    return TaskContext(workdir=workdir, fixture_root=None, params={})


def test_run_subprocess_captures_stdout() -> None:
    completed = run_subprocess([sys.executable, "-c", "print('hello')"])
    assert completed.returncode == 0
    assert completed.stdout.strip() == "hello"


def test_run_subprocess_captures_nonzero_without_raising() -> None:
    completed = run_subprocess([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert completed.returncode == 3


def test_find_graph_falls_back_to_conventional_path(tmp_path: Path) -> None:
    other = ArtifactRef(
        uri=tmp_path / "approved_treats.yaml",
        blake3="b3:" + "0" * 64,
        media_type="application/yaml",
    )
    assert _find_graph([other], _ctx(tmp_path)) == Workdir(tmp_path).root / "tables" / "graph.yaml"


def test_find_graph_prefers_the_graph_ref(tmp_path: Path) -> None:
    graph = ArtifactRef(
        uri=tmp_path / "graph.yaml", blake3="b3:" + "1" * 64, media_type="application/yaml"
    )
    assert _find_graph([graph], _ctx(tmp_path)) == tmp_path / "graph.yaml"


# --- stream_subprocess: live streaming + noise filter -----------------------------


def test_stream_subprocess_returns_full_output_and_exit_code() -> None:
    completed = stream_subprocess([sys.executable, "-c", "print('stage 1'); print('stage 2')"])
    assert completed.returncode == 0
    assert completed.stdout == "stage 1\nstage 2\n"
    assert completed.stderr == ""


def test_stream_subprocess_captures_nonzero_without_raising() -> None:
    completed = stream_subprocess(
        [sys.executable, "-c", "import sys; print('err line', file=sys.stderr); sys.exit(3)"]
    )
    assert completed.returncode == 3
    assert completed.stderr == "err line\n"


def test_clean_stream_line_drops_progress_redraw_and_ansi_noise() -> None:
    assert (
        _clean_stream_line("  \r\r  42%|####      | 4/10 [00:01<00:02]\r")
        == "42%|####      | 4/10 [00:01<00:02]"
    )
    assert _clean_stream_line("\x1b[32m\u2713 Stage 1\x1b[0m\n") == "\u2713 Stage 1"
    assert _clean_stream_line("   \n") is None
    assert _clean_stream_line("\r\r") is None


def test_stream_subprocess_timeout_kills_and_raises_with_partial_output(tmp_path: Path) -> None:
    """A bounded wall time: an expired build is killed, never left hanging forever.

    A Tablassert build takes 20+ minutes; an unbounded wait with an empty log is the worst
    failure mode (the stage looks alive). On timeout the process is killed and
    TimeoutExpired carries what was collected so the report can explain the hang.
    """
    script = tmp_path / "slow.py"
    script.write_text(
        "import sys, time\n"
        "print('build: started', flush=True)\n"
        "time.sleep(30)\n"
        "print('build: should never print', flush=True)\n",
        encoding="utf-8",
    )

    with pytest.raises(subprocess.TimeoutExpired) as info:
        stream_subprocess([sys.executable, str(script)], cwd=tmp_path, timeout=2.0)

    assert "build: started" in (info.value.output or "")
    assert "should never print" not in (info.value.output or "")


def test_run_subprocess_accepts_a_timeout(tmp_path: Path) -> None:
    """run_subprocess forwards its timeout to subprocess.run (the shared contract)."""
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess([sys.executable, str(script)], cwd=tmp_path, timeout=1.0)


def test_graph_stem_parses_real_yaml_not_a_line_scan(tmp_path: Path) -> None:
    """The stem survives quoting, key order, and unrelated top-level keys.

    The old implementation line-scanned for ``name: `` / ``version: `` prefixes, so a quoted
    version, a reordered config, or a nested key that happens to start a line could produce
    the wrong stem and send the contract validator looking for files that do not exist.
    """
    from dakp_pipeline.tablassert import _graph_stem

    graph = tmp_path / "graph.yaml"
    graph.write_text(
        'tables:\n  - tables/approved_treats.yaml\nname: "DRUG_APPROVALS_KP"\nversion: "9.9.9"\nfullmap: /x/fullmap.redb\n',
        encoding="utf-8",
    )
    assert _graph_stem(graph) == "DRUG_APPROVALS_KP_9.9.9"


def test_graph_stem_raises_when_name_or_version_missing(tmp_path: Path) -> None:
    """A graph config without both keys fails loudly (never a guessed default)."""
    from dakp_pipeline.tablassert import _graph_stem

    graph = tmp_path / "graph.yaml"
    graph.write_text("name: DRUG_APPROVALS_KP\ntables: []\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing top-level name/version"):
        _graph_stem(graph)

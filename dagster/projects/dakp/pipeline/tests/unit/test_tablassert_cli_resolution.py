"""How ``dakp_pipeline.tablassert`` resolves the ``tablassert`` CLI argv prefix.

Kept apart from ``test_tablassert_configs.py``, which skips wholesale when the tablassert
package is absent: resolution is pure path logic and must be tested everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dakp_pipeline import tablassert as tablassert_configs


def test_command_prefix_prefers_the_interpreter_sibling_console_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The venv's own ``bin/tablassert`` wins even when the venv is not on ``PATH``.

    Regression: production ran ``.venv/bin/dagster`` without activating the venv, so
    ``shutil.which`` missed the console script and the ``uv run tablassert`` fallback,
    executed from the uv-project-less workdir, died with "Failed to spawn: tablassert".
    """
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    script = bin_dir / "tablassert"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(tablassert_configs.sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr(tablassert_configs.shutil, "which", lambda _name: None)

    assert tablassert_configs._command_prefix(None) == [str(script)]


def test_command_prefix_falls_back_to_path_then_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tablassert_configs.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(tablassert_configs.shutil, "which", lambda _name: "/opt/bin/tablassert")
    assert tablassert_configs._command_prefix(None) == ["/opt/bin/tablassert"]

    monkeypatch.setattr(tablassert_configs.shutil, "which", lambda _name: None)
    assert tablassert_configs._command_prefix(None) == ["uv", "run", "tablassert"]
    assert tablassert_configs._command_prefix("/src/Tablassert")[:3] == [
        "uv",
        "run",
        "--with-editable",
    ]

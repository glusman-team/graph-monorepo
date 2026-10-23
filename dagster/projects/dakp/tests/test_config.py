"""Env-to-stage-params contract of ``dakp_dag.config``.

Regression: the first production run on wenceslaus set ``DAKP_FULLMAP`` but ``kg_build``
still deferred, because the stage params carried ``fullmap`` without the
``run_tablassert`` switch ``tablassert.run`` dispatches on (DAKP derived it from the
fullmap in ``runtime.py``). The publication version was likewise never forwarded.
"""

from __future__ import annotations

import pytest

from dakp_dag.config import run_config, task_context


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DAKP_FULLMAP", "DAKP_PUBLICATION_VERSION", "DAKP_FORCE"):
        monkeypatch.delenv(name, raising=False)


def test_fullmap_enables_the_real_tablassert_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("MO_PUBLISHED_ROOT", str(tmp_path))
    monkeypatch.setenv("DAKP_FULLMAP", "/data/fullmap")
    monkeypatch.setenv("DAKP_PUBLICATION_VERSION", "1.25.0")

    params = task_context(run_config()).params
    assert params["fullmap"] == "/data/fullmap"
    assert params["run_tablassert"] is True
    assert params["publication_version"] == "1.25.0"


def test_no_fullmap_keeps_the_handoff_deferred(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("MO_PUBLISHED_ROOT", str(tmp_path))

    params = task_context(run_config()).params
    assert "fullmap" not in params
    assert not params.get("run_tablassert")
    assert "publication_version" not in params

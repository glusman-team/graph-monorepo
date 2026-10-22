"""Test-wide configuration shared by every suite.

Forces the stdlib ``urllib`` download path for the whole suite so the offline tests that
monkeypatch ``urllib.request.urlopen`` stay deterministic and network-free even though the
bundled aria2c binary is installed. Tests that exercise aria2c opt back in per test with
``monkeypatch.setenv("DAKP_ARIA2", "1")``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _force_urllib_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to the urllib download backend (aria2c off)."""
    monkeypatch.setenv("DAKP_ARIA2", "0")


@pytest.fixture(autouse=True)
def _hermetic_gpu_lock_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the CUDA flock at a per-test directory (``DAKP_GPU_LOCK_DIR`` wins in ``_gpu_lock_dir``).

    On a GPU host the production path activates for tests that fake only the model seam;
    without this override every such test would flock the REAL user cache lock and contend
    with live runs and other xdist workers. A per-test directory keeps workers independent.
    """
    monkeypatch.setenv("DAKP_GPU_LOCK_DIR", str(tmp_path_factory.mktemp("gpu-locks")))

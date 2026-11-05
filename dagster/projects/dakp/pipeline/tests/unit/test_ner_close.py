"""Explicit DiseaseNER teardown releases model and device-lock state."""

import fcntl
import os
from pathlib import Path

import pytest

from dakp_pipeline.ner.ner import _GPU_LOCK_HELD, DiseaseNER


def test_disease_ner_close_is_idempotent_and_releases_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "cuda-0.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _GPU_LOCK_HELD[lock_path] = fd
    ner = DiseaseNER()
    ner._model = object()
    ner._gpu_lock_fd = fd

    ner.close()
    assert ner._model is None
    assert ner._gpu_lock_fd is None
    assert lock_path not in _GPU_LOCK_HELD
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.close(fd)  # already closed by close()
    ner.close()  # no model, no fd: safe no-op

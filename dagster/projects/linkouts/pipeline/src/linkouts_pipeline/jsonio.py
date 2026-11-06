"""Small, deterministic JSON and content-hash helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import blake3


def hash_bytes(raw: bytes) -> str:
    """Return the monorepo's ``b3:<hex>`` content identifier."""
    return f"b3:{blake3.blake3(raw).hexdigest()}"


def hash_file(path: Path) -> str:
    """Hash one file without loading a potentially large KGX input into memory."""
    digest = blake3.blake3()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"b3:{digest.hexdigest()}"


def canonical_json(value: Any) -> bytes:
    """Canonical JSON used for manifest identity and cross-language fixtures."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def write_json_atomic(path: Path, value: Any) -> None:
    """Write a small contract file atomically, leaving no torn JSON after interruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sha256_file(path: Path) -> str:
    """Return a SHA-256 hex digest for interoperability checks, not artifact identity."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

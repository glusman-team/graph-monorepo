"""Release verification against the actual KGX pair and emitted RIG."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dakp_pipeline.selfloops import SCREENED_NODE_CURIES
from dakp_pipeline.tablassert import _command_prefix, stream_subprocess


class ReleaseVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseVerification:
    version: str
    nodes: str
    edges: str
    rig: str
    validate_kgx_stdout: str
    screened_hits: tuple[str, ...]
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iter_ndjson(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def verify_release(
    *,
    version: str,
    nodes_path: Path,
    edges_path: Path,
    rig_path: Path,
    runner: Callable[..., Any] = stream_subprocess,
) -> ReleaseVerification:
    for path in (nodes_path, edges_path, rig_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise ReleaseVerificationError(f"release output is missing or empty: {path}")

    command = [*_command_prefix(None), "validate-kgx", str(nodes_path), str(edges_path)]
    result = runner(command, cwd=nodes_path.parent)
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode:
        detail = (stdout + "\n" + stderr).strip()
        raise ReleaseVerificationError(
            f"tablassert validate-kgx exited {result.returncode}: {detail[-4000:]}"
        )

    node_ids = {node.get("id") for node in _iter_ndjson(nodes_path)}
    endpoint_ids = {
        endpoint
        for edge in _iter_ndjson(edges_path)
        for endpoint in (edge.get("subject"), edge.get("object"))
    }
    screened = sorted(SCREENED_NODE_CURIES & (node_ids | endpoint_ids))
    if screened:
        raise ReleaseVerificationError(
            f"published KGX contains {len(screened)} screened node CURIEs: {screened[:20]}"
        )

    rig_text = rig_path.read_text(encoding="utf-8")
    missing = [expected for expected in (version, "infores:icd10cm") if expected not in rig_text]
    if missing:
        raise ReleaseVerificationError(
            f"RIG {rig_path} is missing required release markers: {missing}"
        )
    return ReleaseVerification(
        version=version,
        nodes=str(nodes_path),
        edges=str(edges_path),
        rig=str(rig_path),
        validate_kgx_stdout=stdout[-4000:],
        screened_hits=(),
        ok=True,
    )


__all__ = ["ReleaseVerification", "ReleaseVerificationError", "verify_release"]

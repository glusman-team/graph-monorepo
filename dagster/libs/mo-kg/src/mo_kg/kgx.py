"""KGX ndjson writer + QC. Matches MultiomicsKG output conventions:

- nodes file: one JSON object per line (id, name, category, ...)
- edges file: one JSON object per line (subject, predicate, object, ...)
- RIG / published naming handled by the caller (code locations)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol


class KGSink(Protocol):
    """Minimal sink protocol so code locations can swap writers in tests."""

    def write_nodes(self, nodes: list[dict]) -> Path: ...

    def write_edges(self, edges: list[dict]) -> Path: ...


def write_kgx_nodes(path: Path, nodes: list[dict]) -> Path:
    """Write KGX node records as ndjson (one JSON object per line)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for node in nodes:
            fh.write(json.dumps(node, sort_keys=True) + "\n")
    return path


def write_kgx_edges(path: Path, edges: list[dict]) -> Path:
    """Write KGX edge records as ndjson (one JSON object per line)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for edge in edges:
            fh.write(json.dumps(edge, sort_keys=True) + "\n")
    return path


def read_kgx(path: Path) -> list[dict]:
    """Read a KGX ndjson file back (used by QC + tests)."""
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def qc_counts(nodes_path: Path, edges_path: Path) -> dict[str, int]:
    """Cheap QC: counts + dangling-edge check (every edge endpoint must be a node id)."""
    nodes = read_kgx(nodes_path)
    edges = read_kgx(edges_path)
    ids = {n["id"] for n in nodes}
    dangling = sum(1 for e in edges if e["subject"] not in ids or e["object"] not in ids)
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "dangling_edges": dangling,
    }


class KGXNdjsonSink:
    """Concrete KGSink writing nodes/edges under a base directory."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def write_nodes(self, nodes: list[dict]) -> Path:
        return write_kgx_nodes(self.base_dir / "kg.nodes.ndjson", nodes)

    def write_edges(self, edges: list[dict]) -> Path:
        return write_kgx_edges(self.base_dir / "kg.edges.ndjson", edges)

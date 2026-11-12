"""KGX ndjson reader + QC. Matches MultiomicsKG output conventions:

- nodes file: one JSON object per line (id, name, category, ...)
- edges file: one JSON object per line (subject, predicate, object, ...)
- RIG / published naming handled by the caller (code locations)
"""

from __future__ import annotations

import json
from pathlib import Path


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

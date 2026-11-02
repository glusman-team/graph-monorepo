"""Post-build self-loop removal for the drug-composition edge family.

A composition row pairs a PRODUCT name with an INGREDIENT name. Both resolve through the
fullmap, and two differently worded cells can land on one identifier (``Atorvastatin
Calcium`` the product and ``atorvastatin calcium`` the substance): the result is an edge
whose subject and object are the same CURIE, a tautology rather than composition. The
shaper already drops text-identical pairs; this module removes the collisions that only
become visible AFTER entity resolution.

It runs on the KGX pair Tablassert just wrote, before the DAKP contract gate and before
publication, and before Tablassert's study assertions (PR #229's ``self-loop-edges`` check)
are applied to the same files. Only the composition predicate is touched: the three
chemical-to-disease families keep their exact historical output.

Dropping an edge can strand its endpoint nodes (a generic product node whose only edge was
the self-loop), so nodes left with no incident edge are dropped too — the study's
isolated-node assertion requires it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dakp_pipeline.logging_setup import logger, stats

COMPOSITION_PREDICATE = "biolink:has_active_ingredient"


def _atomic_write(path: Path, lines: list[str]) -> None:
    """Write ``lines`` beside ``path`` and swap it in atomically (no torn NDJSON)."""
    tmp = path.with_name(f".{path.name}.selfloops.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.writelines(lines)
    os.replace(tmp, path)


def drop_self_loop_edges(
    nodes_path: Path, edges_path: Path, *, predicate: str = COMPOSITION_PREDICATE
) -> dict[str, int]:
    """Drop ``predicate`` edges whose subject equals their object, then orphaned nodes.

    Returns counters (all zero when nothing was dropped, in which case neither file is
    rewritten and their bytes are untouched).
    """
    kept_edges: list[str] = []
    dropped = 0
    with edges_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                kept_edges.append(line)
                continue
            try:
                edge = json.loads(line)
            except json.JSONDecodeError:
                # Not ours to repair: keep the line for the contract/study gates to report.
                kept_edges.append(line)
                continue
            if not isinstance(edge, dict):
                kept_edges.append(line)
                continue
            subject = edge.get("subject")
            object_ = edge.get("object")
            if (
                edge.get("predicate") == predicate
                and isinstance(subject, str)
                and isinstance(object_, str)
                and subject.strip()
                and subject == object_
            ):
                dropped += 1
                continue
            kept_edges.append(line)

    report = {"self_loop_edges_dropped": dropped, "orphan_nodes_dropped": 0}
    if not dropped:
        return report

    _atomic_write(edges_path, kept_edges)

    # Nodes with no remaining incident edge would fail the study's isolated-node assertion.
    referenced: set[str] = set()
    for line in kept_edges:
        if not line.strip():
            continue
        try:
            edge = json.loads(line)
        except json.JSONDecodeError:
            continue
        for key in ("subject", "object"):
            value = edge.get(key)
            if isinstance(value, str):
                referenced.add(value)

    kept_nodes: list[str] = []
    orphans = 0
    with nodes_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                kept_nodes.append(line)
                continue
            try:
                node = json.loads(line)
            except json.JSONDecodeError:
                kept_nodes.append(line)
                continue
            if not isinstance(node, dict):
                kept_nodes.append(line)
                continue
            if node.get("id") in referenced:
                kept_nodes.append(line)
            else:
                orphans += 1
    if orphans:
        _atomic_write(nodes_path, kept_nodes)
    report["orphan_nodes_dropped"] = orphans
    stats(logger, "drop_self_loop_edges", **report)
    return report


__all__ = ["COMPOSITION_PREDICATE", "drop_self_loop_edges"]

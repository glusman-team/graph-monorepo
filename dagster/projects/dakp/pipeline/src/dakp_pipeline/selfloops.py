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

from dakp_pipeline import icd10cm_curies, icd10cm_denylist
from dakp_pipeline.assertions.observed_uses import _pair_key
from dakp_pipeline.logging_setup import logger, stats

COMPOSITION_PREDICATE = "biolink:has_active_ingredient"

# Final resolved-node safety net. The table configs apply the subject CURIE screen before
# emission; composition also needs it on the object side, and refreshed label products can
# introduce a screened subject name after the assertion tables were shaped.
_WATER_CURIES = {
    "CHEBI:15377",
    "UMLS:C0359299",
    "UMLS:C0790233",
    "UMLS:C0991883",
    "UMLS:C1163679",
    "UMLS:C5981420",
    "UMLS:C1550678",
    "MESH:D008900",
}
SCREENED_NODE_CURIES = _WATER_CURIES | {
    curie for curie, _name, _key in icd10cm_curies.SCREENED_CURIES
}
_SCREENED_SUBJECT_KEYS = {row[0] for row in icd10cm_denylist.SCREENED_SUBJECTS}
_LABEL_BACKED_KEYS = {
    _pair_key(name) for name, _evidence in icd10cm_denylist.LABEL_BACKED_EXCEPTIONS
}


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


def drop_screened_edges(nodes_path: Path, edges_path: Path) -> dict[str, int]:
    """Drop screened resolved endpoints or subject names, then orphaned nodes.

    Screened CURIEs are denied on BOTH endpoints so poison/water ingredients cannot enter
    through composition. The wording screen remains subject-only and preserves the same
    label-backed exceptions as the table configs. This final post-resolution gate catches
    leaks regardless of which upstream table or fullmap path produced them.
    """
    node_names: dict[str, str] = {}
    with nodes_path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                node = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                node_names[node["id"]] = str(node.get("name") or "")

    denied_names = _SCREENED_SUBJECT_KEYS - _LABEL_BACKED_KEYS
    kept_edges: list[str] = []
    dropped = 0
    with edges_path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                edge = json.loads(line)
            except json.JSONDecodeError:
                kept_edges.append(line)
                continue
            if not isinstance(edge, dict):
                kept_edges.append(line)
                continue
            subject = edge.get("subject")
            object_ = edge.get("object")
            screened_curie = subject in SCREENED_NODE_CURIES or object_ in SCREENED_NODE_CURIES
            screened_subject = (
                isinstance(subject, str) and _pair_key(node_names.get(subject, "")) in denied_names
            )
            if screened_curie or screened_subject:
                dropped += 1
                continue
            kept_edges.append(line)

    report = {"screened_edges_dropped": dropped, "screened_orphan_nodes_dropped": 0}
    if not dropped:
        return report
    _atomic_write(edges_path, kept_edges)

    referenced: set[str] = set()
    for line in kept_edges:
        if not line.strip():
            continue
        try:
            edge = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(edge, dict):
            for key in ("subject", "object"):
                value = edge.get(key)
                if isinstance(value, str):
                    referenced.add(value)
    kept_nodes: list[str] = []
    orphans = 0
    with nodes_path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                node = json.loads(line)
            except json.JSONDecodeError:
                kept_nodes.append(line)
                continue
            if isinstance(node, dict) and node.get("id") not in referenced:
                orphans += 1
            else:
                kept_nodes.append(line)
    if orphans:
        _atomic_write(nodes_path, kept_nodes)
    report["screened_orphan_nodes_dropped"] = orphans
    stats(logger, "drop_screened_edges", **report)
    return report


__all__ = ["COMPOSITION_PREDICATE", "drop_screened_edges", "drop_self_loop_edges"]

"""Post-build provenance rescoping for drug-composition edges.

Tablassert stamps ``sources`` from a static per-table template
(``tablassert._TABLE_SOURCES[COMPOSITION_TABLE]``), so every composition edge leaves the
build naming BOTH ``infores:dailymed`` and ``infores:drugs-at-fda`` upstream -- even when
only one of them contributed to the pair (a Drugs@FDA-only product such as Demulen 1/50-28
names DailyMed although the current DailyMed release has zero Demulen SPLs; issue #59).
Tablassert 22.2.0 has no per-row provenance mechanism, so this pass performs the per-edge
rescoping DAKP runs right after the build:

1. the assertion TSV's ``upstream_resource_ids`` column carries the per-pair contributor
   truth (see ``assertions/active_ingredients.py``);
2. this pass rewrites each ``biolink:has_active_ingredient`` edge's ``sources`` to match:
   the ``infores:drugapprovals-kp`` primary entry lists exactly the contributing upstream
   CURIEs, and bare ``supporting_data_source`` entries survive only for contributors.

It runs after the self-loop/screen drops and approval reconciliation and before the DAKP
contract gate, so a bad rewrite fails the build instead of shipping. Idempotent: edges
already truthful are rewritten byte-identically and not replaced. Failures raise: a
composition edge with no matching assertion row means the graph and its assertion table
disagree, and shipping either side silently would hide a real bug.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import polars as pl

from dakp_pipeline.assertions import INFORES_DAILYMED, INFORES_DAKP, INFORES_DRUGS_AT_FDA
from dakp_pipeline.assertions.active_ingredients import _norm
from dakp_pipeline.logging_setup import logger, stats

COMPOSITION_PREDICATE = "biolink:has_active_ingredient"
_UPSTREAM_COLUMN = "upstream_resource_ids"
_UPSTREAM_SOURCES = frozenset({INFORES_DAILYMED, INFORES_DRUGS_AT_FDA})


def _record(line: str) -> dict[str, Any]:
    record = json.loads(line)
    if not isinstance(record, dict):
        msg = f"KGX edge record must be a JSON object, got {type(record).__name__}"
        raise RuntimeError(msg)
    return record


def contributors_by_pair(assertions_path: Path) -> dict[tuple[str, str], frozenset[str]]:
    """Union each pair's TSV ``upstream_resource_ids`` cell into one contributor set.

    Rows are keyed exactly like the shaper aggregates them (``_norm`` pair key). Rows
    folded by Tablassert's collision merge into one edge union here, so the merged edge
    cites every source that fed any of its rows.
    """
    frame = pl.read_csv(assertions_path, separator="\t")
    missing = {*(c for c in ("subject_text", "object_text") if c not in frame.columns)}
    if _UPSTREAM_COLUMN not in frame.columns:
        missing.add(_UPSTREAM_COLUMN)
    if missing:
        msg = f"assertion table {assertions_path.name} lacks provenance columns: {sorted(missing)}"
        raise RuntimeError(msg)
    contributors: dict[tuple[str, str], set[str]] = {}
    for subject_text, object_text, cell in zip(
        frame["subject_text"], frame["object_text"], frame[_UPSTREAM_COLUMN], strict=True
    ):
        sources = {part for part in str(cell or "").split("|") if part}
        unknown = sources - _UPSTREAM_SOURCES
        if unknown:
            msg = (
                f"assertion table {assertions_path.name} names non-FDA upstream(s) "
                f"{sorted(unknown)} for pair ({subject_text!r}, {object_text!r})"
            )
            raise RuntimeError(msg)
        key = (_norm(subject_text), _norm(object_text))
        contributors.setdefault(key, set()).update(sources)
    return {key: frozenset(sources) for key, sources in contributors.items()}


def _rescope_sources(sources: Any, contributors: frozenset[str]) -> list[dict[str, Any]]:
    """Return the entry list with truthful upstreams; raise when the template shape moved."""
    if not isinstance(sources, list) or not sources:
        msg = "composition edge carries no sources list"
        raise RuntimeError(msg)
    rescoped: list[dict[str, Any]] = []
    primary_seen = False
    for entry in sources:
        if not isinstance(entry, dict):
            msg = f"sources entry must be a JSON object, got {type(entry).__name__}"
            raise RuntimeError(msg)
        role = entry.get("resource_role")
        resource_id = entry.get("resource_id")
        if role == "primary_knowledge_source":
            primary_seen = True
            if resource_id != INFORES_DAKP:
                msg = (
                    f"composition primary knowledge source is {resource_id!r}, not {INFORES_DAKP!r}"
                )
                raise RuntimeError(msg)
            rescoped.append({**entry, "upstream_resource_ids": sorted(contributors)})
        elif resource_id in contributors:
            rescoped.append(entry)
    if not primary_seen:
        msg = f"composition edge carries no {INFORES_DAKP} primary_knowledge_source entry"
        raise RuntimeError(msg)
    return rescoped


def align_composition_provenance(edges_path: Path, assertions_path: Path) -> dict[str, int]:
    """Rescope every composition edge's ``sources`` to its contributing FDA sources.

    Streams the edges ndjson; composition edges are matched to the assertion TSV via
    ``original_subject``/``original_object`` (the shaper's normalized pair key) and
    rewritten to cite exactly the TSV contributors. Non-composition lines keep their
    exact bytes. Atomic and idempotent: the file is replaced only when at least one edge
    changed, and a second run over truthful edges reports zero rescopes.

    Returns ``{"edges_checked": ..., "edges_rescoped": ...}`` and logs the same stats.
    """
    contributors = contributors_by_pair(assertions_path)
    checked = 0
    rescoped = 0
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=edges_path.parent,
            prefix=f".{edges_path.name}.",
            delete=False,
        ) as out:
            temporary = Path(out.name)
            with edges_path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        out.write(line)
                        continue
                    edge = _record(line)
                    if edge.get("predicate") != COMPOSITION_PREDICATE:
                        out.write(line)
                        continue
                    checked += 1
                    subject_text = edge.get("original_subject")
                    object_text = edge.get("original_object")
                    if not isinstance(subject_text, str) or not isinstance(object_text, str):
                        msg = (
                            f"composition edge {edge.get('id')!r} lacks textual "
                            "original_subject/original_object to match against the assertion table"
                        )
                        raise RuntimeError(msg)
                    pair = contributors.get((_norm(subject_text), _norm(object_text)))
                    if pair is None:
                        msg = (
                            f"composition edge {edge.get('id')!r} "
                            f"({subject_text!r}, {object_text!r}) has no assertion-table row"
                        )
                        raise RuntimeError(msg)
                    if not pair:
                        msg = f"assertion row for pair ({subject_text!r}, {object_text!r}) names no FDA upstream"
                        raise RuntimeError(msg)
                    updated = _rescope_sources(edge.get("sources"), pair)
                    rewritten = json.dumps({**edge, "sources": updated}, ensure_ascii=False) + "\n"
                    if rewritten != line:
                        rescoped += 1
                        out.write(rewritten)
                    else:
                        out.write(line)
            out.flush()
            os.fsync(out.fileno())
        if rescoped:
            os.chmod(temporary, edges_path.stat().st_mode)
            os.replace(temporary, edges_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    report = {"edges_checked": checked, "edges_rescoped": rescoped}
    stats(
        logger,
        "composition_provenance_rescope",
        edges_checked=checked,
        edges_rescoped=rescoped,
    )
    return report


__all__ = ["align_composition_provenance", "contributors_by_pair"]

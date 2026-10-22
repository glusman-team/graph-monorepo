"""Run-context helpers: assemble the per-stage :class:`TaskContext`.

Under Dagster the run config arrives via a RunConfig resource (the dakp_dag code
location), not an orchestrator Variable; this module stays orchestrator-free and
builds contexts from plain mappings.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl

from dakp_pipeline.io.contracts import TaskContext
from dakp_pipeline.paths import Workdir

__all__ = ["build_context"]


def build_context(
    wd: Workdir, fixture_root: Path | str | None, params: Mapping[str, Any] | None
) -> TaskContext:
    """Assemble the per-stage :class:`TaskContext` from a workdir + fixtures + explicit run params.

    Loads the lexical disease map from ``fixture_root`` (when present) and merges it under
    ``params["disease_map"]`` ahead of the caller-supplied params.
    """
    fixture = Path(fixture_root) if fixture_root is not None else None
    disease_map = _load_disease_map(fixture) if fixture is not None else {}
    merged: dict[str, Any] = {"disease_map": disease_map}
    if params:
        merged.update(params)
    # The contraindication shaper builds its own deterministic offline NER backend from
    # ctx.fixture_root (single composite backend). A real production DiseaseNER (offline=False,
    # needs the NER dependencies) may be injected via params["ner"].
    return TaskContext(workdir=wd.root, fixture_root=fixture, params=merged)


def _load_disease_map(fixture_root: Path) -> dict[str, dict[str, str]]:
    """Load the lexical disease dictionary (fast baseline) from the ontology fixture."""
    path = fixture_root / "ontology" / "disease_map.tsv"
    if not path.exists():
        return {}
    frame = pl.read_csv(path, separator="\t")
    mapping: dict[str, dict[str, str]] = {}
    for rec in frame.iter_rows(named=True):
        text = str(rec.get("text", "") or "").strip()
        if not text:
            continue
        mapping[text] = {
            "curie": str(rec.get("curie", "") or ""),
            "name": str(rec.get("name", text) or text),
            "category": str(rec.get("category", "Disease") or "Disease"),
        }
    return mapping

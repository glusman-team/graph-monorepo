"""The inter-stage handoff: one published ArtifactRef list per stage.

Stage-to-stage data flows through the DAKP content-addressed store (BLAKE3 CAS + the
operation index); what crosses the dagster boundary is the small JSON list of
:class:`ArtifactRef` dicts a stage produced. Publishing that list per stage keeps the
monorepo contract (one published artifact per stage, lineage-only deps) and makes a narrow
single-output job possible: a downstream stage reads its upstreams' refs files and fails
loudly when one is absent, instead of forcing the producer into the same run.
"""

from __future__ import annotations

import json
from pathlib import Path

from dakp_dag.config import RunConfig


def refs_path(config: RunConfig, stage: str) -> Path:
    """The published refs file for one stage (``<refs_dir>/<stage>.refs.json``)."""
    return config.refs_dir / f"{stage}.refs.json"


def publish_refs(config: RunConfig, stage: str, refs: list) -> Path:
    """Write a stage's ArtifactRef list as its published handoff artifact.

    Uses the pipeline's own serializer (``io.refs.refs_to_dicts``) so the file is the exact
    shape the Go workers and :func:`load_refs` expect.
    """
    from dakp_pipeline.io.refs import refs_to_dicts

    path = refs_path(config, stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(refs_to_dicts(refs), indent=2) + "\n", encoding="utf-8")
    return path


def load_refs(config: RunConfig, stage: str, *, producer: str | None = None) -> list:
    """Read an upstream stage's published refs, failing loudly when it is not published.

    The single-file sentinel (a huge fan-out published as one refs-file ref, e.g. DailyMed's
    per-SPL member list) is resolved transparently by ``io.refs.refs_from_dicts``.
    """
    from dakp_pipeline.io.refs import refs_from_dicts

    path = refs_path(config, stage)
    if not path.exists():
        who = producer or stage
        msg = (
            f"upstream artifact missing: {path}. Publish it with 'dakp_build_{who}' "
            f"(or materialize the whole chain in one run: dagster asset materialize --select '*+{who}')"
        )
        raise FileNotFoundError(msg)
    return refs_from_dicts(json.loads(path.read_text(encoding="utf-8")))


def refs_metadata(refs: list) -> dict[str, object]:
    """The standard materialization metadata for a refs-producing stage."""
    rows = sum(ref.rows or 0 for ref in refs)
    return {"refs": len(refs), "rows": rows}


__all__ = ["load_refs", "publish_refs", "refs_metadata", "refs_path"]

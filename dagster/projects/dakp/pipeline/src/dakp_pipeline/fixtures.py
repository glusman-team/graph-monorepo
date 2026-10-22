"""Fixture plumbing shared by tests and the offline materialize smoke path."""

from __future__ import annotations

from pathlib import Path

from dakp_pipeline.io.contracts import ArtifactRef, TaskContext

__all__ = ["extract_fixture_sources"]


def extract_fixture_sources(ctx: TaskContext, fixture_root: Path) -> list[ArtifactRef]:
    """Run the pure-Python reference extractors over the committed fixture pipeline (tests).

    The same fixture plumbing the integration harness uses: the single DailyMed SPL fixture plus
    every FAERS ``.txt`` family under ``fixture_root/faers``.
    """
    from dakp_pipeline.extract import faers_ascii, spl_xml

    dailymed_raw = [ctx.fixture("dailymed/dailymed_spl.xml.gz")]
    faers_raw = [
        ctx.fixture(f"faers/{path.name}") for path in sorted((fixture_root / "faers").glob("*.txt"))
    ]
    return [*spl_xml.extract(dailymed_raw, ctx), *faers_ascii.extract(faers_raw, ctx)]

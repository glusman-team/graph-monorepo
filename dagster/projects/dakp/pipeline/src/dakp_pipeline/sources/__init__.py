"""Source fetchers: DailyMed, FAERS, Drugs@FDA, the EMA medicines registry, EMA EPAR
documents, EMA SmPCs, Health Canada Vigilance, and the CDC/NCHS ICD-10-CM Table of
Drugs and Chemicals (the non-therapeutic subject screen behind the observed-use denylist).

Each source module exposes a module-level :func:`fetch` (the default instance method) so tests
can ``monkeypatch.setattr(dailymed, "fetch", ...)`` and the pipeline can call
``dailymed.fetch(ctx)``. Fetchers always run their real (network) acquisition; offline test
runs monkeypatch the module-level ``fetch`` (or the stdlib download seam) to serve fixtures.
"""

from __future__ import annotations

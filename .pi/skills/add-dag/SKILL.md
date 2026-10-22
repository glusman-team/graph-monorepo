---
name: add-dag
description: Scaffold a new Dagster code location (KG DAG) in this monorepo, wired to shared libs and nx.
---

# Add a new DAG (code location)

1. From `dagster/`: `uvx create-dagster project projects/<name>` (accept the uv sync prompt),
   then set `.python-version` inheritance from workspace (3.12) if missing.
2. Wire shared libs in `projects/<name>/pyproject.toml`:
   - dependencies: `mo-core`, `mo-kg`, `mo-dagster`
   - `[tool.uv.sources]` with `{ path = "../../libs/<lib>", editable = true }`
   - `uv add` after editing, or `uv add --editable ../../libs/mo-core ...` directly
3. Add `projects/<name>/project.json` with cached `test` + `lint` targets
   (copy from `projects/dakp/project.json`, change `name` and `cwd`), and a
   `project.json` for any nested package (see `projects/dakp/pipeline/`).
4. Keep the heavy code in a dagster-free package (`projects/<name>/pipeline/`,
   its own venv, path-depended on by the code location) so it is testable
   without dagster; `src/<name>_dag/` holds only assets, jobs, and run config.
5. Create the asset graph mirroring pipeline stages (see `projects/dakp/src/dakp_dag/assets.py`
   for the shape: `acquire_<source>` -> `extract_<source>` (Go pipes worker or an
   in-process extractor, generated from one `SOURCES` table) -> `shape_*` (one per
   edge family in the pipeline's registry) -> `tablassert_configs` -> `kg_build` ->
   `kg_qc` / `legacy_tsv_export`).
   Copy its output contract too: one published artifact per stage (the stage's
   ArtifactRef list at `<published>/<dag>/refs/<stage>.refs.json`, the KGX pair
   under `<published>/<dag>/kg/`), stages
   declare lineage-only `deps` (never a value input, which would force the
   producer into the same run), and a narrow `<dag>_build_<stage>` job generated
   from `all_assets` so any single output can be triggered alone. Mirror the
   `tests/test_single_output.py` assertions for the new DAG.
6. `dg.toml` gains the project automatically when created via `create-dagster project`
   inside the workspace; otherwise append a `[[workspace.projects]]` entry.
7. `cd projects/<name> && uv sync && uv run pytest`; then `make dev` to see the new
   code location in the UI.

Rules: never import across `projects/*`; shared code goes to `libs/*`;
KGX ndjson (mo-kg) is the only KG sink.

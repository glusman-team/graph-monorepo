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
3. Add `projects/<name>/project.json` with a cached `test` target
   (copy from `projects/dakp/project.json`, change `name` and `cwd`).
4. Create the asset graph mirroring pipeline stages (see `projects/dakp/src/dakp_dag/assets.py`
   for the shape: acquisition -> extraction (Go pipes) -> ... -> kg_build writing KGX ndjson via mo-kg).
   Copy its output contract too: one published ndjson artifact per stage, stages
   declare lineage-only `deps` (never a value input, which would force the
   producer into the same run), and a narrow `<dag>_build_<stage>` job generated
   from `all_assets` so any single output can be triggered alone. Mirror the
   `tests/test_single_output.py` assertions for the new DAG.
5. `dg.toml` gains the project automatically when created via `create-dagster project`
   inside the workspace; otherwise append a `[[workspace.projects]]` entry.
6. `cd projects/<name> && uv sync && uv run pytest`; then `make dev` to see the new
   code location in the UI.

Rules: never import across `projects/*`; shared code goes to `libs/*`;
KGX ndjson (mo-kg) is the only KG sink.

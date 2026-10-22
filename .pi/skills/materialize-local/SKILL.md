---
name: materialize-local
description: Run the dakp pipeline locally through dg dev and verify KGX output.
---

# Materialize locally

1. Build the worker once: `cd dagster/projects/dakp/workers/go && go mod download && go build -o bin/dakp-extract ./cmd/dakp-extract`.
2. Sync the venvs: `cd dagster/projects/dakp && uv sync` (the code location) and
   `cd pipeline && uv sync` (the dagster-free package it path-depends on; add
   `--group ner` for the GPU mining backend).
3. Launch: `make dev` (repo root) - opens http://localhost:3000 with every
   code location loaded (`dg dev` reads `dagster/dg.toml`).
4. In the UI: select the `dakp` location -> `dakp_build` job -> Launch run for
   the whole DAG. For one output only, Launch run on `dakp_build_<stage>`
   (`dakp_build_acquire_dailymed`, `dakp_build_extract_faers`,
   `dakp_build_shape_approved_treats`, `dakp_build_tablassert_configs`,
   `dakp_build_kg_build`, `dakp_build_kg_qc`, `dakp_build_legacy_tsv_export`,
   ...), or Materialize a single asset node.
5. Verify output under `dagster/projects/dakp/published/dakp/` (or `$MO_PUBLISHED_ROOT`):
   `refs/<stage>.refs.json` per stage (the ArtifactRef handoff), and after a real
   Tablassert build `kg/kg.nodes.ndjson`, `kg/kg.edges.ndjson`,
   `kg/tablassert_handoff.json`, `kg/qc.json`. Without `$DAKP_FULLMAP` the
   handoff is deferred: `kg_build`/`kg_qc`/`legacy_tsv_export` report
   `deferred: true` metadata instead of failing, and no `kg/` files appear.
6. Cache check: re-run an `extract_*` or `shape_*` stage -> metadata should show
   `cache_hit: true` (the pipeline operation index in `$DAKP_WORKDIR` keys on the
   input BLAKE3 ids + the stage config fingerprint). `DAKP_FORCE=1` bypasses it.
7. Headless, from `dagster/projects/dakp` (no server needed):
   `uv run dagster asset materialize --select kg_build -m dakp_dag.definitions`
   for one output, `--select '*+<stage>'` to pull the whole upstream chain into
   the same run, or `uv run dagster job execute -j dakp_build_kg_build -m dakp_dag.definitions`
   for the narrow job. From `dagster/deployments/local`, `uv run dg check defs`
   loads every location (the CI-equivalent definition gate).
8. A narrow run that needs an artifact no stage has published yet fails with
   `upstream artifact missing: ... Publish it with 'dakp_build_<stage>'`. That is
   the intended contract: stages read published files, they do not rebuild them.
9. `make up` (process-compose daemon + webserver) is the deployed runtime, and
   its code-location load is cwd-coupled: `workspace.yaml` names each project's
   venv with a relative `executable_path`, which dagster resolves against the
   launcher cwd (pinned to `dagster/deployments/local` by
   `nix/modules/dagster.nix`). Trigger runs from `make dev` or the headless
   commands above when in doubt.

Troubleshooting: `extraction worker not built` means step 1 (the message names
the expected `$DAKP_EXTRACT_WORKER_BIN` path).
`upstream artifact missing` means the stage you triggered reads an artifact that
has not been published yet (step 8: run the producer, or `--select '*+<stage>'`).
Import errors mean step 2 (or libs not path-depped in pyproject).
A stage that needs GLiNER2 weights fails without them: run
`dakp_build_acquire_ner_models` first, or set `DAKP_NER_OFFLINE=1` for the
torch-free gazetteer backend.

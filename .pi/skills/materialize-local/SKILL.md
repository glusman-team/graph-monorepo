---
name: materialize-local
description: Run the dakp pipeline locally through dg dev and verify KGX output.
---

# Materialize locally

1. Build the worker once: `cd dagster/projects/dakp/workers/go && go get github.com/hupe1980/dagster-pipes-go github.com/zeebo/blake3 && go build -o bin/extractor ./extractor`.
2. Sync the project venv: `cd dagster/projects/dakp && uv sync`.
3. Launch: `make dev` (repo root) - opens http://localhost:3000 with every
   code location loaded (`dg dev` reads `dagster/dg.toml`).
4. In the UI: select the `dakp` location -> `dakp_build` job -> Launch run for
   the whole DAG. For one output only, Launch run on `dakp_build_<stage>`
   (`dakp_build_acquisition`, `dakp_build_extraction`, `dakp_build_ner`,
   `dakp_build_assertions`, `dakp_build_kg_build`), or Materialize a single
   asset node.
5. Verify output under `dagster/projects/dakp/published/dakp/` (or `$MO_PUBLISHED_ROOT`):
   `acquisition.ndjson`, `extraction.ndjson`, `ner.ndjson`, `assertions.ndjson`,
   `kg.nodes.ndjson`, `kg.edges.ndjson`.
6. Cache check: re-run `extraction` -> worker metadata should show `cache_hit: true`
   (BLAKE3 content key unchanged, worker version unchanged).
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

Troubleshooting: `FileNotFoundError: extractor worker not built` means step 1.
`upstream artifact missing` means the stage you triggered reads an artifact that
has not been published yet (step 8: run the producer, or `--select '*+<stage>'`).
Import errors mean step 2 (or libs not path-depped in pyproject).

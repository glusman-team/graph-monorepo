# graph-monorepo agent guide

Canonical agent context for this repo. pi STACKS this file with the global
`~/.pi/agent/AGENTS.md` (both load; this one never replaces it). Claude Code
reads the same content via the `CLAUDE.md -> AGENTS.md` symlink.

## What this repo is

Monorepo for ISB multiomics knowledge-graph pipelines:

- **Dagster deployment** under `dagster/` hosting several independent DAG
  code locations, each building its own knowledge graph, on shared Python
  libs. KGX ndjson is the only KG output format.
- **Nx** at the root as task graph + cached test runner (pytest via
  `project.json` targets, go test/build/lint via `@naxodev/gonx`).
- **Nix** provides every tool (single devshell); artifacts are managed by
  npm/uv/go themselves.

## Layout (where things go)

| Path | What | Rule |
|---|---|---|
| `dagster/projects/<name>/` | one DAG = one code location = one self-contained workspace | never import across projects |
| `dagster/projects/<name>/src/` | dagster defs: assets, jobs, run config | assets named for DAKP-style stages, one published artifact per stage |
| `dagster/projects/<name>/pipeline/` | that DAG's dagster-free python package (own venv, own nx project) | imports no dagster; assets call it in process |
| `dagster/projects/<name>/workers/<lang>/` | non-python workers, one dir per language (`go/` today, `scala/` later), own go.mod | no repo-wide go.work; never mix languages in one dir |
| `dagster/libs/mo-core` | pydantic domain models, canonical for Go codegen | imports no dagster |
| `dagster/libs/mo-kg` | KGX ndjson writer + QC (only sink) | imports no dagster |
| `dagster/libs/mo-dagster` | pipes helpers (`PipesSubprocessClient`) | the only lib importing dagster |
| `nix/modules/` | `devshell.nix` (all tools) + `dagster.nix` (runtime apps) | minimal flake.nix, /etc/nixos style |
| `.pi/` | project skills/extensions/prompts/settings | skills merge with global after trust |

## Conventions

- Formatting: ruff (python, line-length 100), golangci-lint+gofmt (go),
  nixfmt-tree (nix) - all wired into `prek.toml` two-stage hooks
  (pre-commit = fast auto-fix, pre-push = `nx affected -t test` + locks + flake check).
- Commits: conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Plain ASCII in all user-facing text. No em dashes, no unicode arrows/bullets.
- Env vars only (see `nix/modules/devshell.nix`); no hardcoded paths or keys.
  Values live in untracked `dagster/.env` / machine config, never in git.
  dakp run knobs are resolved in `projects/dakp/src/dakp_dag/config.py`:
  `MO_PUBLISHED_ROOT`, `DAKP_WORKDIR` (BLAKE3 CAS + op index = the cache),
  `DAKP_EXTRACT_WORKER_BIN`, `DAKP_FULLMAP` (absent => deferred Tablassert
  handoff, reported not failed), `DAKP_FORCE`, `DAKP_NER_OFFLINE`,
  `DAKP_THREADS`, `DAKP_MEMORY_BUDGET_GB`, `DAKP_QUARTER_LIMIT`,
  `DAKP_RELEASE_LIMIT`, `DAKP_PUBLICATION_VERSION`, `DAKP_SOURCE_MAX_AGE_DAYS`
  (freshness gate on dailymed/drugsfda/ema re-downloads, default 7),
  `DAKP_ICD10CM_URL` (override the fiscal-year-pinned ICD-10-CM ZIP).
- Every python package has a cached nx `test` target (`project.json`);
  run tests with `npx nx affected -t test`, never bare pytest loops.
- Stage-to-stage handoff is the published artifact under `$MO_PUBLISHED_ROOT`,
  never a dagster value input: assets declare `deps=[upstream]` (lineage only).
  Value inputs force the producer into the same run, which makes it impossible
  to build one output alone.
- Every stage gets a narrow job `dakp_build_<stage>` (generated from
  `all_assets` in `assets.py`) that builds exactly one output; `dakp_build`
  builds all of them.

## Commands

```sh
make dev            # dg dev, UI http://localhost:3000
make up             # process-compose: dagster-daemon + webserver (fixed ports)
make test           # nx run-many -t test (python + go, cached)
make lint / fmt / hooks
npx nx affected -t test          # fast loop: only what changed
cd dagster && uvx dg list project
go build -o bin/dakp-extract ./cmd/dakp-extract   # inside projects/dakp/workers/go
uv run dg check defs                    # inside dagster/deployments/local (loads every location)
# gated dakp pipeline tests (tablassert-dependent tests importorskip; CI runs ungated):
cd dagster/projects/dakp/pipeline && uv run --with 'tablassert[cli,log,qc]==22.2.0' pytest
# one output only, no other stage runs (inside dagster/projects/dakp):
uv run dagster job execute -j dakp_build_kg_build -m dakp_dag.definitions
uv run dagster asset materialize --select kg_build -m dakp_dag.definitions
uv run dagster asset materialize --select '*+kg_build' -m dakp_dag.definitions   # + whole chain
```

## Adding things

- **New KG/DAG**: `uvx create-dagster project projects/<name>` inside `dagster/`,
  wire mo-core/mo-kg/mo-dagster as editable path deps, add `project.json`
  (nx test), dg.toml gains the project automatically. Mirror the dakp output
  contract: published artifact per stage, lineage-only `deps`, one narrow
  `<dag>_build_<stage>` job per output (see `projects/dakp/src/dakp_dag/assets.py`).
- **New source**: one `Source` entry in `projects/dakp/src/dakp_dag/assets.py`
  (key, acquire function, `go:<stage>` or `py:<module>` extractor) plus the
  pipeline-side fetcher/extractor; the acquire/extract assets, their narrow
  jobs, and the published refs appear from the table.
- **New edge predicate**: one `EdgeFamily` in
  `projects/dakp/pipeline/src/dakp_pipeline/families.py` (table, predicate,
  knowledge level, agent type, NER profile, shaper class) plus a thin
  `_run_shape(...)` asset; Tablassert configs and QC read the registry.
- **Go worker**: new cmd under `workers/go/`, use `internal/pipesutil`
  (session + BLAKE3 cache reporting), bump `pipesutil.WorkerVersion` when
  behavior changes. gonx picks up targets automatically.
- **Shared python code**: goes in `libs/*` only if generic; else keep it in the
  project. Promote Go code to a shared module only when two DAGs need it.

## Releases and generated snapshots

- Cut a release with the `release-dakp` skill: explicit `DAKP_PUBLICATION_VERSION`, verify
  validate-kgx + qc.json + the poison/water screens, then the HF upload/card/prune/tag flow.
- `pipeline/src/dakp_pipeline/icd10cm_{denylist,curies}.py` are GENERATED snapshots (the
  ICD-10-CM poison screen at wording and identifier level). Their only writers are
  `pipeline/tests/eval/icd10cm_{denylist,curies}.py`; regenerate them when the ICD-10-CM
  fiscal-year ZIP or the fullmap changes (the curies derivation resolves through the
  production fullmap), then regenerate `pipeline/tests/fixtures/tables/*.yaml` goldens.
  Never hand-edit either snapshot.

## Migration status

`projects/dakp` carries the DAKP v1.25.0 port: `pipeline/` is the dagster-free
python package (io/CAS + op index, sources, extractors, NER, assertion shapers
behind the `families` registry, Tablassert handoff, legacy TSV export),
`workers/go/` holds the ported parsers plus the `cmd/dakp-extract` pipes worker,
and `src/dakp_dag/` is the asset graph (21 assets, `dakp_build` + one narrow job
per stage). Airflow is gone: no DAG/task/XCom code remains, and release-tag
versioning is replaced by explicit publication parameters. CTKP follows
(requires repo privacy decision).

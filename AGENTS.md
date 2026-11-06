# graph-monorepo agent guide

Canonical agent context for this repo. pi STACKS this file with the global
`~/.pi/agent/AGENTS.md` (both load; this one never replaces it). Claude Code
reads the same content via the `CLAUDE.md -> AGENTS.md` symlink.

## What this repo is

Monorepo for ISB multiomics knowledge-graph pipelines and their reader:

- **Dagster deployment** under `dagster/` hosting several independent DAG
  code locations (DAKP today, Linkouts orchestration), each on shared Python
  libs. KGX ndjson is the only KG output format.
- **Linkouts**: KGX in, human-readable edge pages out. `dagster/projects/linkouts/`
  holds the manually launched staging/publication/verification jobs and the hidden
  Go Pipes worker; `sites/linkouts/` holds the dynamic Phoenix reader; `sites/docs/`
  holds the static documentation site (Astro Starlight + Pagefind, GitHub Pages).
- **Nx** at the root as task graph + cached test runner (pytest via
  `project.json` targets, go test/build/lint via `@naxodev/gonx`).
- **Nix** provides every tool (single devshell); artifacts are managed by
  npm/uv/go themselves.

## Build hosts

- Run Go, BEAM, Dagster, Docker, integration, Nx, and test workloads on
  `wenceslaus`, capped at four CPUs. Local (workstation) work is editing,
  synchronization, and `gofmt` only.
- Use `/users/sgoetz/Code/linkouts-port-test` as the isolated remote checkout
  for Linkouts port verification. Do not disturb the dirty DAKP checkout at
  `/users/sgoetz/Code/graph-monorepo`.
- The user's remote `~/.envrc` may be copied for the port but never edited or
  printed. Real credentials never enter source, logs, or test reports.

## Layout (where things go)

| Path | What | Rule |
|---|---|---|
| `dagster/projects/<name>/` | one DAG = one code location = one self-contained workspace | never import across projects |
| `dagster/projects/<name>/src/` | dagster defs: assets, jobs, run config | assets named for DAKP-style stages, one published artifact per stage |
| `dagster/projects/<name>/pipeline/` | that DAG's dagster-free python package (own venv, own nx project) | imports no dagster; assets call it in process |
| `dagster/projects/<name>/workers/<lang>/` | non-python workers, one dir per language (`go/` today, `scala/` later), own go.mod | no repo-wide go.work; never mix languages in one dir; worker commands live under `cmd/`, operations under `internal/` |
| `dagster/libs/mo-core` | pydantic domain models, canonical for Go codegen | imports no dagster |
| `dagster/libs/mo-kg` | KGX ndjson writer + QC (only KG sink) | imports no dagster |
| `dagster/libs/mo-dagster` | pipes helpers (`PipesSubprocessClient`) | the only lib importing dagster |
| `sites/linkouts/` | Phoenix LiveView reader, display configs (`kgs/*.exs`), dictionaries, Fly packaging | dynamic app; never static |
| `sites/docs/` | Starlight content, search, Pages output | every user-facing doc lives here, not in scattered READMEs |
| `sites/linkouts/PRODUCT.md`, `DESIGN.md` | linkouts product + design context | keep accurate when scope changes |
| `nix/modules/` | `devshell.nix` (all tools) + `dagster.nix` (runtime apps) | minimal flake.nix, /etc/nixos style |
| `.pi/` | project skills/extensions/prompts/settings | skills merge with global after trust |

## Conventions

- Formatting: ruff (python, line-length 100), golangci-lint+gofmt (go),
  `mix format` (elixir), prettier (docs), nixfmt-tree (nix) - all wired into
  `prek.toml` two-stage hooks (pre-commit = fast auto-fix, pre-push =
  `nx affected` + locks + flake check).
- Commits: conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Plain ASCII in all user-facing text. No em dashes, no unicode arrows/bullets.
- Env vars only (see `nix/modules/devshell.nix`); no hardcoded paths or keys.
  Values live in untracked `dagster/.env` / machine config, never in git.
- dakp run knobs are resolved in `projects/dakp/src/dakp_dag/config.py`:
  `MO_PUBLISHED_ROOT`, `DAKP_WORKDIR` (BLAKE3 CAS + op index = the cache),
  `DAKP_EXTRACT_WORKER_BIN`, `DAKP_FULLMAP` (absent => deferred Tablassert
  handoff, reported not failed), `DAKP_FORCE`, `DAKP_NER_OFFLINE`,
  `DAKP_THREADS`, `DAKP_MEMORY_BUDGET_GB`, `DAKP_QUARTER_LIMIT`,
  `DAKP_RELEASE_LIMIT`, `DAKP_PUBLICATION_VERSION`, `DAKP_SOURCE_MAX_AGE_DAYS`
  (freshness gate on dailymed/drugsfda/ema re-downloads, default 7),
  `DAKP_ICD10CM_URL` (override the fiscal-year-pinned ICD-10-CM ZIP).
- linkouts run knobs: `MO_PUBLISHED_ROOT` (shared), `LINKOUTS_WORKDIR`,
  `LINKOUTS_WORKER_BIN`, `LINKOUTS_ENABLE_DESTRUCTIVE_JOBS` (opt-in definition
  load only; execution still demands an exact confirmation string), plus Cosmos
  connection/keys at runtime. Staging lives under the workdir; dictionary paths
  and KGX inputs are per-run typed launch config. Never put secrets in Pipes
  extras or metadata.
- Every python package has a cached nx `test` target (`project.json`);
  run tests with `npx nx affected -t test`, never bare pytest loops.
- Stage-to-stage handoff is the published artifact under `$MO_PUBLISHED_ROOT`,
  never a dagster value input: assets declare `deps=[upstream]` (lineage only).
  Value inputs force the producer into the same run, which makes it impossible
  to build one output alone.
- Every stage gets a narrow job (generated from `all_assets` in each
  `assets.py`) that builds exactly one output; `dakp_build` and
  `linkouts_upload` build their whole chains.

## Commands

```sh
make dev            # dg dev, UI http://localhost:3000
make up             # process-compose: dagster-daemon + webserver (fixed ports)
make test / lint / fmt / hooks / locks / check
make help           # component commands (dakp-*, linkouts-*, docs-*)
npx nx affected -t test          # fast loop: only what changed
go build -o bin/dakp-extract ./cmd/dakp-extract   # inside projects/dakp/workers/go
uv run dg check defs                    # inside dagster/deployments/local (loads every location)
# gated dakp pipeline tests (tablassert-dependent tests importorskip; CI runs ungated):
cd dagster/projects/dakp/pipeline && uv run --with 'tablassert[cli,log,qc]==22.2.0' pytest
# one output only, no other stage runs (inside dagster/projects/dakp):
uv run dagster job execute -j dakp_build_kg_build -m dakp_dag.definitions
uv run dagster asset materialize --select kg_build -m dakp_dag.definitions
uv run dagster asset materialize --select '*+kg_build' -m dakp_dag.definitions   # + whole chain
```

Linkouts loop (see `.pi/skills/linkouts-operations/SKILL.md` and
`.pi/skills/linkouts-display/SKILL.md`): build the worker
(`make linkouts-worker-build`), preview Phoenix against the public contract
corpus (`make linkouts-preview`), run the offline matrix (`make check`),
and launch manual jobs only with a disposable file store. Real Cosmos/Fly
operations are explicit later deployment steps, never part of verification.

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
- **Drug composition**: `has_active_ingredient` edges come from
  `assertions/active_ingredients.py` (DailyMed `spl_products` + Drugs@FDA products;
  substance AND moiety, no qualifiers, no CURIE columns). Product/ingredient pairs
  that resolve to one CURIE are removed post-build by `selfloops.py`; the same post-build
  gate removes screened poison/water CURIEs on either endpoint and screened subject names,
  then drops orphaned nodes. Tablassert's study assertions run on the filtered pair
  (build-kg runs without `--qc` for that reason).
- **New edge predicate**: one `EdgeFamily` in
  `projects/dakp/pipeline/src/dakp_pipeline/families.py` (table, predicate,
  knowledge level, agent type, NER profile, shaper class) plus a thin
  `_run_shape(...)` asset; Tablassert configs and QC read the registry.
- **Go worker**: new cmd under `workers/go/cmd/`, use `internal/pipesutil`
  (session + BLAKE3 cache reporting) where the project has it, bump the
  worker version constant when behavior changes. gonx picks up targets
  automatically. Workers stay hidden and argument-free: the Pipes payload is
  the only configuration surface.
- **Shared python code**: goes in `libs/*` only if generic; else keep it in the
  project. Promote Go code to a shared module only when two DAGs need it.
- **Linkouts display**: declarative `sites/linkouts/kgs/<kg>.exs`, validated by
  compiled checks, with real KGX evidence. See the display skill.
- **Documentation**: content under `sites/docs/src/content/docs/`; task-first
  navigation, typed front matter, generated-link check in
  `make docs-verify`. Do not resurrect ExDoc/Cobra/hand-written HTML.

## Releases and generated snapshots

- Cut a release with the `release-dakp` skill: explicit `DAKP_PUBLICATION_VERSION`, verify
  validate-kgx + qc.json + the poison/water screens, then the HF upload/card/prune/tag flow.
- The ICD-10-CM poison screen is DERIVED IN-DAG by the `derive_icd10cm_screen` and
  `derive_icd10cm_curies` assets (`pipeline/src/dakp_pipeline/icd10cm_screen.py` holds the
  logic): the wording denylist always derives from the acquired release; the identifier
  screen resolves through the fullmap and defers without one. The checked-in
  `icd10cm_{denylist,curies}.py` snapshots are the OFFLINE DEFAULT (CI, defs checks) and the
  regression baseline; `pipeline/tests/eval/icd10cm_{denylist,curies}.py` remain their only
  writers, sharing the screen module's functions. Never hand-edit the snapshots.
- Linkouts stored-document fixtures have exactly one canonical home:
  `dagster/projects/linkouts/contracts/docs.ndjson` (writer-side Go test
  regenerates; `sites/linkouts` reads those exact bytes). Never fork them.

## Migration status

`projects/dakp` carries the DAKP v1.25.0 port: `pipeline/` is the dagster-free
python package (io/CAS + op index, sources, extractors, NER, assertion shapers
behind the `families` registry, Tablassert handoff),
`workers/go/` holds the ported parsers plus the `cmd/dakp-extract` pipes worker,
and `src/dakp_dag/` is the asset graph (20 assets, `dakp_build` + one narrow job
per stage). Airflow is gone: no DAG/task/XCom code remains, and release-tag
versioning is replaced by explicit publication parameters. CTKP follows
(requires repo privacy decision).

`projects/linkouts` + `sites/linkouts` carry the Linkouts port: the Go CLI
surface is gone (replaced by the hidden `cmd/linkouts-worker` Pipes worker and
manually triggered Dagster jobs), ExDoc/Cobra/hand-written HTML docs are gone
(Starlight under `sites/docs/`), Phoenix stays dynamic, and deployment builds
one production Docker image from the monorepo root.

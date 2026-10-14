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
| `dagster/projects/<name>/src/` | dagster defs + that DAG's pipeline code | assets named for DAKP-style stages |
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
- Every python package has a cached nx `test` target (`project.json`);
  run tests with `npx nx affected -t test`, never bare pytest loops.

## Commands

```sh
make dev            # dg dev, UI http://localhost:3000
make up             # process-compose: dagster-daemon + webserver (fixed ports)
make test           # nx run-many -t test (python + go, cached)
make lint / fmt / hooks
npx nx affected -t test          # fast loop: only what changed
cd dagster && uvx dg list project
go build -o bin/extractor ./extractor   # inside projects/dakp/workers/go (after `go get`)
```

## Adding things

- **New KG/DAG**: `uvx create-dagster project projects/<name>` inside `dagster/`,
  wire mo-core/mo-kg/mo-dagster as editable path deps, add `project.json`
  (nx test), dg.toml gains the project automatically.
- **Go worker**: new cmd under `workers/go/`, use `internal/pipesutil`
  (session + BLAKE3 cache reporting), bump `pipesutil.WorkerVersion` when
  behavior changes. gonx picks up targets automatically.
- **Shared python code**: goes in `libs/*` only if generic; else keep it in the
  project. Promote Go code to a shared module only when two DAGs need it.

## Migration status

`projects/dakp` is the phase-1 scaffold of the DAKP port (assets mirror DAKP
stages; demo bodies). Phase 2 moves the real pipeline code from ISB/DAKP into
`projects/dakp/src/` + `workers/go/` and retires the Airflow DAG. CTKP follows
(requires repo privacy decision). Details in PLAN.md.

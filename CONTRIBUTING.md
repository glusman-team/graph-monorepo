# Contributing to graph-monorepo

Thank you for helping build the ISB multiomics knowledge graphs. Contributor
experience is treated as part of correctness: a new contributor should be able
to build, test, and understand this repo without guesswork, and this document
is the map.

## Prerequisites

- Nix with flakes enabled, and [direnv](https://direnv.net/) (recommended) or a
  willingness to enter `nix develop` manually.
- Git.
- Everything else (python + uv, go, node + nx, dagster, golangci-lint, prek)
  comes from the single nix devshell (`nix/modules/devshell.nix`). Nothing is
  installed globally, and no version manager is needed.

## One-time setup

```bash
git clone https://github.com/glusman-team/graph-monorepo.git
cd graph-monorepo
direnv allow                       # or: nix develop
make hooks                         # install prek git hooks (two stages)
cd dagster/projects/dakp/workers/go && go mod download   # first worker build
```

Confirm the loop works:

```bash
make test
```

## Daily edit/check loop

```bash
# fastest focused check: only tests affected by your change (cached)
npx nx affected -t test

# everything, before you push (the pre-push hook runs the same thing)
make test

# format + lint
make fmt
make lint

# local UI against your working tree
make dev                           # dg dev, http://localhost:3000
```

## Task runner

The `Makefile` is intentionally small and mirrors the underlying commands:

| Target | Runs |
| --- | --- |
| `make dev` | `dg dev`: all code locations, Dagster UI on http://localhost:3000 |
| `make up` | process-compose: dagster-daemon + webserver (fixed ports) |
| `make test` | `nx run-many -t test`: every cached pytest + go test target |
| `make lint` | `nx run-many -t lint`: ruff + golangci-lint via nx |
| `make fmt` | `nix fmt` (treefmt with nixfmt-tree) |
| `make hooks` | `prek install` (two-stage git hooks, see below) |

## Project layout

```text
dagster/projects/<name>/   one DAG = one code location = one self-contained workspace
  src/                     dagster defs: assets, jobs, run config
  pipeline/                the dagster-free python package the assets call (own venv + tests)
  workers/<lang>/          non-python workers, one dir per language (go/ today)
  project.json             cached nx test target
dagster/libs/mo-core/      pydantic domain models, canonical for Go codegen
dagster/libs/mo-kg/        KGX ndjson writer + QC (the only KG sink)
dagster/libs/mo-dagster/   pipes helpers (the only lib importing dagster)
dagster/deployments/local/ dg / dagster-webserver environment for the workspace
nix/modules/               devshell.nix (all tools), dagster.nix (runtime apps)
.github/                   CI workflow, issue templates, PR template
```

Rules that protect this layout:

- Never import across projects. Shared python code goes in `dagster/libs/*`
  only when it is generic; promoting Go code to a shared module waits until two
  DAGs need it.
- `mo-dagster` is the only lib that imports dagster; `pipeline/` packages never
  do. Assets call them in process.
- KGX ndjson is the only KG output format.
- Stages hand off through published artifacts under `$MO_PUBLISHED_ROOT` with
  lineage-only `deps` (no value inputs), and each stage gets a narrow
  `<dag>_build_<stage>` job so one output can be built alone. See
  `dagster/projects/dakp/src/dakp_dag/assets.py` for the pattern.
- Configuration is environment variables only (see
  `dagster/projects/dakp/src/dakp_dag/config.py`). Values live in untracked
  `dagster/.env`, never in git.
- User-facing text (docs, commits, comments meant for readers) is plain ASCII.

## Quality gates

Run the full local gate before opening a PR:

```bash
prek run --all-files
make test
```

The hooks are split across two stages (`prek.toml`) so committing stays cheap:

- On every **commit**, fast and auto-fixing: ruff check + format, golangci-lint
  (with gofmt formatters), treefmt for nix files.
- On every **push**, the whole-repo gates: `scripts/check-locks.sh` (pyproject
  edits must be re-locked per package), `npx nx affected -t test`, and
  `nix flake check --no-build` when nix files change.

CI (`.github/workflows/ci.yml`) runs the same gates remotely: python
(lock check + sync + pytest per package, then `uv run dg check defs` so every
code location must load), go (build, test, golangci-lint per workers module),
and `nix flake check`.

Formatting baseline: ruff (python, line-length 100), golangci-lint + gofmt
(go), nixfmt-tree (nix). Treat `make lint` / `make fmt` as the stable
interface rather than memorizing rule codes.

## Running subsets

```bash
# one nx project (dakp, dakp-pipeline, mo-core, mo-kg, mo-dagster)
npx nx test dakp-pipeline

# one python test inside a package's venv
cd dagster/libs/mo-kg && uv run pytest tests/test_writer.py -k ncit

# pipeline tests that need a real Tablassert (skipped by default; CI runs ungated)
cd dagster/projects/dakp/pipeline
uv run --with 'tablassert[cli,log,qc]==22.2.0' pytest

# one pipeline output only, no other stage runs
cd dagster/projects/dakp
uv run dagster job execute -j dakp_build_kg_build -m dakp_dag.definitions

# every code location still loads (what CI enforces)
cd dagster/deployments/local && uv run dg check defs
```

## Adding things

- **New KG/DAG:** `uvx create-dagster project projects/<name>` inside
  `dagster/`, wire `mo-core` / `mo-kg` / `mo-dagster` as editable path deps,
  add a `project.json` (nx test target), and mirror the dakp output contract
  (published artifact per stage, lineage-only `deps`, one narrow job per
  output).
- **New source:** one `Source` entry in
  `dagster/projects/dakp/src/dakp_dag/assets.py` plus the pipeline-side
  fetcher/extractor; the assets, narrow jobs, and published refs appear from
  the table.
- **New edge predicate:** one `EdgeFamily` in
  `dagster/projects/dakp/pipeline/src/dakp_pipeline/families.py` plus a thin
  shaper asset; Tablassert configs and QC read the registry.
- **New Go worker:** a new cmd under `workers/go/`, using
  `internal/pipesutil` (pipes session + BLAKE3 cache reporting); bump
  `pipesutil.WorkerVersion` when behavior changes.

## Pull requests

- Use conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, ...).
- Keep PRs focused on one concern; a change that breaks the build or the suite
  is not ready.
- Include tests, or explain why none are needed. Logic changes get regression
  tests; workflows get integration tests; real failure paths get negative
  cases.
- Run `prek run --all-files` and `make test` and include the results in the PR
  description.
- Never weaken a test, inflate a timeout, or skip a check to hide a failure.

## Getting help

- **Usage questions and bugs:** open an issue with the bug-report or
  feature-request template at
  [github.com/glusman-team/graph-monorepo/issues](https://github.com/glusman-team/graph-monorepo/issues),
  with reproduction steps, the exact command, and its full output.
- **Documentation:** the published site at
  [glusman-team.github.io/graph-monorepo](https://glusman-team.github.io/graph-monorepo/)
  mirrors `docs/`. `AGENTS.md` is the canonical agent-facing context and stays
  in sync with this file.
- **Security problems:** do not open a public issue. Email the maintainers
  listed in the [Code of Conduct](CODE_OF_CONDUCT.md) so the report stays
  private until a fix ships.
- **Conduct:** all contributors are expected to follow the
  [Code of Conduct](CODE_OF_CONDUCT.md); violations go to the enforcement
  contacts named there.

## License

By contributing to graph-monorepo, you agree that your contributions will be
licensed under the [Apache License 2.0](LICENSE).

# graph-monorepo

Monorepo for ISB multiomics knowledge-graph pipelines: a Dagster deployment
hosting several independent DAG code locations (each builds its own knowledge
graph as KGX ndjson), shared Python libs, Go extraction workers, Nx as the
task graph + cached test runner, and a single nix devshell providing every tool.

Agent context lives in [AGENTS.md](AGENTS.md) (pi + Claude Code both read it).
Long-term design + review history in [PLAN.md](PLAN.md).

## Layout

```
dagster/
  projects/<name>/    one DAG = one code location = one self-contained workspace
    src/              dagster defs + pipeline code
    workers/<lang>/   go workers today (own go.mod), scala later (mill)
    project.json      cached nx test target
  libs/
    mo-core/          pydantic models, canonical for Go codegen (no dagster dep)
    mo-kg/            KGX ndjson writer + QC (the only sink)
    mo-dagster/       pipes helpers (only lib importing dagster)
nix/modules/          devshell.nix (all tools), dagster.nix (runtime apps)
```

## Quickstart

```sh
direnv allow          # or: nix develop
make hooks            # install prek git hooks (two-stage)
make dev              # dagster dg dev -> http://localhost:3000
make test             # nx run-many -t test (python + go, cached)
```

First-time worker build (fetches deps, then cached):

```sh
cd dagster/projects/dakp/workers/go
go get github.com/hupe1980/dagster-pipes-go github.com/zeebo/blake3
go build -o bin/extractor ./extractor && go test ./...
```

Then `make dev`, pick the `dakp` location, launch `dakp_build` for the whole
DAG (or a `dakp_build_<stage>` job for one output), and find the per-stage
artifacts in `dagster/projects/dakp/published/dakp/`.

## Build one output

Each stage publishes its own artifact and each stage gets a narrow job, so a
single output can be rebuilt without running any of the others. From
`dagster/projects/dakp` (stages: acquisition, extraction, ner, assertions,
kg_build):

```sh
uv run dagster job execute -j dakp_build_kg_build -m dakp_dag.definitions    # only kg_build
uv run dagster asset materialize --select kg_build -m dakp_dag.definitions   # only kg_build
uv run dagster asset materialize --select '*+kg_build' -m dakp_dag.definitions  # kg_build + whole chain
```

In the UI it is Launch run on `dakp_build_<stage>`, or Materialize on a single
asset node. Stages hand off through files under `$MO_PUBLISHED_ROOT` and
declare lineage-only `deps` (no value inputs), so a narrow run reads upstream
artifacts instead of rebuilding them and fails with `upstream artifact
missing` when one has not been published yet. `dakp_build` still runs the
whole DAG, and `tests/test_single_output.py` pins both properties.

## Commands

| Command | Does |
|---|---|
| `make dev` | dg dev, all code locations, UI on :3000 |
| `make up` | process-compose: dagster-daemon + webserver (same ports) |
| `make test` | every cached test (pytest + go test) |
| `npx nx affected -t test` | only tests affected by the current change (fast loop) |
| `make lint` / `make fmt` | golangci-lint + ruff via nx; nixfmt-tree |
| `prek run --all-files` | the same hooks CI runs |

## Add a new KG/DAG

See `.pi/skills/add-dag/SKILL.md` (or ask an agent to): `uvx create-dagster
project projects/<name>`, wire libs, add `project.json`. Never import across
projects; shared code goes in `libs/*`. KGX ndjson is the only sink.

## Add a Go worker

See `.pi/skills/add-worker-go/SKILL.md`: new cmd under `workers/go/`, pipes +
BLAKE3 cache via `internal/pipesutil`, bump `WorkerVersion` on behavior change.

## CI

`.github/workflows/ci.yml`: python matrix (uv lock check + sync + pytest per
package), go (build/test/golangci-lint per workers module), `nix flake check`.
Issue templates in `.github/ISSUE_TEMPLATE/`.

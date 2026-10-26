# graph-monorepo

Docs: https://glusman-team.github.io/graph-monorepo/ (first entry: DAKP).

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
    src/              dagster defs (assets, jobs, run config)
    pipeline/         the dagster-free python package the assets call (own venv + tests)
    workers/<lang>/   go workers today (own go.mod), scala later (mill)
    project.json      cached nx test target (pipeline/ and workers/ have their own)
  libs/
    mo-core/          pydantic models, canonical for Go codegen (no dagster dep)
    mo-kg/            KGX ndjson writer + QC (the only sink)
    mo-dagster/       pipes helpers (only lib importing dagster)
  deployments/local/  dg / dagster-webserver environment for the whole workspace
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
go mod download
go build -o bin/dakp-extract ./cmd/dakp-extract && go test ./...
```

Then `make dev`, pick the `dakp` location, launch `dakp_build` for the whole
DAG (or a `dakp_build_<stage>` job for one output), and find the per-stage
artifacts in `dagster/projects/dakp/published/dakp/` (or wherever
`$MO_PUBLISHED_ROOT` points).

## DAKP sources

| Source | infores | Role |
|---|---|---|
| DailyMed SPL labels | `infores:dailymed` | indications, contraindications, warnings (assertion text) |
| FAERS | `infores:faers` | observed-use (`biolink:applied_to_treat`) reports |
| Drugs@FDA | (none; folded into approval reconciliation) | FDA approval backing for approved-treats edges |
| EMA medicines registry, EPAR documents, SmPCs | `infores:ema` (EPAR evidence also as `infores:epar` upstream id) | EU authorisations and label text |
| Health Canada Canada Vigilance | `infores:canada-vigilance` | observed-use reports |
| CDC/NCHS ICD-10-CM Table of Drugs and Chemicals (FY2027) | `infores:icd10cm` | poison screen: supporting data source on observed-use edges, contributes no assertion text |

The ICD-10-CM screen (added in DAKP 1.25.1) keeps poisons out of the graph two ways;
both screens are derived inside the DAG at build time (`derive_icd10cm_screen` +
`derive_icd10cm_curies` assets), not baked into the code:

- Wording: observed-use rows whose subject text names a substance the Table lists with no
  Adverse-effect code (T36-T65, `icd10cm_denylist.py`) are dropped at load by one Tablassert
  `antijoin` filter.
- Identifier: every table's subject resolution excludes the fullmap CURIEs whose own name is such a
  substance (`icd10cm_curies.py`), so a reporter spelling that resolves to a poison (`LEVOXINE` ->
  hydrazine) or a label ingredient (`TURPENTINE`) cannot surface as a node.

Substances that regulator label text names as medicines are exempt (lithium, zinc, permethrin).
Water-as-substance identifiers and wordings are excluded separately. GHB/GBL (sodium oxybate) are
allowed: it is an approved medicine. Regenerate both snapshots with the scripts in
`dagster/projects/dakp/pipeline/tests/eval/` when the ICD-10-CM release or fullmap changes.

## Build one output

Each stage publishes its own artifact and each stage gets a narrow job, so a
single output can be rebuilt without running any of the others. From
`dagster/projects/dakp` (stages: `acquire_<source>` for dailymed, faers,
drugsfda, ema, ema_smpc, canada_vigilance, icd10cm and `acquire_ner_models`;
`extract_<source>`; `shape_approved_treats`, `shape_faers_uses`,
`shape_contraindications`; `ner_training_export`; `tablassert_configs`;
`kg_build`, `kg_qc`, `legacy_tsv_export`):

```sh
uv run dagster job execute -j dakp_build_kg_build -m dakp_dag.definitions    # only kg_build
uv run dagster asset materialize --select kg_build -m dakp_dag.definitions   # only kg_build
uv run dagster asset materialize --select '*+kg_build' -m dakp_dag.definitions  # kg_build + whole chain
```

In the UI it is Launch run on `dakp_build_<stage>`, or Materialize on a single
asset node. Stages hand off through files under `$MO_PUBLISHED_ROOT` (each
stage's ArtifactRef list at `dakp/refs/<stage>.refs.json`, the KGX pair at
`dakp/kg/`) and declare lineage-only `deps` (no value inputs), so a narrow run
reads upstream artifacts instead of rebuilding them and fails with `upstream
artifact missing` when one has not been published yet. `dakp_build` still runs
the whole DAG, and `tests/test_single_output.py` pins both properties.

Run knobs are environment variables read at materialization time
(`src/dakp_dag/config.py`): `MO_PUBLISHED_ROOT`, `DAKP_WORKDIR` (BLAKE3 CAS +
op index, the cache), `DAKP_EXTRACT_WORKER_BIN`, `DAKP_FULLMAP` (without it the
Tablassert handoff is deferred and reported, not failed), `DAKP_FORCE` (bypass
the op-index skip), `DAKP_NER_OFFLINE=1` (gazetteer-only NER, torch-free),
`DAKP_THREADS`, `DAKP_MEMORY_BUDGET_GB`, `DAKP_QUARTER_LIMIT`,
`DAKP_RELEASE_LIMIT`, `DAKP_PUBLICATION_VERSION`.

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

`.github/workflows/ci.yml`: python (uv lock check + sync + pytest per package,
including packages nested inside a project, then `dg check defs` so every code
location must load), go (build/test/golangci-lint per workers module),
`nix flake check`. Issue templates in `.github/ISSUE_TEMPLATE/`.

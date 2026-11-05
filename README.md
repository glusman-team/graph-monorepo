# graph-monorepo

[![CI](https://github.com/glusman-team/graph-monorepo/actions/workflows/ci.yml/badge.svg)](https://github.com/glusman-team/graph-monorepo/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/github/deployments/glusman-team/graph-monorepo/github-pages?label=docs)](https://glusman-team.github.io/graph-monorepo/)
[![HF likes](https://img.shields.io/badge/dynamic/json?label=HF%20likes&query=likes&url=https%3A%2F%2Fhuggingface.co%2Fapi%2Fdatasets%2FSkyeAv%2Fdrug-approvals-kp&color=yellow)](https://huggingface.co/datasets/SkyeAv/drug-approvals-kp)
[![GitHub stars](https://img.shields.io/github/stars/glusman-team/graph-monorepo?label=GitHub%20stars)](https://github.com/glusman-team/graph-monorepo)

**[Start with the documentation homepage](https://glusman-team.github.io/graph-monorepo/)**. It routes readers by task: use DAKP data, browse Linkouts, build a KG, publish a serving index, configure a display, operate a store, or contribute.

## Components

| Component | Source | Purpose |
| --- | --- | --- |
| DAKP | `dagster/projects/dakp/` | Build and validate the Drug Approvals Knowledge Provider as KGX NDJSON. |
| Linkouts orchestration | `dagster/projects/linkouts/` | Manually launch typed staging, publication, verification, inspection, and dictionary jobs. |
| Linkouts worker | `dagster/projects/linkouts/workers/go/` | Hidden argument-free Go Pipes worker; preserves the tested ingestion implementation. |
| Linkouts web | `sites/linkouts/` | Dynamic Phoenix LiveView edge pages, version history, random pools, and declarative display configs. |
| Documentation | `sites/docs/` | Static Astro Starlight site with Pagefind, deployed to GitHub Pages. |
| Shared Python | `dagster/libs/` | Generic domain models, KGX writing/QC, and Dagster helpers. |

DAKP-style pipelines publish **KGX NDJSON only**. Linkouts consumes KGX and writes a compressed serving index for Phoenix; that envelope is not another KG publication format.

Every Dagster project is an independent code location with its own uv environment. `src/<name>_dag/` holds Dagster definitions, `pipeline/` holds Dagster-free logic, and `workers/<lang>/` holds language-specific workers. Never import implementation across independent projects. Go modules stay independent; there is no root `go.work`.

## Documentation

```sh
npm ci
npm run docs:dev       # local Starlight site
npm run docs:verify    # typed content + production build + generated-link checks
```

Pages publishes `sites/docs/dist` after CI succeeds on `main`. The old hand-written HTML site and generated Cobra/ExDoc machinery are removed. Phoenix remains dynamic.

## Local development

Prerequisites: Nix with flakes, and optionally direnv.

```sh
direnv allow           # or nix develop
make hooks
npm ci
make test
make lint
```

The devshell pins Node, Go 1.26.10, Elixir 1.19/OTP 28, uv, and repository tools. Commands are limited to four parallel tasks/CPUs for shared build hosts. Run heavy Linkouts builds and tests on `wenceslaus`, not an interactive workstation.

```sh
make dev               # Dagster UI on http://localhost:3000
make up                # daemon + webserver
make check             # offline aggregate verification
make locks             # recursive uv/npm/Go/Mix lock checks
make go-test-race      # both independent Go modules
make docs-test
make linkouts-test
make linkouts-contract-test
make linkouts-web-build
make linkouts-check-deploy
```

Use `make help` for component commands. `.envrc.example` documents public local settings; the committed `.envrc` contains only `use flake`. Keep real credentials in an untracked parent environment. Never put secrets in Dagster configuration, Pipes extras, asset metadata, logs, or committed files.

## Linkouts manual workflows

Build the hidden worker and start Dagster:

```sh
make linkouts-worker-build
cd dagster/projects/linkouts && uv sync --group dev
make dev               # from the repository root
```

Launch `linkouts_upload` manually for a complete release: `stage_release` -> `push_staged_release` -> `status_report` -> `probe_release`. Narrow jobs materialize one output and read published artifact references without rebuilding upstream stages.

Staging is content-addressed and publication is resumable. Filtered or dry-run staging cannot enter normal publication. Provisioning (`linkouts_init_store`) is separate. Destructive definitions are absent unless `LINKOUTS_ENABLE_DESTRUCTIVE_JOBS=1` is set at code-location load, and execution still requires exact target confirmation. No schedule or sensor is installed.

See the [operation reference](https://glusman-team.github.io/graph-monorepo/linkouts/operations/reference/) and [upload guide](https://glusman-team.github.io/graph-monorepo/linkouts/operations/upload/).

## Phoenix preview

Run against the public canonical contract with no cloud credentials:

```sh
cd sites/linkouts
mix setup
COSMOS_BACKEND=file \
COSMOS_DOCS=../../dagster/projects/linkouts/contracts/docs.ndjson \
  mix phx.server
```

Open `http://localhost:4000`. The canonical corpus also has a Go byte-regeneration test and an Elixir decoding suite. The Fly preview uses the same public fixture and never receives production Cosmos credentials.

## Deployment

The production Docker image builds from the monorepo root with a narrow `.dockerignore` allowlist, digest-pinned bases, and a non-root runtime user:

```sh
docker build -f sites/linkouts/Dockerfile -t linkouts .
LINKOUTS_IMAGE=linkouts sites/linkouts/scripts/smoke_preview.sh
```

Real Cosmos and Fly operations are explicit later deployment steps, not test targets. Production Fly deploys require successful CI for the selected commit and the authorized environment/token. Preview deployment is manual and remains public/credential-free apart from its own generated Phoenix signing key.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and `AGENTS.md`. CI includes scoped fan-out, contract regeneration/readback, race tests, strict Phoenix checks, Dialyzer, Docker smoke, Actionlint, ShellCheck, Hadolint, secret scanning, and blocking Go/Hex/npm vulnerability audits. No vulnerability gate is permanently informational.

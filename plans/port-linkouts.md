# Port Linkouts into Graph Monorepo

## Context

Port `https://github.com/glusman-team/linkouts` at source revision `6412dd01748a6132cd44c33a28aa1747e1414a59` into `graph-monorepo` without weakening either repository's quality gates. Consolidate the Phoenix edge-linkout application, Go ingestion/storage pipeline, KG configuration, contracts, tests, deployment, documentation, and agent tooling. Replace Cobra/ExDoc documentation with a general static documentation site, and replace the public Go CLI workflow with a manually triggered Dagster pipeline that can later be scheduled or triggered automatically.

The source contains three major components:

- `web/`: Elixir/Phoenix dynamic application, deployment configuration, application tests, and contract tests.
- `cli/`: Go/Cobra commands and internal storage, codec, Cosmos DB, ClickHouse/chDB, pipeline, and rate-limit packages.
- `docs/` and `kgs/`: ExDoc/Cobra documentation, ADRs, operator guides, and KG display configurations.

The destination uses independent Dagster code locations under `dagster/projects/<name>`, dagster-free pipeline packages, language-specific workers, Nx affected targets, Nix, and `prek` fast pre-commit plus comprehensive pre-push hooks.

## Confirmed layout and ownership

- `dagster/projects/linkouts/`: manually triggered Dagster definitions, run configuration, tests, and Dagster-free orchestration code.
- `dagster/projects/linkouts/workers/go/`: the complete editable Go source, internal packages, fixtures, and a hidden single-purpose Dagster Pipes worker; no Cobra user-facing CLI.
- `sites/linkouts/`: the complete editable Phoenix application and its runtime KG configuration/deployment assets.
- `sites/docs/`: general monorepo documentation using a researched static site generator, with a task-oriented homepage that routes DAKP, Linkouts, contributor, operator, and architecture readers before detailed navigation.
- Root agent/CI/tooling files: merge source and destination conventions rather than overwrite either.

This is a reviewed source snapshot, not a subtree or external dependency. All implementation source and tests become directly editable in this repository; no Linkouts Git history import is required.

| Source | Destination |
| --- | --- |
| `web/lib/**`, `web/assets/**`, `web/config/**`, `web/priv/**`, `web/test/**`, Mix manifests | `sites/linkouts/` with the same application structure |
| `kgs/**` | `sites/linkouts/kgs/` |
| `cli/internal/**`, fuzz corpora, and relevant command tests | `dagster/projects/linkouts/workers/go/internal/**` and worker/operation tests |
| `cli/testdata/**` | Linkouts project test fixtures; generated wire fixtures move to the shared contract corpus |
| `cli/cmd/linkouts/**` | Replaced by internal operations plus `cmd/linkouts-worker`; no Cobra port |
| `scripts/extract_fixtures.py` | `dagster/projects/linkouts/scripts/` with explicit arguments |
| `docs/pages/**`, `docs/adr/**`, `docs/README.md` | Rewritten Starlight content under `sites/docs/src/content/docs/` |
| `Dockerfile`, `fly*.toml` | `sites/linkouts/` with monorepo-safe build paths |
| `PRODUCT.md`, `DESIGN.md`, `.impeccable/**` | `sites/linkouts/` design/agent context |
| Root README/security/environment/ignore/Gitleaks and `.github/**` | Merge into destination equivalents; preserve both repositories' applicable rules |
| `docs/cli/**`, ExDoc project, `repair-pool-index` | Intentionally removed as generated, superseded, or one-off code documented below |

## Approach

1. Inventory both repositories' behavior, tests, CI, hooks, deployment, documentation, and agent tooling.
2. Establish the Linkouts component boundaries and port runtime/test fixtures with no behavior change.
3. Replace Cobra command dispatch with explicit Dagster jobs/assets and a narrow internal execution boundary. Extract command-embedded probe/status/get/rig/dictionary logic into tested internal Go packages, retain the existing pipeline/storage/engine/codec implementation, and delete Cobra plus generated CLI docs. Keep destructive and administrative operations separate from the normal upload workflow.
4. Move the Phoenix application under `sites/linkouts/` and preserve its dynamic behavior and Fly deployment contract.
5. Build a general static documentation site under `sites/docs/` with Astro Starlight; migrate Linkouts guides/ADRs and existing monorepo/DAKP documentation, and remove generated Cobra/ExDoc documentation machinery. Lead with a task-oriented splash homepage, not a repository file listing.
6. Merge agent guidance and scoped Nx/prek/CI targets. Require all relevant source and destination gates, with affected/path filtering that cannot skip required whole-repository safety checks. Replace informational security findings with blocking, documented gates where upstream fixes permit.
7. Audit dead code and duplication using tests, static analysis, and runtime references. Remove or consolidate only demonstrated dead/bloated code while retaining contract coverage.

## Files to modify

- `plans/port-linkouts.md` (this plan only during planning)
- `AGENTS.md`, `.pi/`, `.claude/`
- `.github/workflows/`, `.github/dependabot.yml`
- `prek.toml`, `nx.json`, `package.json`, `flake.nix`, `nix/`, `Makefile`
- `dagster/workspace.yaml`, `dagster/dg.toml`, deployment configuration
- New `dagster/projects/linkouts/**`
- New `sites/linkouts/**`
- New `sites/docs/**`
- Existing `docs/**` content as it is migrated or retired

## Reuse

- `dagster/projects/dakp/src/dakp_dag/assets.py`: narrow per-output jobs and lineage-only asset dependencies.
- `dagster/libs/mo-dagster/src/mo_dagster/pipes.py`: Dagster Pipes subprocess helper patterns.
- `dagster/projects/dakp/workers/go/internal/pipesutil/pipesutil.go`: mirror its small cache/materialization reporting pattern in a project-local Linkouts helper with a Linkouts-owned worker version; Go `internal` visibility prevents direct cross-module reuse.
- Root `prek.toml`: fast pre-commit and comprehensive pre-push hook split.
- Root `nx.json` and package `project.json` files: cached, affected test/lint targets.
- `.github/workflows/ci.yml` and `.github/workflows/pages.yml`: destination CI and documentation deployment baseline.
- Source `.github/workflows/ci.yml`: race-enabled offline Go tests, fuzz smoke, gofumpt, golangci-lint, warnings-as-errors Elixir compile/tests, strict Credo, cluster tests, KG config validation, Hex audit, contract staleness checks, and pre-commit verification.
- Source `.pre-commit-config.yaml`: generic whitespace/JSON/YAML/merge/large-file checks, Gitleaks, fast formatting, and path-scoped pre-push gates.
- Source `.github/workflows/deploy.yml`: path-scoped Fly deployment and least-privilege permissions.
- Source CI action pinning: immutable commit-SHA pins are stronger than the destination's current movable major-version tags and should become the merged convention.

## Steps

- [ ] Import the pinned Linkouts source snapshot using the mapping above; preserve all runtime code, tests, fixtures, display configs, static assets, dictionaries, ADRs, and deployment assets not explicitly retired.
- [ ] Create `linkouts_pipeline` with Dagster-free worker payload models, typed validation, content-addressed staging manifests, artifact references, and tests. Create `linkouts_dag` with only definitions, Dagster config, resources, assets, and jobs.
- [ ] Port Go internals to a monorepo module path. Extract command-embedded operations into focused internal packages, add the argument-free Pipes worker, move command tests to operation/worker tests, and retain structured logging/cancellation/error behavior.
- [ ] Implement content-fingerprinted staging, resumable publication, readback/probe checks, inspection outputs, dictionary artifacts, and guarded admin jobs. Register narrow jobs plus `linkouts_upload`, `dg.toml`, and `workspace.yaml`; add DAKP-style single-output and stub-worker tests.
- [ ] Consolidate canonical contract fixtures and small KGX inputs. Update Go golden tests and Elixir readers to use them, add byte-staleness checks, and run a real Go-worker/file-store Dagster integration test.
- [ ] Move Phoenix to `sites/linkouts/`; fix every KG/config/fixture/data path, preserve compiled validation and runtime dictionary behavior, update feedback links, and retain all offline/cluster/UI tests.
- [ ] Move and correct the Dockerfile, `.dockerignore`, production/preview Fly configs, and deployment workflow. Build the production image from the monorepo root and keep preview data public and credential-free.
- [ ] Scaffold Astro Starlight in `sites/docs/`, add npm workspace/Nx scripts, implement the intent-first homepage and navigation, migrate all useful Linkouts and DAKP documentation, and delete ExDoc/Cobra/hand-written HTML machinery after link/content review.
- [ ] Add Nix/Node/Go/BEAM tooling and lockfiles, explicit Nx cache inputs/outputs, module-aware `prek` scripts, generalized recursive lock checking, Dependabot entries, and Makefile component commands.
- [ ] Rebuild CI with scoped path detection, all source/destination gates, SHA-pinned actions, least privileges, required aggregate status, Pages build/deploy, and CI-gated Fly deployment. Test both changed-component and shared-contract fan-out.
- [ ] Merge agent skills, design context, issue/security/contributor guidance, and root documentation. Remove confirmed dead code and duplicated fixtures, run unused-dependency/static-reference audits, and document any additional deletion.
- [ ] Run the complete verification matrix offline first; use only disposable file/fake backends for integration. Perform any real Cosmos/Fly operation only as an explicit later deployment step with production credentials.

## Verification

Run the affected graph and then the complete offline matrix from a clean checkout:

```sh
npx nx show projects
npx nx affected -t lint,test,typecheck,contract,build --base=origin/main --head=HEAD
npx nx run-many -t lint,test,typecheck,contract,build --all
scripts/check-locks.sh
cd dagster/deployments/local && uv sync --quiet && uv run dg check defs
```

Verify affected scoping with no changes, one DAKP-only change, one Linkouts-Go change, one Phoenix-only change, one docs-only change, and one shared-contract/toolchain change. The shared cases must fan out to both implementations; unrelated jobs must remain skipped without weakening the required aggregate result.

Go gates run independently in both modules. For Linkouts:

```sh
cd dagster/projects/linkouts/workers/go
go mod tidy -diff
go vet ./...
go test -race -count=1 ./...
for fuzz in FuzzCanonicalStable FuzzDeltaRoundTrip FuzzBlobRoundTrip; do
  go test ./internal/codec -run='^$' -fuzz="^${fuzz}$" -fuzztime=30s
done
govulncheck ./...
```

Also assert `gofumpt -l .` and golangci-lint are clean, the worker builds, and no Cobra/documentation dependency remains.

Phoenix and contract gates:

```sh
cd sites/linkouts
mix format --check-formatted
mix deps.unlock --check-unused
MIX_ENV=test mix compile --force --warnings-as-errors
mix credo --strict
mix test --warnings-as-errors
CLUSTER_TEST=1 mix test test/edge_linkouts/cluster_two_node_test.exs --include cluster
mix linkouts.check
mix dialyzer
mix hex.audit
```

The contract job additionally regenerates Go fixtures in a temporary directory, compares bytes, runs the Elixir contract test against the canonical corpus, and fails on any checked-in diff.

Dagster tests must load every definition and execute `linkouts_upload` with the real Go worker against disposable file stores. Assert staging and publication output, retry idempotency, changed-input isolation, no secret payloads/logs, and filtered/incomplete-manifest refusal. Execute each narrow job and prove it neither rebuilds upstream assets nor mutates unrelated outputs. Assert destructive jobs are absent by default and reject an incorrect confirmation when explicitly enabled. No test uses production Cosmos.

Docs, deployment configuration, and repository gates:

```sh
npm ci
npm run docs:check
npm run docs:build
prek run --all-files --hook-stage pre-commit
prek run --all-files --hook-stage pre-push
actionlint
zizmor .github/workflows
nix flake check --no-build --no-warn-dirty
docker build -f sites/linkouts/Dockerfile -t graph-monorepo-linkouts:test .
flyctl config validate -c sites/linkouts/fly.toml
flyctl config validate -c sites/linkouts/fly.dev.toml
```

Inspect the built Pages artifact for the intent-first homepage, working Pagefind index, valid `/dakp/` and Linkouts routes, and no broken links. Run the production container with the public preview fixture and smoke-test `/healthz`, root, edge/version, raw, and KGX routes without production credentials. Exercise CI path behavior on a pull request before requiring the aggregate check; deploy to Fly only as a separate, explicitly approved production step.

## Documentation approach

Use **Astro Starlight** for `sites/docs/`. It provides static prerendered output, accessible navigation and dark mode, Markdown/MDX content with frontmatter validation, built-in Pagefind search, customizable splash templates and components, sitemap support, and first-party GitHub Pages deployment guidance. It fits the existing Node 24/npm/Nx toolchain and removes the separate ExDoc project, its custom Mix task, Cobra's Markdown generator, and their generated output.

Upstream evidence:

- <https://starlight.astro.build/>: documentation navigation, accessibility, search, SEO, Markdown/MDX, typed frontmatter, and Astro extensibility.
- <https://starlight.astro.build/guides/customization/>: splash landing pages, custom components/styles, sitemap, edit links, and custom 404 support.
- <https://starlight.astro.build/guides/site-search/>: built-in low-bandwidth Pagefind search for static sites.
- <https://docs.astro.build/en/guides/deploy/github/>: official static GitHub Pages build/deployment and required `site`/`base` configuration.
- <https://docs.astro.build/en/guides/typescript/#type-checking>: `astro build` transpiles without type checking, so CI must run `astro check` separately.

The homepage will route readers by intent before exposing detailed navigation: **use DAKP data**, **explore an edge in Linkouts**, **run a Dagster pipeline**, **add a KG**, **operate or release**, and **contribute to the monorepo**. Sidebar sections will cover Getting Started, DAKP, Linkouts, Platform, Operations, Reference, and Decisions. Migrate the useful source guides and four ADRs; rewrite CLI examples as Dagster launch/config instructions; migrate the current DAKP HTML to maintainable Markdown/MDX; preserve the existing `/dakp/` route in the new site.

Documentation checks will include formatting, `astro check`, production `astro build`, internal/external link validation, and Pages artifact deployment. Configure `site` and `base` explicitly for the graph-monorepo Pages URL. Remove `docs/mix.exs`, `docs/mix.lock`, `docs/lib/`, generated `docs/cli/`, and hand-written HTML after content migration; do not reproduce stale CLI documentation or contradictory defaults.

## Dagster operation mapping

The source's normal low-cost upload workflow is a staged `load` into a file store followed by `push` into Cosmos. `load` joins KGX nodes/edges, merges version deltas, samples the per-release random pool, supports dry-run/resume/ID-filter tails, and writes the pool index only after successful edge writes. `push` is resumable, writes edge documents before pool documents, and writes the shared pool index last. These become separate assets so each has a narrow manual job and the full `linkouts_upload` job runs the intended chain.

Proposed operation mapping:

| Existing command | Monorepo surface |
| --- | --- |
| `load` | `stage_release` asset; typed release/input/store/engine/dictionary/compression/resume/sampling/throughput config; full and narrow jobs. Direct-to-Cosmos mode is consolidated into the two-stage `linkouts_upload` path |
| `push` | `push_staged_release` asset; explicit staged-artifact dependency and target config; dry-run metadata; full and narrow jobs |
| `train-dict` | Separate dictionary-training asset/job producing a versioned artifact consumed by staging and Phoenix deployment |
| `status` / `probe` | Read-only verification assets and manual jobs, with Dagster asset checks/metadata for store health and read cost |
| `get` | Read-only manual inspection job returning structured metadata, not a terminal formatter |
| `rig` | Read-only KGX inspection/config-draft job producing an artifact |
| `init` | Separate idempotent provisioning job, never an implicit upload side effect |
| `purge` / container drop | Separate destructive jobs with exact-target confirmation in typed run config; dry-run where meaningful; excluded from `linkouts_upload` and all future automatic triggers |
| `docs` | Delete with Cobra; documentation is maintained by the static docs site |

Preserve every meaningful command option through typed run config or process-level environment configuration: input/output locations, release/key/version selectors, backend/engine settings, worker and partition counts, compression/dictionary settings, pool sampling, retry/resume/delay/batch controls, probe bounds, raw/version/KGX inspection formats, predicate/table output for KGX inspection, and dry-run behavior. Do not carry forward Cobra's help/completion/version surface; worker and operation versions become structured Dagster metadata.

The worker should dispatch explicit operation names from Pipes extras and emit structured materialization/check results. Build one hidden `cmd/linkouts-worker` binary with no arguments or command parser. It must not reconstruct a CLI or accept arbitrary command-line text. Move non-interactive logic out of `cmd/linkouts`; replace terminal confirmation with Dagster's explicit run configuration and exact resource/target matching. Preserve cancellation, close-error, retry, idempotency, and partial-failure semantics in the internal packages.

Run-specific values use typed Dagster configuration; process defaults and credentials use environment variables. Never put Cosmos connection strings/keys or ClickHouse credentials in Pipes extras, run metadata, or logs. Remove partial-key masking and log only whether a credential was configured.

For staging, the worker streams BLAKE3 fingerprints of the normalized key, nodes/edges content, dictionary, codec settings, and worker version, then uses a digest-scoped store under `$LINKOUTS_WORKDIR`. A manifest records those fingerprints and the output state. A retry may resume only an exact manifest match; changed inputs select a different store; an explicit restart affects only that digest-scoped staging path. Publish consumes this manifest and refuses missing, incomplete, filtered, or mismatched staged data. ID-filter and no-pool modes remain diagnostic validation options, not valid production upload inputs. Use separate dry-run/validation jobs so `linkouts_upload` cannot silently skip publication.

Dictionary training is deliberately outside `linkouts_upload`. Roll out a new dictionary by training it, adding it to the Phoenix registry while retaining every old dictionary, deploying the reader, then staging/pushing data that uses it. This preserves reads of old and new envelopes throughout the rollout. A later sensor may issue a `RunRequest` for `linkouts_upload`, but this port adds no sensor or schedule and never exposes destructive jobs to automation. Destructive definitions are absent unless `LINKOUTS_ENABLE_DESTRUCTIVE_JOBS=1`, and then require an exact resolved account/container/release confirmation string.

## Quality-gate inventory

Destination gates currently cover per-package `uv lock --check`, `uv sync --group dev`, pytest, all Dagster definitions, DAKP Go build/test/lint, and Nix flake evaluation. Its Pages workflow publishes hand-written static HTML. Its `prek` configuration currently scopes all Go changes to the DAKP module, so adding a second Go module requires explicit per-module hook routing.

Source gates additionally require gofumpt, `go test -race -count=1 ./...`, codec fuzz smoke, Elixir formatting, forced warnings-as-errors compilation, strict Credo, offline tests, two-node cluster tests, `mix linkouts.check`, Hex audit, byte-reproducible Go-to-Elixir contract fixture checks, generated-doc staleness checks, Gitleaks, and generic file hygiene. Source `govulncheck` is informational only because its Go 1.26.8 toolchain lacks fixes expected in 1.26.9; the port selects Go 1.26.10 and makes this gate blocking.

The merged CI should use independent scoped jobs for Python, each Go module, Phoenix, docs/contracts, hooks, and Nix; a final required aggregate status should prevent a skipped path job from being interpreted as success incorrectly. All third-party actions should be SHA-pinned, workflows should declare minimal permissions and timeouts, and deployment triggers must follow the moved paths.

## Scoped CI, hooks, and toolchain

Use a SHA-pinned paths-filter job to route changes to independent jobs: destination Python/Dagster, DAKP Go, Linkouts Go, Linkouts Phoenix, cross-language contracts, docs, Nix, and workflow/hook hygiene. Shared root/toolchain changes fan out to every dependent job. Contract changes run both writer and reader checks even when only one side changed. A final always-running required aggregate job inspects every needed result and fails on failure/cancellation or an unexpectedly skipped applicable job; branch protection should require the aggregate, not optional path jobs.

CI gates:

- **Python/Dagster:** recursive `uv lock --check`, per-package sync and pytest, Ruff lint/format, and `dg check defs` for every code location.
- **Both Go modules:** module-local tidy/build/vet checks, `go test -race -count=1 ./...`, golangci-lint, and blocking `govulncheck`. Linkouts also runs gofumpt and its codec fuzz-smoke target. Use module-specific caches and config; never route Linkouts files through the DAKP command. Set the Linkouts module/devshell toolchain to Go 1.26.10 so the source's pending stdlib fixes are present.
- **Phoenix:** `mix format --check-formatted`, forced warnings-as-errors compile/test, strict Credo, unused-dependency check, `mix linkouts.check`, offline tests, two-node cluster tests, `mix hex.audit`, and Dialyzer with a lock/toolchain-keyed PLT cache.
- **Contracts:** byte-reproducible Go golden generation and Elixir decoding/re-encoding against one canonical fixture directory; verify no generated diff.
- **Docs:** npm clean install, Prettier check, `astro check`, production build, link checks, and Pages artifact validation.
- **Repository:** generic whitespace/JSON/YAML/TOML/merge/large-file checks, Gitleaks with the narrowly scoped source allowlists, actionlint, zizmor, Nix evaluation, and production Docker build/config validation when Linkouts deployment inputs change.

Pin every third-party action by commit SHA, add explicit least-privilege permissions and timeouts, retain concurrency cancellation for CI but not Pages/deployment, and keep dependency updates enabled for npm, both Go modules, Linkouts Mix/Docker, every uv package, and GitHub Actions. Pin the production container base by digest. The Fly deployment must run only for changed deployment inputs on `main` after applicable CI succeeds; preserve a separate fixture-backed preview app and health checks.

Extend the Nix devshell with `beam28Packages.elixir_1_19` on OTP 28, gofumpt, govulncheck, Gitleaks, lychee, zizmor, and flyctl. Node/Astro dependencies remain npm-lockfile-managed; Python remains uv-managed. Keep the existing Go toolchain override and add the required BEAM environment/cache setup. The current unstable nixpkgs snapshot provides Elixir 1.19.7/OTP 28.5.0.8; align CI and the container to the same OTP 28 / Elixir 1.19 compatibility line rather than relying on a runner image default.

`prek` remains two-stage and path-scoped:

- **Pre-commit:** generic hygiene and Gitleaks on staged files; Ruff for Python; module-routed gofumpt/golangci-lint formatting for DAKP versus Linkouts Go; `mix format` only for Phoenix/config files; Prettier only for docs; treefmt only for Nix.
- **Pre-push:** lock/tidy checks for touched ecosystems, module-wide lint/type/static checks, contract checks when either side changes, and `nx affected` test/build targets with the proper base. Workflow and Nix files trigger their own validators. No network audit or full race/cluster suite runs on every commit.

Add explicit Nx projects and cache inputs for Linkouts Dagster/pipeline, Linkouts Go, Phoenix, and docs. `sites/docs/dist` is a cached build output. Shared contracts and root CI/tool configuration are inputs to both sides, so Nx cannot return a stale success after a contract or toolchain change.

## Dead code and duplication removal

- Remove Cobra, `cmd/linkouts`, CLI build/version variables, generated CLI pages, and docs staleness machinery after moving still-relevant tests and operation logic to internal packages/worker tests.
- Remove the ExDoc-only Mix project and custom `docs.site` task; migrate content instead of wrapping it.
- Remove `cli/tools/repair-pool-index`: it is a one-off historical repair with hardcoded releases/counts and production writes, not a reusable recovery primitive. Document supported backup/recovery instead.
- Remove unused `DictPath` and `ZstdLevel` configuration fields and their dead parsing paths. Retain operational settings that are actually read. Removing Cobra also removes its pflag/md2man/blackfriday dependency subtree after `go mod tidy`.
- Keep one canonical contract corpus under `dagster/projects/linkouts/contracts/`; Go and Elixir tests read it directly. The preview deployment uses the same public `docs.ndjson`, eliminating both duplicate copies.
- Replace the fixture extractor's machine-specific defaults and old DAKP path assumptions with explicit input/output arguments under the Linkouts project; retain the committed small real/synthetic KGX fixtures.
- Remove the machine-specific DAKP smoke-test defaults and stale generated documentation examples. Replace scaffold READMEs and duplicated ignore rules with component-specific instructions and consolidated root ignores.
- Run unused-dependency, compiler, static-analysis, and reference audits before deleting anything else. Preserve `engine/fake.go`, file stores, preview support, and test fakes: they are used by offline contracts/development and are not dead code.

## Agent and contributor tooling

Preserve existing `.pi` skills and add focused Linkouts skills for adding a KG, running/resuming an upload, dictionary rollout, and deployment/verification. Update `add-dag` and `add-worker-go` to describe multiple workers and the hidden Pipes worker convention. Colocate the source `PRODUCT.md`, `DESIGN.md`, and `.impeccable/design.json` with `sites/linkouts/` so design agents operate on the Phoenix application rather than treating its design system as monorepo-wide.

Merge the source's structured bug/KG-request templates, Gitleaks rules, security guidance, and contribution examples into destination files with graph-monorepo paths and Dagster terminology. Update `feedback_repo` and generated feedback links to the monorepo's actual issue templates. Expand `AGENTS.md`, `README.md`, `CONTRIBUTING.md`, and `.envrc.example` with component ownership, secrets handling, manual-job instructions, and scoped check commands.

Document one deliberate rule distinction: DAKP-style KG-building DAGs still publish KGX ndjson only. Linkouts consumes KGX and materializes a compressed serving index; that storage envelope is not an alternative KG publication format. Never import implementation code across projects; share only the versioned contract fixtures and genuinely generic helpers.

## Non-goals

- No automatic sensor/schedule in this port; `linkouts_upload` is the stable manual entrypoint for a later `RunRequest` integration.
- No public CLI compatibility layer and no Elixir/Phoenix-to-static rewrite.
- No Linkouts Git-history import, external source dependency, production data mutation, or live deployment during local verification.
- No permanent `continue-on-error` vulnerability exception; Go 1.26.10 supersedes the source's informational stdlib advisory workaround.

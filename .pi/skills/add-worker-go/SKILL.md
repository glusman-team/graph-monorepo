---
name: add-worker-go
description: Add a Go Pipes worker to an existing DAG's workers/go directory, following the DAKP/Linkouts patterns (cmd/ + internal/, argument-free, typed payload).
---

# Add a Go worker

1. Workers live in `dagster/projects/<dag>/workers/go/` (own `go.mod`, no repo-wide go.work).
   Module path is the full monorepo path, e.g.
   `github.com/glusman-team/graph-monorepo/dagster/projects/<dag>/workers/go`.
2. Create the command under `cmd/<workername>/main.go`. Workers are HIDDEN and
   ARGUMENT-FREE: all input arrives in the Dagster Pipes payload; flags and
   positional args are not part of the interface.
3. Put reusable logic in `internal/` packages (`internal/operations` for
   one-package-per-operation, plus `internal/worker` for dispatch/contracts).
   Commands stay thin.
4. DAKP pattern (`internal/pipesutil`):
   - `dagsterpipes.New[map[string]any]()` for the session, `session.Run(run)`.
   - Read `ctx.Extras()` for input/output paths (python side passes them via mo-dagster).
   - `pipesutil.HashInput(raw)` for the BLAKE3 cache key; check the artifact
     exists for a cache hit before recomputing.
   - `pipesutil.ReportMaterialization(ctx, assetKey, dataVersion, cacheInfo, rows)`
     - metadata: cache_key, cache_hit, rows, worker_version.
   - Bump `pipesutil.WorkerVersion` when extraction behavior changes.
   Linkouts pattern (`internal/worker`): typed request structs decoded from the
   Pipes payload, one `run<Operation>` per operation, structured `slog` logging,
   context cancellation, atomic report writes. Follow the owning project's
   pattern; do not import across `projects/*`.
5. First build on a fresh checkout: `go get github.com/hupe1980/dagster-pipes-go github.com/zeebo/blake3`
   then `go build -o bin/<workername> ./cmd/<workername>`, `go test ./...`.
   `go mod tidy` must stay clean; each worker module is independent.
6. Types: schemas come from mo-core (`dagster/libs/mo-core/scripts/export_json_schema.py`),
   generated via `go generate` into `gen/` (quicktype). Keep the fixture roundtrip test green.
7. nx targets (`build`/`test`/`lint`) are inferred from go.mod by `@naxodev/gonx`:
   `npx nx test dagster/projects/<dag>/workers/go` etc. work from the repo root
   (gonx names the project by its path; `npx nx show projects` lists it).
8. Point the launching dagster asset at the binary via env var (see
   `DAKP_EXTRACT_WORKER_BIN` in `projects/dakp/src/dakp_dag/assets.py` and
   `LINKOUTS_WORKER_BIN` in `projects/linkouts/src/linkouts_dag/config.py`),
   never a hardcoded path.
9. Tests: command tests become operation tests plus one dispatch test with a
   stub session. Preserve failure-path, cancellation, and structured-log
   assertions when porting a command.

Scala workers later: same layout under `workers/scala/` (mill), pipes via
`dagster-pipes-java`. Never mix languages inside one directory.

---
name: add-worker-go
description: Add a Go pipes worker to an existing DAG's workers/go directory, following the DAKP pattern.
---

# Add a Go worker

1. Workers live in `dagster/projects/<dag>/workers/go/` (own `go.mod`, no repo-wide go.work).
2. Create the command: `workers/go/<workername>/main.go`.
3. Use `internal/pipesutil`:
   - `dagsterpipes.New[map[string]any]()` for the session, `session.Run(run)`.
   - Read `ctx.Extras()` for input/output paths (python side passes them via mo-dagster).
   - `pipesutil.HashInput(raw)` for the BLAKE3 cache key; check the artifact
     exists for a cache hit before recomputing.
   - `pipesutil.ReportMaterialization(ctx, assetKey, dataVersion, cacheInfo, rows)`
     - metadata: cache_key, cache_hit, rows, worker_version.
   - Bump `pipesutil.WorkerVersion` when extraction behavior changes.
4. First build on a fresh checkout: `go get github.com/hupe1980/dagster-pipes-go github.com/zeebo/blake3`
   then `go build -o bin/<workername> ./<workername>`, `go test ./...`.
5. Types: schemas come from mo-core (`dagster/libs/mo-core/scripts/export_json_schema.py`),
   generated via `go generate` into `gen/` (quicktype). Keep the fixture roundtrip test green.
6. nx targets (`build`/`test`/`lint`) are inferred from go.mod by `@naxodev/gonx`:
   `npx nx test dagster/projects/dakp/workers/go` etc. work from the repo root (gonx names the project by its path; `npx nx show projects` lists it).
7. Point the launching dagster asset at the binary via env var (see
   `DAKP_WORKER_BIN` in `projects/dakp/src/dakp_dag/assets.py`), never a hardcoded path.

Scala workers later: same layout under `workers/scala/` (mill), pipes via
`dagster-pipes-java`. Never mix languages inside one directory.

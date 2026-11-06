---
name: linkouts-operations
description: Safely run Linkouts Dagster operations - build the hidden worker, verify offline with a file store, launch manual upload/narrow jobs, and treat real Cosmos/Fly mutations as explicit deployment steps. Use when the user says stage/publish/upload/probe/verify a Linkouts release, train a dictionary, inspect KGX, purge/reinit a store, or asks what jobs exist.
---

# Linkouts operations

## Shape

- `dagster/projects/linkouts/pipeline/` - dagster-free Python: typed payloads,
  staging manifests, publication, artifact references. No dagster imports.
- `dagster/projects/linkouts/src/linkouts_dag/` - assets, jobs, resources, run configs.
- `dagster/projects/linkouts/workers/go/` - hidden argument-free Go Pipes worker
  (`cmd/linkouts-worker`). Operations: load/stage/push/status/probe/get/init/purge/
  train-dictionary/inspect/rig/verify. The Pipes payload is the only input surface.
- `sites/linkouts/` - Phoenix reader (separate concern; do not touch for ingestion).

Normal upload = manually launched `linkouts_upload`:
`stage_release` -> `push_staged_release` -> `status_report` -> `probe_release`.
Narrow jobs (`linkouts_upload_<stage>` etc.) build exactly one output and read
published refs; they fail with "upstream artifact missing" if the producer has
not run. That is the intended contract - never make a consumer rebuild upstream.

## Offline verification (default)

Always start here. Disposable local dirs only:

```sh
make linkouts-worker-build
make linkouts-contract-test   # canonical corpus: Go regenerates bytes, Elixir reads them
make linkouts-test            # dagster-free pipeline + dagster defs/narrow-job tests
make linkouts-web-test        # phoenix suite (file backend, no credentials)
make check                    # offline aggregate
```

A real integration run points `LINKOUTS_WORKDIR` at a fresh temp dir and uses
a `file:` store. Staging materializes under that workdir. Nothing in tests or
examples may point at a real Cosmos account.

## Manual run (file store)

1. `cd dagster/projects/linkouts && uv sync --group dev`
2. Build the worker: `make linkouts-worker-build` from the repo root
   (sets `LINKOUTS_WORKER_BIN`; never hardcode the path).
3. `make dev`, then in the UI launch `linkouts_upload` with typed launch config:
   kg/version key (`infores:<kg>-<version>`), KGX paths, store `file:<dir>`,
   dictionary path. Secrets stay out of launch config entirely.
4. Re-run `push_staged_release` after an interrupted run: staging is
   content-addressed and publication resumes; already-published documents are
   not rewritten.
5. Read `$MO_PUBLISHED_ROOT/linkouts/` refs and reports. `status_report` counts
   edge documents and pool presence; `probe_release` samples point reads with
   mean/worst RU thresholds.
6. Point Phoenix at the same file store to eyeball pages:
   `cd sites/linkouts && COSMOS_BACKEND=file COSMOS_DOCS=<docs.ndjson> mix phx.server`.

## Dictionaries

`train_dictionary` / dictionary artifacts are separate narrow jobs. The
dictionary id is stored on documents; changing training changes compression,
not semantics. Never retrain mid-upload and never mix dictionaries across
documents of one store without a purge + reload.

## Destructive jobs

`init_store` (provisioning) and the purge scopes are guarded twice:

1. definitions only exist when `LINKOUTS_ENABLE_DESTRUCTIVE_JOBS=1` is set at
   code-location load (not in a shared webserver/daemon environment), and
2. execution requires the exact confirmation string for the selected store and
   scope (for example `DELETE cosmos kg <slug>`; container drop is `DROP <store>`).

Dry-run staging can never publish; dry-run purge reports without deleting.
Never enable these definitions just to "get past" a test - use a file store.

## Real mutations (explicit only)

Real Cosmos writes, container drops, and Fly deploys require the user's explicit
go-ahead for that operation, that target, and that version. Do not run them as
part of port verification or CI. Azure keys come from the environment
(`COSMOS_PRIMARY_CONNECTION_STRING_RW` worker-only; Phoenix sees the read-only
key). Record the exact command, target, and resulting report; never paste keys.

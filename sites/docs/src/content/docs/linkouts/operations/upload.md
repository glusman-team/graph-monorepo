---
title: Stage and publish a release
description: Manually upload KGX through content-addressed staging, resumable publication, and readback checks.
sidebar:
  order: 0
---

`linkouts_upload` is the normal Linkouts entrypoint. Launch it manually from the Dagster UI. It contains only `stage_release`, `push_staged_release`, `status_report`, and `probe_release`; dictionary training, inspection, provisioning, and destructive work stay in separate jobs.

## Inputs

A KGX release needs two NDJSON files and a stable version key:

- `nodes.ndjson`: one node per line with at least `id`, ideally `name` and `category`;
- `edges.ndjson`: one edge per line. Every edge must have a stable `id`, which becomes its permanent page URL;
- `<name>-<version>`, for example `infores:drugapprovals-kp-1.16.0`.

Use the canonical infores name. The name without `infores:` becomes the slug in documents, random pools, and URLs. Match a display config's `name:` or Phoenix uses the generic default display.

## Prepare the worker

From the Linkouts project on the build host:

```sh
cd dagster/projects/linkouts/workers/go
go build -o bin/linkouts-worker ./cmd/linkouts-worker

cd ../..
uv sync --group dev
uv run dg dev --project .
```

The binary accepts no command-line arguments. Dagster supplies a typed, secret-free Pipes payload. The worker reads process-level cloud credentials only from its environment.

| Environment variable                  | Purpose                                                           |
| ------------------------------------- | ----------------------------------------------------------------- |
| `MO_PUBLISHED_ROOT`                   | Artifact references shared between independent runs.              |
| `LINKOUTS_WORKDIR`                    | Content-addressed staging and per-run reports.                    |
| `LINKOUTS_WORKER_BIN`                 | Hidden worker path; defaults to `workers/go/bin/linkouts-worker`. |
| `COSMOS_PRIMARY_CONNECTION_STRING_RW` | Read-write connection string for provisioning and publication.    |
| `COSMOS_ENDPOINT`, `COSMOS_KEY`       | Explicit overrides for endpoint and write key.                    |
| `COSMOS_DB`, `COSMOS_CONTAINER`       | Resolved target names; defaults are `edge_linkouts` and `edges`.  |
| `RU_BUDGET_CLI`                       | Worker RU pacing ceiling; default 900 RU/s.                       |
| `CHDB_CACHE_DIR`                      | Optional embedded ClickHouse extraction cache.                    |

Never copy a connection string or key into launch configuration.

## Provision once

Run the narrow `linkouts_init_store` job with:

```yaml
ops:
  init_store:
    config:
      store: cosmos
```

Provisioning is idempotent. It creates a database/container when missing, partitions on `/id`, and sets indexing to `none`: Phoenix only performs point reads by ID. Indexing would add storage and write RU costs without serving a query.

## Launch an upload

In the launchpad, select `linkouts_upload` and provide operation configuration. This example uses placeholders for local input paths and expects the exact staged key at publication:

```yaml
ops:
  stage_release:
    config:
      key: infores:drugapprovals-kp-1.16.0
      nodes_path: /data/drug-approvals/1.16.0/nodes.ndjson
      edges_path: /data/drug-approvals/1.16.0/edges.ndjson
      sample_size: 1024
      concurrency: 4
  push_staged_release:
    config:
      expected_key: infores:drugapprovals-kp-1.16.0
      target_store: cosmos
      concurrency: 4
      confirmation: PUSH <resolved-endpoint>/<resolved-database>/<resolved-container>
  status_report:
    config:
      check_pools: true
  probe_release:
    config:
      count: 20
      max_mean_ru: 2.0
      max_worst_ru: 10.0
```

For Cosmos, `confirmation` must exactly equal `PUSH <COSMOS_ENDPOINT>/<COSMOS_DB>/<COSMOS_CONTAINER>` after environment resolution. The worker computes the expected value; it does not accept a partial name or a different target. File-backed offline publication does not need this confirmation.

Use `target_store: file:/absolute/path/docs.ndjson` for a persistent disposable file target. `mem://` is rejected because a publication must survive the worker process.

## What staging does

1. Embedded ClickHouse streams edges and joins subject/object names and categories from nodes without materializing either file in Go memory.
2. Nulls, empty strings/lists, and null-like values are removed recursively.
3. The worker fingerprints the normalized key, input contents, optional dictionary, codec settings, and worker version with BLAKE3.
4. It writes a digest-scoped local store and manifest under `LINKOUTS_WORKDIR`.
5. Versions are stored as full canonical entries or deltas. A version other deltas depend on is materialized whole, preventing cycles.
6. A reservoir sample becomes `__random_pool__:<slug>:<version>` and the release is added to the `__random_pool__` index.

The normal asset rejects dry-run, ID-filtered, no-pool, incomplete, or empty staging manifests. Use `linkouts_validate_release` for diagnostic subsets.

## Publication and resume

Publication reads the staged manifest through its verified artifact reference, not an upstream in-memory value. It creates missing documents, skips identical bytes, and replaces changed documents. An interrupted publication can be launched again against the same manifest; completed identical documents are skipped.

For staging retries:

- identical inputs and settings select the same digest and may resume the exact manifest;
- changed inputs select a new digest instead of contaminating an earlier store;
- `no_repack: true` skips documents already carrying the release key;
- `restart: true` restarts only the selected digest-scoped staging path;
- `expected_key` prevents publishing a different staged release.

Cosmos writes use ETag preconditions and measured RU charges. Throttling is retried with backoff rather than treated as a completed write. The web budget defaults to 100 RU/s and the worker to 900 RU/s on the shared 1000 RU/s account.

## Verification in the same job

`status_report` checks document counts, graphs, releases, pool membership, pool consistency, and the container indexing policy. `probe_release` performs seeded random point reads and reports latency and RU cost. Optional `max_mean_ru` and `max_worst_ru` make an unexpected cost regression fail the run.

A later sensor may request `linkouts_upload`, but this port installs no schedule or sensor. Do not automate provisioning or destructive jobs.

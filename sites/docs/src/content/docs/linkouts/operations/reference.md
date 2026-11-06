---
title: Dagster job reference
description: Every normal, verification, inspection, dictionary, provisioning, and guarded administration job.
sidebar:
  order: 1
---

All Linkouts jobs are manual. Narrow jobs build one output and read upstream artifacts from published references; they do not rebuild the upstream chain implicitly.

## Job inventory

| Job                            | Purpose                                                     | Writes to a shared target?               |
| ------------------------------ | ----------------------------------------------------------- | ---------------------------------------- |
| `linkouts_upload`              | Stage, publish, status, and probe for one complete release. | Yes, only through `push_staged_release`. |
| `linkouts_load_release`        | Directly merge one DAKP release into the live store.        | Yes; normally launched by DAKP sensor.   |
| `linkouts_stage_release`       | Build one staging manifest.                                 | Digest-scoped local staging only.        |
| `linkouts_push_staged_release` | Publish an existing verified manifest.                      | Yes.                                     |
| `linkouts_status_report`       | Inspect stored releases, pools, indexing, and counts.       | Report artifact only.                    |
| `linkouts_probe_release`       | Measure point-read latency and RU charges.                  | Report artifact only.                    |
| `linkouts_validate_release`    | Complete a dry-run or filtered diagnostic stage.            | Digest-scoped local staging only.        |
| `linkouts_dry_run_publication` | Mirror a manifest into a disposable in-memory target.       | No persistent target.                    |
| `linkouts_train_dictionary`    | Train and report a deterministic zstd dictionary.           | Dictionary/report artifacts only.        |
| `linkouts_inspect_kgx`         | Summarize KGX fields or emit a display starter.             | Text artifact only.                      |
| `linkouts_inspect_edge`        | Inspect one stored edge and its versions.                   | JSON artifact only.                      |
| `linkouts_init_store`          | Idempotently provision the Cosmos database/container.       | Yes; never part of upload.               |
| `linkouts_purge`               | Opt-in release, KG, store, or container deletion.           | Destructive; absent by default.          |

## Staging fields

`stage_release` accepts:

| Field                             | Default                | Meaning                                                                    |
| --------------------------------- | ---------------------- | -------------------------------------------------------------------------- |
| `key`, `nodes_path`, `edges_path` | required               | Release key and KGX inputs.                                                |
| `base_key`                        | none                   | Preferred full-version base for delta encoding.                            |
| `dictionary_path`                 | none                   | Trained zstd dictionary.                                                   |
| `zstd_level`                      | `0`                    | Codec level from 0 through 22.                                             |
| `no_repack`                       | `false`                | Skip documents already carrying this version key.                          |
| `restart`                         | `false`                | Restart only this content fingerprint's staging path.                      |
| `sample_size`                     | `1024`                 | Random-pool capacity. Normal publication requires a nonzero sample.        |
| `sample_seed`, `sampled_at`       | deterministic defaults | Reproducible sampling controls; `sampled_at` is an ISO timestamp when set. |
| `concurrency`                     | `8`                    | Worker concurrency; use at most 4 on the designated build host.            |
| `threads`                         | `0`                    | Engine thread count; zero uses the engine default.                         |
| `engine`                          | `chdb`                 | `chdb` for real KGX or `fake` for deterministic tests.                     |
| `progress`                        | `true`                 | Structured progress logging.                                               |

`validate_release` also accepts `ids_path` and `sample_full_release`, and always uses dry-run staging. Filtered or no-pool output is diagnostic and cannot enter normal publication.

## Direct live load

`load_release` is separate from staged blue/green publication. It accepts the same KGX inputs as staging plus:

- `target_store`: `cosmos` or `file:<path>`; the default is `cosmos`;
- `dictionary_path`: required for DAKP production loads;
- `base_key`: optional preferred delta base;
- `zstd_level`, `sample_size`, `sample_seed`, `sampled_at`, `concurrency`, `threads`, `engine`, and `progress`.

The Dagster asset always sets the worker `no_repack` option so an existing edge document keeps its prior release history. The worker writes and validates a typed `linkouts.load/1` report before Dagster publishes its artifact reference. See [Automated DAKP releases](/graph-monorepo/dakp/release-automation/) for the `hf_publish` sensor contract.

## Publication fields

- `expected_key`: refuse a different staged release.
- `target_store`: `cosmos` or `file:<path>`; `mem://` is forbidden for persistent publication.
- `concurrency`: publisher concurrency.
- `confirmation`: exact resolved Cosmos target confirmation, required for non-dry-run Cosmos publication.

`dry_run_publication` uses the same fields but always writes to memory and produces only a report. It refuses to emit a persistent output path.

## Verification fields

`status_report` accepts an optional `store`, optional `dictionary_path`, and `check_pools` (default `true`). Without a store override it reads the target selected by the latest publication report.

`probe_release` accepts `store`, `dictionary_path`, `count` (default 20), optional `seed`, `max_mean_ru`, and `max_worst_ru`. Cost bounds must be positive.

## Inspection fields

`inspect_edge` requires `edge_id`. It accepts `store`, `dictionary_path`, optional `version_key`, `list_versions`, and `raw`. `raw` and `list_versions` are mutually exclusive.

`inspect_kgx` requires `edges_path`. `limit` defaults to 20,000 and zero inspects all rows. `predicate` narrows the report. `emit_config: true` writes a display-config starter; otherwise it writes a field/predicate report.

## Guarded administration

Set `LINKOUTS_ENABLE_DESTRUCTIVE_JOBS=1` in the code-location environment and reload definitions before `linkouts_purge` exists. This is separate from launch-time confirmation.

`purge_store` scopes are:

- `release`: requires `key` and forbids `kg`;
- `kg`: requires `kg` and forbids `key`;
- `all`: forbids both selectors;
- `container`: deletes the Cosmos container and has no dry-run mode.

The default `dry_run: true` previews non-container deletion. A real run requires the exact confirmation computed from the resolved store, scope, and selector. The worker error states the required value. Container deletion requires `dry_run: false` and its own exact `DROP <store>` confirmation.

Do not enable destructive definitions in a normal webserver, daemon, schedule, or sensor environment. Use a deliberately isolated code-location process for an approved maintenance window.

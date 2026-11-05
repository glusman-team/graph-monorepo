---
title: Recovery and migration
description: Resume safely, rebuild a serving index, and replace a container without ad-hoc production repair scripts.
sidebar:
  order: 3
---

The supported recovery unit is the original KGX release plus its dictionary, typed worker configuration, and content-addressed staging manifest. Do not hand-edit compressed envelopes or random-pool documents.

## Interrupted work

For an interrupted stage, rerun the same narrow job with identical inputs. The content fingerprint selects the same staging namespace. Use `no_repack: true` to skip documents already carrying the release key, or `restart: true` to restart only that fingerprint.

For interrupted publication, rerun `linkouts_push_staged_release` against the same manifest with the same `expected_key` and target. Existing identical documents are skipped. Changed or missing documents are written; a different manifest cannot be substituted accidentally.

A dry-run report is not a backup and does not create a recoverable target.

## Rebuild into a new container

Use a new container for storage-format migrations or substantial repair rather than modifying production documents in place.

1. Retain the original KGX inputs, every required dictionary, worker version, and run configuration.
2. Provision the new target with the separate `linkouts_init_store` job and a resolved `COSMOS_CONTAINER` naming the new container.
3. Stage and publish every required release with exact `expected_key` and target confirmation. Use the intended base versions so delta chains are complete.
4. Run `status_report` with `check_pools: true` and `probe_release` with explicit RU bounds against the new target.
5. Compare release labels, edge counts, pool counts, dictionary IDs, and representative edge readbacks with the old target.
6. Update Phoenix's `COSMOS_CONTAINER` through the authorized deployment process and verify the live health and edge pages.
7. Keep the old container until the rollback window closes. Any later deletion requires the separately enabled and confirmed administration job.

Changing `COSMOS_CONTAINER` is a real production operation. It is not part of offline verification or `linkouts_upload` automation.

## File-store recovery

For a disposable or development file backend, stop all writers and copy the NDJSON file to a new path. Verify its hash before restarting readers. Do not copy a file while publication is appending or replacing documents. Restore by pointing `COSMOS_DOCS` or `target_store` at the verified copy, then run status and probe checks.

## Random-pool or version inconsistencies

Treat a missing or inconsistent pool as a data-integrity failure. Rebuild from the source KGX into a clean staging namespace and publish through the normal verified path. Status deliberately rejects missing pool indexes, missing release pools, and count mismatches rather than fabricating replacements.

The historical `repair-pool-index` utility was retired: it hardcoded releases/counts and performed production writes. It is not a supported recovery mechanism.

## Cloud backup

Cosmos backup/restore, account retention, and any point-in-time restore are production Azure operations outside this repository's offline tests. Document the account's configured backup policy separately, test restoration into an isolated account/container, and never restore over the only production copy during an unverified procedure.

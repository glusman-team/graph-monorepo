---
title: Dictionary rollout
description: Train, deploy, and retire zstd dictionaries without breaking old or new edge reads.
sidebar:
  order: 2
---

Edge documents from one KG share structure, so a trained zstd dictionary can reduce storage substantially. Each compressed envelope records the dictionary ID. Phoenix resolves that ID through its registry and fails loudly on an unknown or malformed dictionary; it never guesses.

Dictionary training is deliberately outside `linkouts_upload`.

## 1. Train an artifact

Launch `linkouts_train_dictionary`:

Use `threads: 4` on a four-CPU build host:

```yaml
ops:
  train_dictionary:
    config:
      nodes_path: /data/drug-approvals/1.16.0/nodes.ndjson
      edges_path: /data/drug-approvals/1.16.0/edges.ndjson
      samples: 4096
      threads: 4
      engine: chdb
```

The job publishes the `.dict` file and a JSON report containing `dictionary_id`, sample counts, byte size, and output path. Training uses canonical encoded blob envelopes and stops at the requested sample count, making the committed golden dictionary reproducible.

## 2. Deploy the reader first

For an image-baked registry, add the artifact as:

```text
sites/linkouts/priv/zstd/<slug>.<dictionary_id>.dict
```

Retain every dictionary still referenced by stored documents. Then run:

```sh
cd sites/linkouts
mix test test/edge_linkouts/dicts_test.exs test/contract_test.exs --warnings-as-errors
```

Build and deploy the Phoenix reader before publishing data with the new dictionary.

For an externally mounted registry, set `ZSTD_DICT_DIR` to the directory containing the complete old-and-new registry. The environment setting replaces the default `priv/zstd` directory rather than merging with it, so copy all required dictionaries. The directory is loaded once at application boot into `persistent_term`; changing files requires an application restart.

## 3. Stage and publish

Set `dictionary_path` on `stage_release` to the exact trained artifact and launch `linkouts_upload`. Also set the same dictionary on standalone status, probe, or edge-inspection jobs when they need to decode dictionary-compressed documents.

The normal upload fingerprints dictionary bytes. Changing the dictionary selects a different staging namespace; it cannot resume an incompatible manifest.

## Rollback and retirement

- A reader missing a live dictionary cannot serve those documents. Do not roll back Phoenix to a registry that lacks a newly published ID.
- Keep every old dictionary while any stored envelope references it. Deltas and older releases are not rewritten by dictionary training.
- Duplicate IDs with different bytes fail registry loading because one ID must never decode two incompatible dictionaries.
- Retire a dictionary only after a verified migration proves no stored document references its ID. There is no automatic garbage collection.

The public preview currently uses dictionary-free `docs.ndjson`. Dictionary fixtures remain public test data under the [shared contract corpus](../../../contracts/).

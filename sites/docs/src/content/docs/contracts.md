---
title: Shared contracts and fixtures
description: The single byte-stable corpus used by the Go writer, Elixir reader, and public preview.
sidebar:
  order: 2
---

The canonical Linkouts contract corpus lives in `dagster/projects/linkouts/contracts/`. Do not copy it into Go or Phoenix test directories.

| File                  | Purpose                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `docs.ndjson`         | Two releases with full entries, deltas, random pools, and the pool index. Also powers the credential-free preview. |
| `dictdocs.ndjson`     | The first release encoded with the committed dictionary.                                                           |
| `fixture.dict`        | Deterministic zstd dictionary used by dictionary read/write tests.                                                 |
| `drift.ndjson`        | Older real KGX slot shapes accepted by the reader.                                                                 |
| `unresolvable.ndjson` | Malformed or unresolvable references that must fail loudly.                                                        |

Small KGX writer inputs live in `dagster/projects/linkouts/fixtures/kgx/`.

## Verify bytes

Run on the designated build host:

```sh
cd dagster/projects/linkouts/workers/go
go test ./internal/contract

cd ../../../../../sites/linkouts
mix test test/contract_test.exs --warnings-as-errors
```

The Go test regenerates every artifact in a temporary directory and compares committed bytes. The Elixir suite decodes those exact files and checks canonical re-encoding. It also exercises dictionary lookup by the ID embedded in the dictionary header.

After an intentional format change, review both implementations together and regenerate explicitly:

```sh
cd dagster/projects/linkouts/workers/go
LINKOUTS_UPDATE_CONTRACTS=1 go test ./internal/contract
```

Do not update fixtures to hide a reader or writer regression. Dictionary bytes depend on encoded blob envelopes and the requested sample count; both behaviors are pinned.

## Artifact references

Dagster assets exchange typed `ArtifactRef` JSON rather than in-memory values. Consumers check that the referenced file exists and that its current BLAKE3 hash matches the reference. This prevents a missing or modified staging manifest from silently entering publication.

---
title: Linkouts overview and local preview
description: Read permanent edge pages or run Phoenix against the public contract fixture without Azure.
sidebar:
  order: 0
---

Linkouts gives every edge in a Biolink KGX knowledge graph a permanent, human-readable page. A reviewer opening `/edges/<uuid>` sees a relationship sentence, linked identifiers, recorded evidence, provenance, and changes between releases.

**Live browser:** [linkouts.skyelanegoetz.com](https://linkouts.skyelanegoetz.com).

## Components

- `dagster/projects/linkouts/workers/go/` joins node names onto KGX edges, removes null-like values, and writes compressed versioned documents through a hidden Dagster Pipes worker.
- `dagster/projects/linkouts/src/linkouts_dag/` exposes manually launched typed jobs. There is no public Go CLI.
- `sites/linkouts/` is the Phoenix LiveView reader. Its root page lists graphs and releases, release pills open random relationships, and display behavior comes from `kgs/*.exs`.

The Go writer and Elixir reader share the [byte-stable contract corpus](../contracts/).

## Run the public preview locally

Use the committed fixture and force the file backend. Explicitly setting `COSMOS_BACKEND=file` prevents ambient cloud credentials from changing the backend.

```sh
cd sites/linkouts
mix setup
COSMOS_BACKEND=file \
COSMOS_DOCS=../../dagster/projects/linkouts/contracts/docs.ndjson \
  mix phx.server
```

Open:

- `http://localhost:4000/` for the graph and release list;
- `http://localhost:4000/edges/12ae7437-12dc-3c2a-b487-5297c09fc5e5` for a direct edge;
- `http://localhost:4000/drugapprovals-kp/random?version=1.16.0` for a random edge from one release;
- `http://localhost:4000/random` for a random edge across latest releases.

The fixture contains two releases, full and delta entries, random pools, and the pool index. It requires no Cosmos account and contains no credentials.

## Choose an operating task

- [Upload a release](operations/upload/) for content-addressed staging, publication, status, and probe checks.
- [Add a KG display](display/add-a-kg/) to render a new graph or improve an existing sentence.
- [Run the job reference](operations/reference/) for validation, inspection, dictionaries, provisioning, and guarded administration.
- [Deploy](deployment/) only after offline verification. Preview and production are separate Fly applications.

## Verification

From `sites/linkouts`, the core offline gates are:

```sh
mix format --check-formatted
MIX_ENV=test mix compile --force --warnings-as-errors
mix credo --strict
mix test --warnings-as-errors
mix test --warnings-as-errors --only cluster
MIX_ENV=test mix linkouts.check
mix deps.unlock --check-unused
```

The DailyMed test tagged `:live` is a separate network check, not part of the offline matrix.

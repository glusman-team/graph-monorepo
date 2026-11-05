---
title: Repository architecture
description: Component ownership, artifact boundaries, and the two kinds of knowledge-graph workflow.
sidebar:
  order: 1
---

Graph Monorepo hosts independent Dagster code locations, shared Python libraries, language-specific workers, a dynamic Phoenix reader, and this static documentation site.

## Choose by ownership

| Path                                          | Owner                   | Responsibility                                                                  |
| --------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------- |
| `dagster/projects/dakp/`                      | DAKP                    | Acquire sources, extract and shape assertions, and publish validated KGX.       |
| `dagster/projects/linkouts/pipeline/`         | Linkouts                | Dagster-free typed operation, staging, and artifact contracts.                  |
| `dagster/projects/linkouts/src/linkouts_dag/` | Linkouts                | Definitions, resources, assets, narrow jobs, and run configuration only.        |
| `dagster/projects/linkouts/workers/go/`       | Linkouts                | KGX ingestion and the hidden argument-free Dagster Pipes worker.                |
| `dagster/projects/linkouts/contracts/`        | Both Linkouts languages | Canonical stored-document bytes and dictionary fixtures.                        |
| `dagster/projects/linkouts/fixtures/kgx/`     | Linkouts                | Small public KGX writer inputs.                                                 |
| `sites/linkouts/`                             | Phoenix                 | Dynamic edge pages, random-release navigation, display configs, and deployment. |
| `sites/docs/`                                 | Documentation           | Astro Starlight content, search, and GitHub Pages output.                       |
| `dagster/libs/`                               | Shared libraries        | Only genuinely generic Python models, KGX writing, and Dagster helpers.         |

## Artifact boundaries

Every independently runnable stage publishes an artifact reference. Dagster `deps` records lineage; it does not pass an in-memory value that would force an upstream rebuild. A narrow job therefore reads the exact existing upstream artifact and fails when it is missing or its content hash changed.

Linkouts staging is content-addressed by normalized release key, KGX inputs, dictionary, codec settings, and worker version. Publication consumes a completed unfiltered manifest. Retry resumes only an exact match. Changed inputs select a different staging namespace.

## Workflow rules

1. Do not import implementation code across independent Dagster projects.
2. Keep Dagster out of `linkouts_pipeline` and other pipeline packages.
3. Keep Go modules independent; there is no root `go.work`.
4. Publish KGX NDJSON from KG-building DAGs. Only Linkouts writes the compressed serving envelope.
5. Keep credentials in the worker or Phoenix process environment, never in Pipes extras, run configuration, metadata, or committed files.
6. Keep normal uploads manual. Destructive jobs are absent unless explicitly enabled and confirmed.

## Runtime shape

The Linkouts Go worker joins node names onto KGX edges, normalizes values, compresses canonical versioned documents, and maintains random pools. Phoenix performs point reads by document ID and renders those documents through `sites/linkouts/kgs/*.exs`. The cross-language contract is tested byte for byte.

Documentation is the only static application. Phoenix remains a dynamic LiveView service with clustered caches and no SQL database.

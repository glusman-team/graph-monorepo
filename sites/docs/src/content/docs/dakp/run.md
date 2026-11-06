---
title: Run the DAKP pipeline
description: Build the complete DAKP knowledge graph or one published stage artifact.
sidebar:
  order: 1
---

DAKP builds a knowledge graph. Its publication format is validated KGX NDJSON, not the Linkouts serving envelope.

## Start Dagster

From the repository root:

```sh
make dev
```

Open `http://localhost:3000`, select the `dakp` code location, and launch `dakp_build` for the complete graph or `dakp_build_<stage>` for one output.

Build only `kg_build` from a shell:

```sh
cd dagster/projects/dakp
uv run dagster job execute -j dakp_build_kg_build -m dakp_dag.definitions
```

Equivalent asset commands:

```sh
uv run dagster asset materialize --select kg_build -m dakp_dag.definitions
uv run dagster asset materialize --select '*+kg_build' -m dakp_dag.definitions
```

The first command builds one output. The second selects the complete upstream chain.

## Stage boundaries

Stage-to-stage handoff is a published artifact under `$MO_PUBLISHED_ROOT`, never a Dagster value input. Each stage declares lineage-only dependencies and has a narrow job. A narrow run reads the existing upstream artifact and fails with `upstream artifact missing` if it has not been published.

Artifact references live at `dakp/refs/<stage>.refs.json`; the final KGX pair is under `dakp/kg/` within the configured published root.

Stages include:

- `acquire_<source>` for DailyMed, FAERS, Drugs@FDA, EMA, EMA SmPC, Canada Vigilance, ICD-10-CM, and NER models;
- `extract_<source>`;
- `shape_approved_treats`, `shape_faers_uses`, `shape_contraindications`, and `shape_has_active_ingredient`;
- `tablassert_configs`, `kg_build`, and `kg_qc`.

## Process configuration

Environment variables are resolved in `src/dakp_dag/config.py` at materialization time:

| Variable                                   | Role                                                                                   |
| ------------------------------------------ | -------------------------------------------------------------------------------------- |
| `MO_PUBLISHED_ROOT`                        | Published artifact root shared with other code locations.                              |
| `DAKP_WORKDIR`                             | BLAKE3 content-addressed storage and operation index.                                  |
| `DAKP_EXTRACT_WORKER_BIN`                  | Hidden Go Pipes worker binary.                                                         |
| `DAKP_FULLMAP`                             | Identifier mapping input. Without it, the Tablassert handoff is deferred and reported. |
| `DAKP_FORCE`                               | Bypass operation-index reuse.                                                          |
| `DAKP_NER_OFFLINE`                         | Gazetteer-only, torch-free NER when set to `1`.                                        |
| `DAKP_THREADS`, `DAKP_MEMORY_BUDGET_GB`    | Worker resource limits.                                                                |
| `DAKP_QUARTER_LIMIT`, `DAKP_RELEASE_LIMIT` | Bounded source acquisition for development.                                            |
| `DAKP_PUBLICATION_VERSION`                 | Explicit release version.                                                              |
| `DAKP_SOURCE_MAX_AGE_DAYS`                 | Freshness limit for selected re-downloads.                                             |
| `DAKP_ICD10CM_URL`                         | Optional override for the fiscal-year-pinned ICD-10-CM archive.                        |

Keep credentials and machine-specific values outside Git. Do not put secrets in Dagster metadata or run configuration.

## Verify a release

Before publication, inspect `kg_qc`, the KGX validation output, and the poison/water screens. The ICD-10-CM snapshots under `pipeline/src/dakp_pipeline/` are offline defaults and regression baselines; their only writers are the evaluation scripts under `pipeline/tests/eval/`. Do not hand-edit those snapshots.

Use the repository's `release-dakp` agent skill for the explicit Hugging Face upload, dataset card, pruning, and tag procedure.

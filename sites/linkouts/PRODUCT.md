# Linkouts product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: Translator consortium researchers and curators. They arrive with an edge UUID (or via a random/KG entry point) to check what a knowledge-graph edge asserts, what evidence backs it, where it came from, and how it changed across KG releases.

Secondary audience: operators who run the Dagster upload jobs and maintain the display configs. Data consumers and reviewers read the pages but do not administer them.

## Product Purpose

Linkouts turns raw knowledge-graph edges (KGX `edges.ndjson` + `nodes.ndjson` releases) into permanent, human-readable pages: one URL per edge showing the assertion, its evidence and provenance, and a per-release diff of how the edge changed. Success means a curator can answer "what does this edge claim and why should I believe it" from the page alone, for every release of every supported KG, at near-zero read cost.

## Positioning

A neighboring tool could copy the page layout but not the mechanism: each edge's full version history lives in a single zstd blob (trained-dictionary compression) fetched by one point read against a Cosmos container with indexing mode `none`, with all naming, ordering, and layout derived server-side in Phoenix. Storage holds raw facts only; nothing is precomputed, indexed, or queried. Adding a KG is a declarative `.exs` display config in `kgs/`, not code. Ingestion is a manually launched Dagster pipeline with a content-addressed, resumable stage - not a public CLI.

## Operating Context

- Data arrives as KGX releases produced by Tablassert; `--kg`/`--version` are inferred from its filenames, with the canonical `infores:` form (`infores:drugapprovals-kp-1.23.3`) accepted on stage and the prefix-less slug (`drugapprovals-kp`) stored on documents and used in URLs.
- Live at https://linkouts.skyelanegoetz.com behind Cloudflare (origin lockdown via `X-Origin-Key`) on Fly.io; production deploys require green CI for the selected commit and touch `sites/linkouts/`, `dagster/projects/linkouts/`, deployment configs, or the monorepo Docker context.
- Entry points: `/` (KG list with release pills), `/edges/:uuid` (edge page with per-release toggle and diff), `/random`, `/<kg>/random`, `/<kg>/random?version=<label>`.
- Read path never queries Cosmos: point reads by id, coalesced and cached, under a strict `RU_BUDGET_WEB` so traffic cannot starve ingestion (`RU_BUDGET_CLI` caps the worker).
- Dev workflow is monorepo `make` targets (`make linkouts-web-setup`, `make linkouts-lint`, `make linkouts-contract-test`, `make linkouts-worker-build`); secrets come from the environment/Fly, never the repo.

## Capabilities and Constraints

- Web: Phoenix LiveView app in `sites/linkouts/`; per-KG display behavior driven by `kgs/*.exs` (`_default.exs` fallback); no database queries, no indexed reads.
- Ingestion: `dagster/projects/linkouts/` (typed Python stages) plus the hidden argument-free Go Pipes worker in `workers/go/`; stage, publish (resumable, content-addressed), status, probe; dictionary training, inspection, verification, and provisioning are narrow separate jobs.
- Terminology is fixed by the domain: `infores:` canonical names, KG slugs, KGX node/edge records, RU budgeting.
- Undecided: whether random/discovery surfaces grow beyond curation use.

## Brand Commitments

- Name: linkouts (live domain linkouts.skyelanegoetz.com).
- Must read as vaguely associated with ISB/Translator.
- Do NOT use official Translator assets (logo, brand kit) - explicit user constraint, "yet" implies this may change but this is not authorized now.
- No other voice, palette, or identity constraints are binding; `DESIGN.md` records the design system in force.

## Evidence on Hand

- Live deployment serving `infores:drugapprovals-kp` releases 1.23.3 and 1.23.4.
- DAKP sample data for local load (file store, no Cosmos account).
- `sites/docs/` (task-first guides, operation runbooks, ADRs) records the real architecture decisions; `DESIGN.md` records the visual system.
- No testimonials, marketing claims, or benchmarks exist; future work must not fabricate them.

## Product Principles

1. Evidence and citation accuracy outrank presentation: a page that misstates an assertion or its provenance is a defect, whatever it looks like.
2. Every release of an edge stays inspectable: version history and the cross-release diff are the product, not a feature of it.
3. Cost discipline is a feature: minimal storage, point reads only, hard RU budgets are part of the design contract, not implementation detail.
4. Adding a KG is data, not code: new graphs ship as declarative `kgs/*.exs` configs.
5. Ingestion is explicit: a human launches the upload; no schedule guesses when a release is ready.

## Accessibility & Inclusion

No product-specific accessibility standard has been established (open decision; default web good practice applies until the user sets one).

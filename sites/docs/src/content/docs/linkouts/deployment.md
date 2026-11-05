---
title: Deploy Linkouts
description: Build the production image, operate the isolated preview, and deploy Phoenix safely behind Cloudflare.
sidebar:
  order: 21
---

The Phoenix app runs on Fly.io behind Cloudflare and normally reads Azure Cosmos DB. Everything in the credentialed sections changes real infrastructure or can incur cost. None of it belongs to the offline verification matrix.

## Build and validate without deploying

From the monorepo root:

```sh
python3 sites/linkouts/scripts/check_deploy.py
docker build -f sites/linkouts/Dockerfile -t linkouts:local .
```

The Dockerfile uses the repository-root context but copies only `sites/linkouts`. Both base images are pinned by digest, compilation uses warnings as errors, and the release runs as the non-root `app` user. The root `.dockerignore` allowlist prevents unrelated local data and credentials reaching a remote builder.

The structural check validates both Fly TOML files, the Dockerfile path, health checks, the canonical preview fixture path, and the absence of preview credentials. `flyctl config validate` requires a Fly access token, so it runs only in the authorized deployment workflow.

## Isolated public preview

`sites/linkouts/fly.dev.toml` targets `edge-linkouts-dev`, not production. It runs the production release with `LINKOUTS_PREVIEW_FIXTURES=/app/preview-docs.ndjson`. Fly injects only `dagster/projects/linkouts/contracts/docs.ndjson`, resolved relative to the TOML file.

The preview has no Cosmos credentials, production cluster membership, production origin key, or private data. It displays a "DEV preview" notice and `noindex, nofollow` metadata. Never run Phoenix's `MIX_ENV=dev` debug server publicly.

First-time authorized setup:

```sh
flyctl apps create edge-linkouts-dev --org personal
python3 -c 'import secrets; print("SECRET_KEY_BASE=" + secrets.token_hex(64))' |
  flyctl secrets import -a edge-linkouts-dev
```

Deploy from the repository root:

```sh
flyctl config validate -c sites/linkouts/fly.dev.toml
flyctl deploy -c sites/linkouts/fly.dev.toml --remote-only --ha=false .
curl -fsS https://edge-linkouts-dev.fly.dev/healthz
```

The VM stops when idle and starts on request. The workflow's manual `preview` target also requires a successful CI run for the selected commit.

## Production order

### 1. Publish and verify data

Use the [upload guide](../operations/upload/) and the [dictionary rollout](../operations/dictionaries/) when applicable. The Phoenix deployment does not ingest data.

### 2. Create the Fly application

For first-time authorized setup:

```sh
flyctl apps create edge-linkouts --org personal
```

`lax` is configured as the primary region. Confirm current region and account requirements before creation.

### 3. Set production secrets

The web app receives only the Cosmos read-only key. The read-write key belongs to the worker and must never reach Phoenix.

```sh
fly secrets set -a edge-linkouts \
  SECRET_KEY_BASE=... \
  COSMOS_ENDPOINT=... \
  COSMOS_DB=edge_linkouts \
  COSMOS_CONTAINER=edges \
  COSMOS_READ_ONLY_KEY=... \
  X_ORIGIN_KEY=...
```

`COSMOS_PRIMARY_CONNECTION_STRING_R` is accepted instead of separate endpoint/key variables. `X_ORIGIN_KEY` is the origin-lockdown secret Cloudflare sends as `X-Origin-Key`; `/healthz` is the sole exempt route. A production boot without required secrets fails rather than serving an empty site. `PHX_HOST` is public configuration in `fly.toml`.

### 4. Deploy through CI

Pushes to `main` that change `sites/linkouts`, the canonical contracts, `.dockerignore`, or the deployment workflow deploy only after the CI workflow succeeds for that commit. The workflow compares deployment inputs and skips unrelated successful CI runs.

Manual production or preview dispatch also verifies a successful CI run for the selected SHA. Environment protection can require an additional authorized approval. The deployment command is:

```sh
flyctl deploy --remote-only --config sites/linkouts/fly.toml .
```

No live deployment is performed by repository tests.

### 5. Configure Cloudflare and TLS

Create the proxied CNAME for `linkouts.skyelanegoetz.com`, use SSL mode **Full (strict)**, and configure the origin transform rule for `X-Origin-Key`. LiveView requires WebSocket upgrades. Add the Fly certificate through the authorized account procedure and verify the public hostname, not only `edge-linkouts.fly.dev`.

## Health and liveness

`/healthz` returns `200 ok` without touching Cosmos. Fly checks it every 10 seconds with a 2-second timeout and 10-second grace period. It proves the node can serve, not that Cosmos is reachable; a dependency outage must not cause a restart loop.

After deployment, separately verify `/`, a known edge, random selection, and LiveView interactions through Cloudflare.

## Throughput budget

The shared free-tier account provides 1000 RU/s. The worker defaults to 900 RU/s and Phoenix to 100 RU/s. Both use measured Cosmos charges for pacing. Do not raise the web budget during an upload; page reads may wait while ingestion runs, and the combined ceiling protects both applications.

Use `status_report` and `probe_release` to inspect storage and measure actual point-read cost before changing limits. The container indexing policy remains `none`.

## Clustering

One node is a valid cluster of one. `CLUSTER_QUERY=edge-linkouts.internal` enables DNS discovery, while `sites/linkouts/rel/env.sh.eex` names nodes `<app>@<fly-private-ip>` over IPv6. To scale during an authorized maintenance operation:

```sh
fly secrets set -a edge-linkouts RELEASE_COOKIE=<random>
fly scale count 2 -a edge-linkouts
```

The partitioned cache, owner-routed read coalescing, and per-node RU budget division follow automatically. Keep `RELEASE_COOKIE` secret. See [ADR 4](../adrs/0004-clustering/).

## Replace or recover stored data

Use a new container, publish and verify all releases, then flip `COSMOS_CONTAINER` through the authorized deployment process. Keep the old container during the rollback window. The [recovery guide](../operations/recovery/) describes the full sequence and why ad-hoc production repair scripts are not supported.

For dictionary changes, deploy the complete old-and-new reader registry before publishing envelopes with a new ID. See [dictionary rollout](../operations/dictionaries/).

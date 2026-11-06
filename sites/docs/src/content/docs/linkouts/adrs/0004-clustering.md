---
title: "ADR 4: Cluster-ready read path"
description: "libcluster and nebulex_distributed for the Phoenix read path."
sidebar:
  order: 4
---

Status: accepted. The app runs on one Fly machine today; this ADR makes adding a second
machine a no-code-change operation (scale the machine count, set one secret) instead of an
architecture change.

## Decision

- Node discovery: `libcluster` `Cluster.Strategy.DNSPoll` against `CLUSTER_QUERY`
  (`edge-linkouts.internal` in fly.toml). The topology is only configured when that env is
  set, so dev and test never start it.
- Cache: `EdgeLinkouts.Cache` uses `Nebulex.Adapters.Partitioned` over a Local primary.
  Every key has exactly one owner node; a miss on a non-owner is one ring lookup + one
  intra-cluster call instead of one Cosmos read per node.
- Coalescing: `EdgeLinkouts.Dedupe.execute/4` accepts an MFA tuple. The tuple routes to the
  key's ring owner, so the whole cluster shares one in-flight backend read per id.
  Anonymous closures stay local: a closure carries the caller's compiled module version and
  sending one across a mixed-version cluster (rolling deploy) is undefined behavior.
- RU budget: `EdgeLinkouts.RateLimiter` stays per-node (no coordination to fail) and
  divides the configured budget by `EdgeLinkouts.Cluster.node_count/0`, a live count of the
  `:pg` group `:edge_linkouts_nodes` kept in an atomics cell. Strict total at any N; a join
  or leave mis-splits by one second at worst.
- Node naming: `sites/linkouts/rel/env.sh.eex` sets `RELEASE_NODE` to `<app>@<fly-private-ip>` with
  `inet6_tcp` when `FLY_PRIVATE_IP` is present; local release runs are untouched.

## Why not the alternatives

- Multilevel (L1 local + L2 partitioned): duplicates memory and adds a hop at N=1 for zero
  RU saving. The ring of one already makes Partitioned a pure local cache.
- Global name registration for single-flight (`:global`): known netsplit behavior (split
  brains run the fun twice or block); the ring owner is a cleaner rendezvous.
- `Node.list/0` for the budget divisor: it includes ad-hoc nodes like `fly ssh console`
  sessions, which would shrink the per-node budget while holding no traffic. The `:pg`
  group counts only nodes that started the app.

## Failure modes (accepted, by design)

- Owner unreachable mid-call: `:erpc.call` signals failures as class `:error`
  (`{:erpc, :noconnection}`, `{:erpc, :timeout}`, a wrapped remote raise) and a dying link
  as `:exit`; the fallback catches all three classes and the read runs locally. A partition
  costs duplicate reads, never an error page. Covered by the dead-owner test in
  `cluster_two_node_test.exs`, which fails against an `:exit`-only catch.
- Rolling deploy where the old version lacks `Dedupe.run_local/4`: same fallback.
- Ring not yet converged at boot: `find_node` errors are caught and treated as "local".
- A `fly ssh console` node never joins the `:pg` group and never takes traffic or budget.

## Why not batching or PubSub for the read path

- Multi-item transactional batches are impossible: the container's partition key is `/id`,
  and a Cosmos transaction cannot span partition keys, so N edges are N transactions.
- `ReadMany` is query-backed and bills as a query, not as N cheaper point reads; it saves
  round-trips, not RU, and the read path is already one point read per page view (the
  LiveView keeps the decoded blob in its assigns, so a `?version=` switch costs zero).
- Phoenix.PubSub cannot coalesce (a broadcast is fan-out, not single-flight) and there is
  TTL classes bound stale reads after publication; the read model needs no synchronous
  cross-node invalidation.

## Scaling up (the whole runbook)

1. `fly secrets set RELEASE_COOKIE=<random>` (releases read it natively at boot).
2. `fly scale count 2`.
3. Nothing else: DNSPoll finds the peer, the ring splits the keyspace, the limiter halves
   each node's budget automatically. RU total stays at `RU_BUDGET_WEB`.

## Verification

- `sites/linkouts/test/edge_linkouts/cluster_test.exs`: single-node behavior (ring of one, MFA
  executes locally, count is 1).
- `sites/linkouts/test/edge_linkouts/cluster_two_node_test.exs` (tagged `:cluster`, excluded by
  default; run `mix test --only cluster`): two real nodes on loopback prove one backend
  read for 20 concurrent callers of the same id across both nodes, and that the membership
  count tracks a joining peer. Peer services boot via `test/support/cluster_peer.ex`
  (support beams land in ebin, so the peer can load them; test modules cannot).

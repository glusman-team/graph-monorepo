defmodule EdgeLinkouts.Cache do
  @moduledoc """
  The in-memory cache behind `EdgeLinkouts.Dedupe`'s recent-results replay.

  Every Cosmos read funnels through the web read path and is coalesced by
  `EdgeLinkouts.Dedupe`; successful results are stored here with a per-class TTL:

  - **edge documents** — 30 s (`:dedupe_ttl_ms`). A page view right after `linkouts_upload`
    must show the release that was just loaded.
  - **pool index / one release's pool** — 15 min (`:pool_ttl_ms`, set per read by `Edges`).
    Those documents are rewritten only when the worker loads a release.
  - **not-found** — briefly (`:negative_ttl_ms`, default 10 s), so dead-link traffic costs
    nothing, without a long-ttl override being able to pin a 404.

  `{:ok, _}` and `{:error, :not_found}` are the only results ever stored: a throttle or an
  outage is retried, not replayed.

  The adapter is `Nebulex.Adapters.Local`: generational ETS with reads promoting hot
  entries into the newer generation, so eviction is recency-aware (a full table evicts
  instead of silently refusing to store, which the old hand-rolled table did). Size is
  bounded by `:max_size` entries and `:allocated_memory` bytes; `gc_interval` sets the
  generation length. Hit/miss counters are available from `iex` with
  `EdgeLinkouts.Cache.info!(:stats)` — callable observability, no dashboard, matching
  `EdgeLinkouts.RateLimiter.stats/1`.

  Single node by design, like everything else on this read path.
  """

  # Partitioned over a local primary: at one node the ring holds only this node and every
  # operation is the old local behavior; the moment a second node joins, each key has one
  # owner and the whole cluster shares the cached result instead of re-reading Cosmos
  # (the owner is also where `EdgeLinkouts.Dedupe` coalesces the miss). Multilevel L1+L2 was
  # rejected at N=1: it duplicates memory and adds a hop for zero RU saving - see ADR 0004.
  use Nebulex.Cache,
    otp_app: :edge_linkouts,
    adapter: Nebulex.Adapters.Partitioned
end

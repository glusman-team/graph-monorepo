# This file is responsible for configuring your application
# and its dependencies with the aid of the Config module.
#
# This configuration file is loaded before any dependency and
# is restricted to this project.

# General application configuration
import Config

config :edge_linkouts,
  generators: [timestamp_type: :utc_datetime]

# Cosmos read path. Backends and endpoints are re-selected at runtime in config/runtime.exs
# (prod requires the read-only key; dev falls back to the file backend when no credentials
# are configured; tests use EdgeLinkouts.Cosmos.Fake).
config :edge_linkouts,
  cosmos_backend: EdgeLinkouts.Cosmos.HTTP,
  # Compile-time fallback; runtime.exs owns the operative split of the free tier's
  # 1000 RU/s: 10% here, 90% for the worker during ingestion (RU_BUDGET_CLI).
  ru_budget_web: 150,
  # Point-read cost estimate used by RateLimiter.allow?/2 before the call. The actual
  # x-ms-request-charge is reconciled afterwards on every response.
  cosmos_estimated_read_ru: 10,
  # Bound on how long Dedupe waiters wait for a leader before reading directly.
  dedupe_wait_ms: 500,
  # How long a pool index / one release's pool may be replayed (see EdgeLinkoutsWeb.Edges).
  pool_ttl_ms: 900_000

# The recent-results cache behind EdgeLinkouts.Dedupe (see that module and EdgeLinkouts.Cache).
config :edge_linkouts, EdgeLinkouts.Cache,
  # The partitioned adapter's local store: per-node primary storage with the same eviction
  # bounds the old local-only cache had. The distributed layer adds no memory at N=1.
  primary: [
    # Generation length: equal to the longest TTL class, so a 15 min entry dies with its
    # generation at the latest; shorter classes expire on read long before that.
    gc_interval: :timer.minutes(15),
    # Same bound the old hand-rolled table had, now with real eviction behind it.
    max_size: 10_000,
    # Starting guess for a dev-sized machine; revisit when the Fly machine size is chosen.
    allocated_memory: 64 * 1024 * 1024,
    gc_memory_check_interval: :timer.seconds(10)
  ]

# Configure the endpoint
config :edge_linkouts, EdgeLinkoutsWeb.Endpoint,
  url: [host: "localhost"],
  adapter: Bandit.PhoenixAdapter,
  render_errors: [
    formats: [html: EdgeLinkoutsWeb.ErrorHTML, json: EdgeLinkoutsWeb.ErrorJSON],
    layout: false
  ],
  pubsub_server: EdgeLinkouts.PubSub,
  live_view: [signing_salt: "9G7ur/24"]

# Configure LiveView
config :phoenix_live_view,
  # the attribute set on all root tags. Used for Phoenix.LiveView.ColocatedCSS.
  root_tag_attribute: "phx-r"

# Configure esbuild (the version is required)
config :esbuild,
  version: "0.25.4",
  edge_linkouts: [
    args:
      ~w(js/app.js --bundle --target=es2022 --outdir=../priv/static/assets/js --external:/fonts/* --external:/images/* --alias:@=.),
    cd: Path.expand("../assets", __DIR__),
    env: %{"NODE_PATH" => [Path.expand("../deps", __DIR__), Mix.Project.build_path()]}
  ]

# Configure tailwind (the version is required)
config :tailwind,
  version: "4.3.3",
  edge_linkouts: [
    args: ~w(
      --input=assets/css/app.css
      --output=priv/static/assets/css/app.css
    ),
    cd: Path.expand("..", __DIR__),
    env: %{"NODE_PATH" => [Path.expand("../deps", __DIR__), Mix.Project.build_path()]}
  ]

# Configure Elixir's Logger
config :logger, :default_formatter,
  format: "$time $metadata[$level] $message\n",
  metadata: [:request_id]

# Use Jason for JSON parsing in Phoenix
# The built-in JSON module (Elixir 1.18+) — no jason dependency anywhere.
config :phoenix, :json_library, JSON

# Import environment specific config. This must remain at the bottom
# of this file so it overrides the configuration defined above.
import_config "#{config_env()}.exs"

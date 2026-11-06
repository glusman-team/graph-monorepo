import Config

# Cluster discovery (inert when unset): CLUSTER_QUERY is a DNS name that resolves to the
# peer nodes' addresses - on Fly, "<app>.internal" from fly.toml. node_basename is the
# node-name prefix before the address. See sites/linkouts/rel/env.sh.eex for how the node name and the
# release cookie are set in production.
if query = System.get_env("CLUSTER_QUERY") do
  config :libcluster,
    topologies: [
      fly: [
        strategy: Cluster.Strategy.DNSPoll,
        config: [
          query: query,
          node_basename:
            System.get_env("CLUSTER_NODE_BASENAME") ||
              System.get_env("FLY_APP_NAME") || "edge_linkouts"
        ]
      ]
    ]
end

# config/runtime.exs is executed for all environments, including
# during releases. It is executed after compilation and before the
# system starts, so it is typically used to load production configuration
# and secrets from environment variables or elsewhere. Do not define
# any compile-time configuration in here, as it won't be applied.
# The block below contains prod specific runtime configuration.

# ## Using releases
#
# If you use `mix release`, you need to explicitly enable the server
# by passing the PHX_SERVER=true when you start it:
#
#     PHX_SERVER=true bin/edge_linkouts start
#
# Alternatively, you can use `mix phx.gen.release` to generate a `bin/server`
# script that automatically sets the env var above.
if System.get_env("PHX_SERVER") do
  config :edge_linkouts, EdgeLinkoutsWeb.Endpoint, server: true
end

config :edge_linkouts, EdgeLinkoutsWeb.Endpoint,
  http: [port: String.to_integer(System.get_env("PORT", "4000"))]

# Web app's slice of the free tier's 1000 RU/s: 10% here, 90% for the worker during
# ingestion (RU_BUDGET_CLI). 900 + 100 is the whole tier, so while a load runs the app
# is at its ceiling and page reads can wait; outside a load it idles far below this.
config :edge_linkouts,
  ru_budget_web: String.to_integer(System.get_env("RU_BUDGET_WEB", "100"))

if directory = System.get_env("ZSTD_DICT_DIR") do
  config :edge_linkouts, zstd_dict_dir: directory
end

# A separate public DEV preview runs a production release with only bundled sample data.
# This never enables Phoenix's development error pages or needs Cosmos credentials.
preview_fixtures =
  case System.get_env("LINKOUTS_PREVIEW_FIXTURES") do
    value when is_binary(value) and value != "" -> value
    _ -> nil
  end

config :edge_linkouts,
  fixture_preview: config_env() == :prod and is_binary(preview_fixtures)

# Cache TTL classes, env-tunable. Set only when the variable is present so that
# config/test.exs' dedupe_ttl_ms: 0 (caching off in tests) is not clobbered.
if dedupe_ttl = System.get_env("DEDUPE_TTL_MS") do
  config :edge_linkouts, dedupe_ttl_ms: String.to_integer(dedupe_ttl)
end

if pool_ttl = System.get_env("POOL_TTL_MS") do
  config :edge_linkouts, pool_ttl_ms: String.to_integer(pool_ttl)
end

# Connection-string fallbacks below go through
# EdgeLinkouts.Cosmos.HTTP.connection_string_key/2: each `;`-separated pair is split on
# the first "=" only, because base64 account keys end in "=" padding.

if config_env() == :dev do
  # Reload browser tabs when matching files change.
  config :edge_linkouts, EdgeLinkoutsWeb.Endpoint,
    live_reload: [
      web_console_logger: true,
      patterns: [
        # Static assets, except user uploads
        ~r"priv/static/(?!uploads/).*\.(js|css|png|jpeg|jpg|gif|svg)$"E,
        # Router, Controllers, LiveViews and LiveComponents
        ~r"lib/edge_linkouts_web/router\.ex$"E,
        ~r"lib/edge_linkouts_web/(controllers|live|components)/.*\.(ex|heex)$"E
      ]
    ]

  # Cosmos in dev: the real read-only client when credentials are configured (or
  # COSMOS_BACKEND=http forces it), otherwise the NDJSON file backend against the worker's
  # file store (COSMOS_DOCS=/tmp/edges.ndjson), which starts empty when unset.
  dev_key =
    System.get_env("COSMOS_READ_ONLY_KEY") ||
      EdgeLinkouts.Cosmos.HTTP.connection_string_key(
        System.get_env("COSMOS_PRIMARY_CONNECTION_STRING_R"),
        "AccountKey"
      )

  dev_endpoint =
    System.get_env("COSMOS_ENDPOINT") ||
      EdgeLinkouts.Cosmos.HTTP.connection_string_key(
        System.get_env("COSMOS_PRIMARY_CONNECTION_STRING_R"),
        "AccountEndpoint"
      )

  # An explicit COSMOS_BACKEND=file wins even when cloud credentials are in the environment:
  # direnv exports them, and a local click-through must not quietly read the real account.
  file_backend = fn ->
    config :edge_linkouts,
      cosmos_backend: EdgeLinkouts.Cosmos.File,
      cosmos_file: System.get_env("COSMOS_DOCS")
  end

  cond do
    System.get_env("COSMOS_BACKEND") == "file" ->
      file_backend.()

    System.get_env("COSMOS_BACKEND") == "http" or (dev_endpoint && dev_key) ->
      unless dev_endpoint && dev_key do
        raise "COSMOS_BACKEND=http needs COSMOS_ENDPOINT and COSMOS_READ_ONLY_KEY (or COSMOS_PRIMARY_CONNECTION_STRING_R)"
      end

      config :edge_linkouts,
        cosmos_backend: EdgeLinkouts.Cosmos.HTTP,
        # A map, not a keyword list: EdgeLinkouts.Cosmos.HTTP reads it with map access, and a
        # keyword list made the first request crash with BadMapError.
        cosmos_http: %{
          endpoint: dev_endpoint,
          db: System.get_env("COSMOS_DB", "edge_linkouts"),
          container: System.get_env("COSMOS_CONTAINER", "edges"),
          key: dev_key
        }

    true ->
      file_backend.()
  end
end

if config_env() == :prod do
  # The secret key base is used to sign/encrypt cookies and other secrets.
  # A default value is used in config/dev.exs and config/test.exs but you
  # want to use a different value for prod and you most likely don't want
  # to check this value into version control, so we use an environment
  # variable instead.
  secret_key_base =
    System.get_env("SECRET_KEY_BASE") ||
      raise """
      environment variable SECRET_KEY_BASE is missing.
      You can generate one by calling: mix phx.gen.secret
      """

  host = System.get_env("PHX_HOST") || "example.com"

  # Origin lockdown key (Cloudflare Transform Rule adds the header on every proxied
  # request). Copied from the environment here - once, at boot - instead of being read
  # per request, so tests can never see a stray shell variable and the plug can compare
  # in constant time. Unset means the check is inert.
  if origin_key = System.get_env("X_ORIGIN_KEY") do
    config :edge_linkouts, origin_check_key: origin_key
  end

  if preview_fixtures do
    config :edge_linkouts,
      cosmos_backend: EdgeLinkouts.Cosmos.File,
      cosmos_file: preview_fixtures
  else
    # Cosmos read-only access (see ADR 0002 on the docs site). The web app is
    # read-only and sees the read-only key only; the read-write key never reaches it.
    cosmos_endpoint =
      System.get_env("COSMOS_ENDPOINT") ||
        EdgeLinkouts.Cosmos.HTTP.connection_string_key(
          System.get_env("COSMOS_PRIMARY_CONNECTION_STRING_R"),
          "AccountEndpoint"
        ) ||
        raise """
        environment variable COSMOS_ENDPOINT is missing.
        Set it to the Cosmos account endpoint, e.g. https://<account>.documents.azure.com:443/
        (or export COSMOS_PRIMARY_CONNECTION_STRING_R; the endpoint and key are read from it).
        """

    cosmos_key =
      System.get_env("COSMOS_READ_ONLY_KEY") ||
        EdgeLinkouts.Cosmos.HTTP.connection_string_key(
          System.get_env("COSMOS_PRIMARY_CONNECTION_STRING_R"),
          "AccountKey"
        ) ||
        raise """
        environment variable COSMOS_READ_ONLY_KEY is missing.
        Azure Portal -> your Cosmos account -> Settings -> Keys -> "Primary or secondary
        read-only keys". Never set the read-write key here: the web app cannot write and
        must not be able to.
        """

    config :edge_linkouts,
      cosmos_backend: EdgeLinkouts.Cosmos.HTTP,
      cosmos_http: %{
        endpoint: cosmos_endpoint,
        db: System.get_env("COSMOS_DB", "edge_linkouts"),
        container: System.get_env("COSMOS_CONTAINER", "edges"),
        key: cosmos_key
      }
  end

  config :edge_linkouts, EdgeLinkoutsWeb.Endpoint,
    url: [host: host, port: 443, scheme: "https"],
    http: [
      # Enable IPv6 and bind on all interfaces.
      # Set it to  {0, 0, 0, 0, 0, 0, 0, 1} for local network only access.
      # See the documentation on https://bandit.hexdocs.pm/Bandit.html#t:options/0
      # for details about using IPv6 vs IPv4 and loopback vs public addresses.
      ip: {0, 0, 0, 0, 0, 0, 0, 0}
    ],
    secret_key_base: secret_key_base

  # ## SSL Support
  #
  # To get SSL working, you will need to add the `https` key
  # to your endpoint configuration:
  #
  #     config :edge_linkouts, EdgeLinkoutsWeb.Endpoint,
  #       https: [
  #         ...,
  #         port: 443,
  #         cipher_suite: :strong,
  #         keyfile: System.get_env("SOME_APP_SSL_KEY_PATH"),
  #         certfile: System.get_env("SOME_APP_SSL_CERT_PATH")
  #       ]
  #
  # The `cipher_suite` is set to `:strong` to support only the
  # latest and more secure SSL ciphers. This means old browsers
  # and clients may not be supported. You can set it to
  # `:compatible` for wider support.
  #
  # `:keyfile` and `:certfile` expect an absolute path to the key
  # and cert in disk or a relative path inside priv, for example
  # "priv/ssl/server.key". For all supported SSL configuration
  # options, see https://plug.hexdocs.pm/Plug.SSL.html#configure/1
  #
  # We also recommend setting `force_ssl` in your config/prod.exs,
  # ensuring no data is ever sent via http, always redirecting to https:
  #
  #     config :edge_linkouts, EdgeLinkoutsWeb.Endpoint,
  #       force_ssl: [hsts: true]
  #
  # Check `Plug.SSL` for all available options in `force_ssl`.
end

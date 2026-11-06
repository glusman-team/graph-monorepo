defmodule EdgeLinkouts.Application do
  # See https://elixir.hexdocs.pm/Application.html
  # for more information on OTP Applications
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    # Dictionaries before anything that reads: the registry is the decode path for every
    # stored document, and a boot that cannot read its dictionaries must fail here.
    EdgeLinkouts.Dicts.reload!()

    # libcluster only starts when CLUSTER_QUERY configured a topology in runtime.exs
    # (fly.toml); with none configured the supervisor is empty and the app is a cluster of
    # one either way. EdgeLinkouts.Cluster (the :pg membership count the RU limiter divides
    # by) runs regardless: at one node it simply reports 1.
    cluster_supervisor =
      case Application.get_env(:libcluster, :topologies, []) do
        [] -> []
        topologies -> [{Cluster.Supervisor, [topologies]}]
      end

    children =
      [
        {Phoenix.PubSub, name: EdgeLinkouts.PubSub},
        # One connection pool for Cosmos point reads (see EdgeLinkouts.Cosmos.Finch).
        {Finch, name: EdgeLinkouts.Cosmos.Finch, pools: %{:default => [size: 16, count: 1]}},
        # Read path support: file backend (dev), RU budget, result cache, in-flight read coalescing.
        EdgeLinkouts.Cluster,
        EdgeLinkouts.Cosmos.File,
        EdgeLinkouts.RateLimiter,
        EdgeLinkouts.Cache,
        EdgeLinkouts.Dedupe,
        # Start to serve requests, typically the last entry
        EdgeLinkoutsWeb.Endpoint
      ] ++ cluster_supervisor ++ test_children()

    # See https://elixir.hexdocs.pm/Supervisor.html
    # for other strategies and supported options
    opts = [strategy: :one_for_one, name: EdgeLinkouts.Supervisor]
    Supervisor.start_link(children, opts)
  end

  # The in-memory Cosmos fake is programmable per test, so it runs only in the test env
  # (config :edge_linkouts, :start_cosmos_fake in config/test.exs).
  defp test_children do
    if Application.get_env(:edge_linkouts, :start_cosmos_fake, false) do
      [EdgeLinkouts.Cosmos.Fake]
    else
      []
    end
  end

  # Tell Phoenix to update the endpoint configuration
  # whenever the application is updated.
  @impl true
  def config_change(changed, _new, removed) do
    EdgeLinkoutsWeb.Endpoint.config_change(changed, removed)
    :ok
  end
end

defmodule EdgeLinkouts.ClusterPeer do
  @moduledoc """
  Helpers executed ON a peer node in the two-node cluster tests
  (`test/edge_linkouts/cluster_two_node_test.exs`).

  Lives in test/support so its beam is compiled into ebin and a `:peer` node can load it
  from the code path; modules in test/*.exs are only required into the test VM's memory.
  """

  alias EdgeLinkouts.{Cache, Cluster, Dedupe}

  @doc """
  Starts the minimal read-path stack under one long-lived process. Detached on purpose:
  any rpc/erpc worker exits after the call, and a service start_link'ed to such a worker
  would die with it.
  """
  def boot do
    spawn(fn ->
      # A :peer VM boots with kernel+stdlib only; the cache stack needs its app processes
      # there too (telemetry handlers, the Nebulex registry).
      for app <- [:telemetry, :nebulex, :nebulex_distributed] do
        {:ok, _} = Application.ensure_all_started(app)
      end

      {:ok, _} = Cache.start_link(name: Cache)
      {:ok, _} = Dedupe.start_link(name: Dedupe)
      {:ok, _} = Cluster.start_link([])
      Process.sleep(:infinity)
    end)

    :ok
  end

  @doc """
  MFA target for the coalescing test: counts invocations cluster-wide (the Agent lives on
  the main node; a remote owner reaches it over distribution), then simulates a slow read
  so every concurrent caller lands inside the same in-flight window.
  """
  def slow_counted_fetch(counter) do
    Agent.update(counter, &(&1 + 1))
    Process.sleep(100)
    :ok
  end
end

defmodule EdgeLinkouts.ClusterTwoNodeTest do
  @moduledoc """
  Real two-node proofs, run with `mix test --include cluster` (excluded by default because
  they start distribution on loopback and take seconds, not milliseconds).

  Covers the two cluster guarantees the read path relies on:

  - a hot edge id is coalesced once across the whole cluster, not once per node
    (the id's ring owner runs the single-flight; the other node routes to it);
  - the membership count the RU limiter divides by tracks a joining node.
  """

  use ExUnit.Case, async: false

  @moduletag :cluster

  alias EdgeLinkouts.{Cache, Cluster, Dedupe, RateLimiter}

  setup do
    ensure_distribution!()

    # The test app booted with distribution off, so the main cache's ring holds a stale
    # :nonode@nohost member. Restart it under the now-live distribution (in production the
    # release starts distribution before the application, so this cannot happen there).
    _ = Supervisor.restart_child(EdgeLinkouts.Supervisor, EdgeLinkouts.Cache)

    peer_args = Enum.flat_map(:code.get_path(), fn path -> [~c"-pa", path] end)

    # A fresh name per test: reusing one across sequential tests races epmd re-registration
    # and leaves the previous dead node's ring entry ambiguous with the new peer's.
    peer_name = :"peer_#{System.unique_integer([:positive])}"
    {:ok, peer, peer_node} = :peer.start_link(%{name: peer_name, args: peer_args})

    # The stack boots in one detached peer process: any rpc/erpc worker exits after the
    # call, and a service start_link'ed to such a worker would die with it.
    :ok = :rpc.call(peer_node, EdgeLinkouts.ClusterPeer, :boot, [])

    assert_eventually(fn -> :rpc.call(peer_node, Process, :whereis, [Cache]) != nil end)
    assert_eventually(fn -> :rpc.call(peer_node, Process, :whereis, [Cluster]) != nil end)

    on_exit(fn ->
      try do
        :peer.stop(peer)
      catch
        # The peer may already be dead (test process exit kills the linked peer first).
        _, _ -> :ok
      end
    end)

    [peer_node: peer_node]
  end

  test "one backend read for the same hot id across both nodes", %{peer_node: peer_node} do
    # Ring discovery is asynchronous; wait until the ring is exactly this node and the peer
    # (a previous test's dead peer may linger in the ring until its pg leave propagates).
    assert_ring([node(), peer_node])

    {:ok, counter} = Agent.start_link(fn -> 0 end)
    key = live_owned_key(peer_node)
    fetch = {EdgeLinkouts.ClusterPeer, :slow_counted_fetch, [counter]}

    from_main =
      for _ <- 1..10 do
        Task.async(fn -> Dedupe.execute(key, fetch) end)
      end

    from_peer =
      for _ <- 1..10 do
        Task.async(fn ->
          :erpc.call(peer_node, Dedupe, :execute, [key, fetch, Dedupe, []])
        end)
      end

    results = Enum.map(from_main ++ from_peer, &Task.await(&1, 10_000))

    assert Enum.uniq(results) == [:ok]
    assert Agent.get(counter, & &1) == 1
  end

  test "an owner that disappears mid-flight falls back to a local read", %{peer_node: peer_node} do
    assert_ring([node(), peer_node])
    key = live_owned_key(peer_node)

    # Kill the peer while it still owns the key: the ring has not converged yet, so the
    # next execute routes to a node that no longer exists. :erpc signals that as class
    # :error ({:erpc, :noconnection}), which the fallback must catch - an uncaught one
    # would surface as an error page instead of a read.
    :ok = :rpc.call(peer_node, :init, :stop, [])

    {:ok, counter} = Agent.start_link(fn -> 0 end)
    fetch = {EdgeLinkouts.ClusterPeer, :slow_counted_fetch, [counter]}

    assert Dedupe.execute(key, fetch) == :ok
    assert Agent.get(counter, & &1) == 1
  end

  test "a joining peer raises the live node count to 2", %{peer_node: peer_node} do
    assert_eventually(fn -> Cluster.node_count() == 2 end)
    assert_eventually(fn -> :erpc.call(peer_node, Cluster, :node_count, []) == 2 end)
  end

  test "a cache write on one node is readable from the other", %{peer_node: peer_node} do
    assert_ring([node(), peer_node])

    :ok = Cache.put("shared-key", "shared-value")
    assert :rpc.call(peer_node, Cache, :get, ["shared-key"]) == {:ok, "shared-value"}
  end

  test "the RU budget is divided by the live node count", %{peer_node: _peer_node} do
    assert_eventually(fn -> Cluster.node_count() == 2 end)

    # Per-node budget is the configured total divided by the live node count (direnv sets
    # RU_BUDGET_WEB=100 locally, fly.toml sets 100 in prod), so derive expectations from
    # the actual config instead of hardcoding either value.
    total = Application.get_env(:edge_linkouts, :ru_budget_web)
    per_node = div(total, 2)

    :ok = RateLimiter.charge(per_node - 10)
    # per_node - 10 + 11 exceeds the halved budget; per_node - 10 + 9 fits it.
    refute RateLimiter.allow?(11)
    assert RateLimiter.allow?(9)
  end

  # The test VM starts distribution AFTER the app (mix test boots the app first), so the
  # cache ring can carry a stale :nonode@nohost member until the next membership event
  # heals it. Production boots distribution before the application, so the artifact is
  # impossible there; tolerate exactly that one entry here and nothing else.
  # These tests need a distributed node. Two environment facts break that on a fresh
  # runner, and both are fixed here rather than by skipping (ExUnit does not honor
  # {:skip, reason} returned from setup, so a "skip" would run the test anyway):
  #
  # - epmd is usually not running and not on PATH, so Node.start cannot spawn it. Start it
  #   from the emulator's own erts bin, which :os.find_executable does locate.
  # - a shortname node cannot use a dotted host, and OTP refuses longnames when the local
  #   hostname has no domain, so the name is <main>@<hostname> over shortnames.
  defp ensure_distribution! do
    if Node.alive?() do
      :ok
    else
      start_epmd()
      {:ok, host} = :inet.gethostname()

      case Node.start(:"main@#{host}", :shortnames) do
        {:ok, _} ->
          :ok

        {:error, reason} ->
          raise """
          cannot start Erlang distribution on #{host} (#{inspect(reason)}); the two-node           cluster tests need it. Start epmd (or put it on PATH) and make sure the hostname           resolves in /etc/hosts, or drop --only cluster to run the rest of the suite.
          """
      end
    end
  end

  defp start_epmd do
    case :os.find_executable(~c"epmd") do
      false ->
        :ok

      path ->
        # -daemon forks and exits 0, including when an epmd is already running.
        # find_executable returns a charlist; System.cmd wants a binary path.
        _ = System.cmd(List.to_string(path), ["-daemon"], stderr_to_stdout: true)
        :ok
    end
  end

  defp assert_ring(expected) do
    assert_eventually(fn ->
      ring = Cache.nodes()

      Enum.all?(expected, &(&1 in ring)) and
        Enum.all?(ring -- expected, &(&1 == :nonode@nohost))
    end)
  end

  # A key whose ring owner is a live node: a key hashing to the stale :nonode@nohost entry
  # would fall back to local reads on both nodes and could double-count the backend fetch.
  defp live_owned_key(peer_node) do
    Enum.find_value(1..1000, fn i ->
      key = "hot-edge-#{i}"

      case Cache.find_node(key) do
        {:ok, owner} when owner == peer_node or owner == node() -> key
        _other -> nil
      end
    end) || raise "no live-owned key found in 1000 tries"
  end

  defp assert_eventually(fun, attempts \\ 100)
  defp assert_eventually(fun, 0), do: assert(fun.())

  defp assert_eventually(fun, attempts) do
    if fun.() do
      assert true
    else
      Process.sleep(100)
      assert_eventually(fun, attempts - 1)
    end
  end
end

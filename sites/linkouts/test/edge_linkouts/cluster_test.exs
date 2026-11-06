defmodule EdgeLinkouts.ClusterTest do
  @moduledoc """
  Single-node behavior of the cluster wiring: everything must be a no-op at N=1, because
  that is the production shape. The real two-node proofs live in
  `EdgeLinkouts.ClusterTwoNodeTest` (tagged :cluster, excluded by default).
  """

  use ExUnit.Case, async: false

  alias EdgeLinkouts.{Cache, Cluster, Dedupe}

  test "node_count is 1 with no peers" do
    assert Cluster.node_count() == 1
  end

  test "the ring of one resolves every key to this node" do
    assert Cache.find_node("some-edge-id") == {:ok, node()}
    assert Cache.find_node("__random_pool__") == {:ok, node()}
  end

  test "an MFA descriptor executes locally and coalesces at N=1" do
    test_pid = self()

    fetch = {__MODULE__, :counted_fetch, [test_pid]}

    results =
      1..20
      |> Enum.map(fn _ -> Task.async(fn -> Dedupe.execute("edge-1", fetch) end) end)
      |> Enum.map(&Task.await/1)

    assert Enum.uniq(results) == [:ok]
    assert_received :fetch
    refute_received :fetch
  end

  # MFA target for the coalescing test: reports each invocation, then simulates a slow read.
  @doc false
  def counted_fetch(test_pid) do
    send(test_pid, :fetch)
    Process.sleep(50)
    :ok
  end
end

defmodule EdgeLinkouts.Cluster do
  @moduledoc """
  Live membership of the app nodes, for the two cluster-aware pieces of the read path.

  Every node joins the `:pg` group `:edge_linkouts_nodes` at boot; the member count is kept
  in an `:atomics` cell so the RU limiter can divide the shared budget by the live cluster
  size on its hot path without a process round trip, and `EdgeLinkouts.Dedupe` can ask the
  partitioned cache's ring where a key lives. `:pg` handles member death and netsplits
  (monitors), so the count tracks reality rather than a static config.

  Single node is the norm (one Fly machine): the group has one member, the count is 1, and
  the limiter divides by 1. Nothing here requires libcluster; libcluster only brings peers
  into the group. The count is read through `:persistent_term` with a fallback of 1, so a
  bare test that starts the limiter without the cluster supervisor sees single-node
  behavior.

  NOT `Node.list/0`: that includes ad-hoc nodes like a `fly ssh console` session, which
  would shrink the per-node budget while holding no traffic.
  """

  use GenServer

  # Own :pg scope: the default scope is only guaranteed when distribution starts it, and a
  # single-machine boot has none (that is exactly the failure the first version of this
  # module hit). One named scope, started under our own GenServer, works in every mode.
  @scope :edge_linkouts_pg
  @group :edge_linkouts_nodes
  # Name-scoped like the RateLimiter/Dedupe instances, so two Cluster processes under
  # different names in one VM cannot fight over one count cell.
  defp count_key(name), do: {__MODULE__, :node_count, name}

  def start_link(opts \\ []) do
    GenServer.start_link(__MODULE__, opts, name: Keyword.get(opts, :name, __MODULE__))
  end

  @doc "The live node count (never less than 1)."
  @spec node_count() :: pos_integer()
  def node_count(name \\ __MODULE__) do
    case :persistent_term.get(count_key(name), nil) do
      nil -> 1
      ref -> max(:atomics.get(ref, 1), 1)
    end
  end

  @impl true
  def init(opts) do
    name = Keyword.get(opts, :name, __MODULE__)
    # The scope is linked to this GenServer: when the supervisor restarts us the scope goes
    # down and comes back with it, so a stale scope can never double-count a restarted node.
    {:ok, _scope} = :pg.start_link(@scope)
    ref = :atomics.new(1, signed: false)
    :persistent_term.put(count_key(name), ref)
    :ok = :pg.join(@scope, @group, self())
    # monitor streams {ref, :join | :leave, group, pids} on later membership changes. It
    # does NOT backfill existing members (our own join included), so the count is seeded
    # explicitly below and every change after that arrives as a message.
    _ref = :pg.monitor(@scope, @group)
    :atomics.put(ref, 1, member_count())
    {:ok, ref}
  end

  @impl true
  def handle_info({_ref, change, @group, _pids}, ref) when change in [:join, :leave] do
    :atomics.put(ref, 1, member_count())
    {:noreply, ref}
  end

  def handle_info(_other, ref), do: {:noreply, ref}

  defp member_count do
    @scope |> :pg.get_members(@group) |> length() |> max(1)
  end
end

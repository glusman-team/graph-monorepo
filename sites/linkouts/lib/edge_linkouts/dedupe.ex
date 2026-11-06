defmodule EdgeLinkouts.Dedupe do
  @moduledoc """
  Collapses concurrent identical reads: one Cosmos read per document id, not one per caller,
  and replays recent successes from `EdgeLinkouts.Cache`.

  Two browsers opening the same edge within a millisecond must produce one backend call.
  `execute/3` makes the first caller the leader, which runs the function; concurrent
  callers for the same id wait and receive the leader's exact result. The result is shared
  by message, not cached: the coalescing half is request dedupe, not an HTTP cache.

  Failure handling:

  - The leader crashing (or its function raising/exiting) notifies waiters, which fall
    through to their own direct call.
  - A waiter's wait is bounded (`:wait_ms`, default 500); on timeout it deregisters and
    calls directly rather than blocking a LiveView forever.
  - Every claim and every waiter is monitored, so a crashed process leaves no entry.

  Recent results:

  A LiveView page view mounts twice, once for the static HTTP render and once for the
  connected process, a few hundred milliseconds apart. Coalescing alone cannot merge those,
  because the first read has finished before the second starts, so every page view would
  cost two Cosmos reads. Successful results are therefore replayed for `:ttl_ms`
  (default 30 s) from the cache named by the `:cache` option (`EdgeLinkouts.Cache` by
  default); a read may pass `:ttl_ms` to override the default, which is how pool documents
  replay for 15 minutes (see the web read path).

  Not-found is also replayed, briefly: `{:error, :not_found}` is kept for
  `:negative_ttl_ms` (default 10 s) so a crawler hammering dead links does not re-spend a
  request unit per hit. The negative ttl is deliberately independent of any caller's
  `:ttl_ms` override - a pool read passing a 15-minute override must not pin a 404 for
  15 minutes. Every other failure - a throttle, an outage - is retried, never replayed.

  The cache is read from the calling process, so the hot path never waits on this
  GenServer; the server only writes, and the write is a synchronous call before the leader
  returns, so a connected mount that follows the static render cannot race past the store.

  Clustering: a caller may pass an MFA tuple instead of a closure. The tuple routes the read
  to the id's owner on the partitioned cache's ring, so the whole cluster shares one
  in-flight read per id; closures stay local (see `execute/4`). Failures to route fall back
  to local execution, so a partition costs an extra read, never an error page. See ADR 0004.
  """

  use GenServer

  alias EdgeLinkouts.Cache

  @default_wait_ms 500
  @default_ttl_ms 30_000
  @default_negative_ttl_ms 10_000

  def start_link(opts \\ []) do
    opts = Keyword.put_new(opts, :name, __MODULE__)
    GenServer.start_link(__MODULE__, opts, name: Keyword.fetch!(opts, :name))
  end

  @doc """
  Runs `fun` once for all concurrent callers that pass the same `id`.

  Returns exactly what `fun` returns. If this process is not the leader, it waits up to
  the configured `:wait_ms` for the leader's result, then calls `fun` itself.

  `opts`:

  - `:ttl_ms` - how long a successful result may be replayed, overriding the server default.
    A read of a document the worker only rewrites on a load (the pool index, one release's pool)
    can be replayed for minutes without showing anything stale to a person; an edge document
    uses the default, so a page view right after a load shows the release that was just loaded.
    The override is ignored when the server's own `:ttl_ms` is zero, which is how tests turn
    caching off: a caller must not be able to switch it back on behind their back.
  """
  @type descriptor :: {module(), atom(), [term()]}

  @spec execute(term(), (-> result) | descriptor(), GenServer.name(), keyword()) :: result
        when result: var
  def execute(id, fun, server \\ __MODULE__, opts \\ [])

  # An MFA descriptor can route to the key's ring owner, so N nodes share one in-flight read
  # per key, not one per node (the partitioned cache's ring is the single source of owner
  # truth). A closure stays local: sending a compiled anonymous fun to a node running a
  # different code version is undefined behavior, which a rolling deploy would hit exactly
  # when the fleet is mixed.
  def execute(id, {mod, fun_name, args} = mfa, server, opts)
      when is_atom(mod) and is_atom(fun_name) and is_list(args) do
    case owner_for(server, id) do
      {:remote, owner} -> remote_read(owner, id, mfa, server, opts)
      :local -> run_local(id, mfa, server, opts)
    end
  end

  def execute(id, fun, server, opts) when is_function(fun, 0) do
    execute_locally(id, fun, server, opts)
  end

  @doc false
  @spec run_local(term(), descriptor(), GenServer.name(), keyword()) :: term()
  # Called through :erpc by peer nodes: coalesce on the owner, exactly like a local caller.
  def run_local(id, {mod, fun_name, args}, server, opts) do
    execute_locally(id, fn -> apply(mod, fun_name, args) end, server, opts)
  end

  @spec remote_read(node(), term(), descriptor(), GenServer.name(), keyword()) :: term()
  defp remote_read(owner, id, mfa, server, opts) do
    :erpc.call(owner, __MODULE__, :run_local, [id, mfa, server, opts], 10_000)
  catch
    # The owner left the cluster mid-call, is unreachable, timed out, or runs code from
    # before run_local/4 existed (rolling deploy): the read must still work, so run it
    # locally. :erpc signals every one of these as class :error ({:erpc, :noconnection},
    # {:erpc, :timeout}, or a wrapped remote raise), and a dying link arrives as :exit, so
    # all three classes fall back rather than only the exits.
    _kind, _reason -> run_local(id, mfa, server, opts)
  end

  # The key's owner is the node the partitioned cache would store it on. Any failure to
  # answer (cache not started, ring not formed yet) degrades to local execution.
  @spec owner_for(GenServer.name(), term()) :: :local | {:remote, node()}
  defp owner_for(server, id) do
    case cache_for(server) do
      cache when is_atom(cache) and not is_nil(cache) ->
        try do
          case EdgeLinkouts.Cache.find_node(cache, id) do
            {:ok, owner} when owner == node() -> :local
            {:ok, owner} -> {:remote, owner}
            _other -> :local
          end
        rescue
          _ -> :local
        catch
          :exit, _ -> :local
        end

      _not_running ->
        :local
    end
  end

  @spec execute_locally(term(), (-> result), GenServer.name(), keyword()) :: result
        when result: var
  defp execute_locally(id, fun, server, opts) do
    case recent(server, id) do
      {:ok, result} ->
        result

      :miss ->
        case GenServer.call(server, {:begin, id}) do
          {:leader, key} -> lead(key, fun, server, Keyword.get(opts, :ttl_ms))
          {:wait, key, wait_ms} -> wait(key, fun, wait_ms, server)
        end
    end
  end

  @doc "Drops every recent result. For tests, and for an operator after a load."
  @spec clear(GenServer.name()) :: :ok
  def clear(server \\ __MODULE__), do: GenServer.call(server, :clear)

  # Read the recent-results cache from the calling process. A miss, a cache that is not
  # running, or a server that is not running all fall through to a normal read.
  # with_dynamic_cache/2 is how Nebulex v3 scopes calls to a named instance: the leading-arg
  # arities collide with the public defaults (Cache.get(instance, key) would read key=instance
  # on the default cache), so there is no per-call instance argument.
  @spec recent(GenServer.name(), term()) :: {:ok, term()} | :miss
  defp recent(server, id) do
    case cache_for(server) do
      cache when is_atom(cache) and not is_nil(cache) ->
        Cache.with_dynamic_cache(cache, fn ->
          # fetch, not get: get returns {:ok, default} on a miss, which would masquerade as a
          # cached nil and skip the read.
          case Cache.fetch(id) do
            {:ok, result} -> {:ok, result}
            _ -> :miss
          end
        end)

      _ ->
        :miss
    end
  end

  # The cache name is resolved through :persistent_term, not passed through execute/3, so
  # the server side (which stores) and the caller side (which reads) resolve it the same
  # way. Same pattern as EdgeLinkouts.RateLimiter keeping its atomics ref there.
  @spec cache_for(GenServer.name()) :: module() | nil
  defp cache_for(server) when is_atom(server) do
    :persistent_term.get({__MODULE__, :cache, server}, nil)
  end

  defp cache_for(_server), do: nil

  defp lead(key, fun, server, ttl_ms) do
    result = fun.()
    # A call, not a cast: the result must be in the cache before this process returns, or
    # the connected mount that follows the static render races past the store and reads
    # Cosmos a second time.
    :ok = GenServer.call(server, {:done, key, result, ttl_ms})
    result
  rescue
    e ->
      GenServer.cast(server, {:crashed, key})
      reraise e, __STACKTRACE__
  catch
    :exit, reason ->
      GenServer.cast(server, {:crashed, key})
      exit(reason)
  end

  defp wait(key, fun, wait_ms, server) do
    receive do
      {:dedupe_result, ^key, result} -> result
      {:leader_down, ^key} -> fun.()
    after
      wait_ms ->
        GenServer.cast(server, {:cancel, key, self()})
        fun.()
    end
  end

  # ---------------------------------------------------------------- server

  # claims is keyed by id; by_key maps each claim's key (a make_ref) back to its id and
  # by_monitor maps every monitor ref to {role, id, pid}. Completion, cancellation and every
  # DOWN therefore land in single map lookups instead of scans over all claims - the old
  # find-by-key walked every claim, and the old find-by-monitor walked every claim AND
  # every waiter, which is quadratic under a stampede.
  defstruct claims: %{},
            by_key: %{},
            by_monitor: %{},
            wait_ms: @default_wait_ms,
            ttl_ms: @default_ttl_ms,
            negative_ttl_ms: @default_negative_ttl_ms,
            cache: Cache

  @impl true
  def init(opts) do
    name = Keyword.fetch!(opts, :name)

    wait_ms =
      Keyword.get_lazy(opts, :wait_ms, fn ->
        Application.get_env(:edge_linkouts, :dedupe_wait_ms, @default_wait_ms)
      end)

    ttl_ms =
      Keyword.get_lazy(opts, :ttl_ms, fn ->
        Application.get_env(:edge_linkouts, :dedupe_ttl_ms, @default_ttl_ms)
      end)

    negative_ttl_ms =
      Keyword.get_lazy(opts, :negative_ttl_ms, fn ->
        Application.get_env(:edge_linkouts, :negative_ttl_ms, @default_negative_ttl_ms)
      end)

    cache = Keyword.get(opts, :cache, Cache)
    :persistent_term.put({__MODULE__, :cache, name}, cache)

    {:ok,
     %__MODULE__{
       wait_ms: wait_ms,
       ttl_ms: ttl_ms,
       negative_ttl_ms: negative_ttl_ms,
       cache: cache
     }}
  end

  @impl true
  def handle_call(:clear, _from, state) do
    _ = Cache.with_dynamic_cache(state.cache, fn -> Cache.delete_all() end)
    {:reply, :ok, state}
  end

  @impl true
  def handle_call({:begin, id}, {pid, _tag}, %__MODULE__{} = state) do
    case Map.fetch(state.claims, id) do
      :error ->
        key = make_ref()
        claim = %{owner: pid, mon: Process.monitor(pid), key: key, waiters: []}
        state = state |> put_claim(id, claim) |> put_monitor(claim.mon, {:owner, id, pid})
        {:reply, {:leader, key}, state}

      {:ok, claim} ->
        waiter = %{pid: pid, mon: Process.monitor(pid)}
        claim = %{claim | waiters: [waiter | claim.waiters]}
        state = state |> put_claim(id, claim) |> put_monitor(waiter.mon, {:waiter, id, pid})
        # Waiters wait on the claim's key, which is what the owner's completion notifies
        # with; per-waiter keys would never match.
        {:reply, {:wait, claim.key, state.wait_ms}, state}
    end
  end

  def handle_call({:done, key, result, ttl_ms}, _from, state) do
    case fetch_claim(state, key) do
      nil ->
        {:reply, :ok, state}

      {id, _claim} = entry ->
        remember(state, id, result, ttl_ms)
        {:reply, :ok, finish(entry, {:dedupe_result, key, result}, state)}
    end
  end

  @impl true
  def handle_cast({:crashed, key}, state) do
    case fetch_claim(state, key) do
      nil -> {:noreply, state}
      entry -> {:noreply, finish(entry, :leader_down, state)}
    end
  end

  def handle_cast({:cancel, key, pid}, state) do
    case fetch_claim(state, key) do
      nil ->
        {:noreply, state}

      {id, claim} ->
        {removed, kept} = Enum.split_with(claim.waiters, &(&1.pid == pid))
        state = drop_waiter_monitors(state, removed)
        {:noreply, put_claim(state, id, %{claim | waiters: kept})}
    end
  end

  @impl true
  def handle_info({:DOWN, mon, :process, _pid, _reason}, state) do
    case Map.fetch(state.by_monitor, mon) do
      {:ok, {:owner, id, _pid}} ->
        {:noreply, finish({id, Map.fetch!(state.claims, id)}, :leader_down, state)}

      {:ok, {:waiter, id, pid}} ->
        Process.demonitor(mon, [:flush])
        claim = Map.fetch!(state.claims, id)
        waiters = Enum.reject(claim.waiters, &(&1.pid == pid))
        {:noreply, state |> drop_monitor(mon) |> put_claim(id, %{claim | waiters: waiters})}

      :error ->
        {:noreply, state}
    end
  end

  # Sends the outcome to every waiter, drops their monitors, and removes the claim, so a
  # crashed leader or caller never leaves an entry behind.
  defp finish({id, claim}, outcome, state) do
    message = outcome_message(outcome, claim.key)
    state = drop_waiter_monitors(state, claim.waiters)
    Process.demonitor(claim.mon, [:flush])

    state =
      state
      |> drop_index(claim.key)
      |> drop_monitor(claim.mon)
      |> Map.update!(:claims, &Map.delete(&1, id))

    Enum.each(claim.waiters, &send(&1.pid, message))
    state
  end

  defp outcome_message(:leader_down, key), do: {:leader_down, key}

  defp outcome_message({:dedupe_result, key, result}, _claim_key),
    do: {:dedupe_result, key, result}

  defp drop_waiter_monitors(state, waiters) do
    Enum.reduce(waiters, state, fn waiter, acc ->
      Process.demonitor(waiter.mon, [:flush])
      drop_monitor(acc, waiter.mon)
    end)
  end

  # put_claim keeps both indexes coherent: claims by id, and the claim's key mapped back
  # to its id (waiter joins re-put the same key/id pair, which is idempotent).
  defp put_claim(%__MODULE__{} = state, id, claim),
    do: %{
      state
      | claims: Map.put(state.claims, id, claim),
        by_key: Map.put(state.by_key, claim.key, id)
    }

  defp put_monitor(state, mon, entry),
    do: Map.update!(state, :by_monitor, &Map.put(&1, mon, entry))

  defp drop_monitor(state, mon),
    do: Map.update!(state, :by_monitor, &Map.delete(&1, mon))

  defp drop_index(state, key) when is_reference(key),
    do: Map.update!(state, :by_key, &Map.delete(&1, key))

  defp fetch_claim(state, key) do
    case Map.fetch(state.by_key, key) do
      {:ok, id} -> {id, Map.fetch!(state.claims, id)}
      :error -> nil
    end
  end

  # Only successes and not-found are kept: a throttle or an outage must be retried, not
  # replayed. A server-wide ttl of zero disables caching entirely, and a caller's override
  # cannot revive it. Not-found uses the short negative ttl so it never rides a caller's
  # long pool override.
  defp remember(%{ttl_ms: default}, _id, _result, _override) when default <= 0, do: :ok

  defp remember(
         %{cache: cache, negative_ttl_ms: neg},
         id,
         {:error, :not_found} = result,
         _override
       )
       when neg > 0 do
    _ = Cache.with_dynamic_cache(cache, fn -> Cache.put(id, result, ttl: neg) end)
    :ok
  end

  defp remember(%{negative_ttl_ms: neg}, _id, {:error, :not_found}, _override) when neg <= 0,
    do: :ok

  defp remember(%{cache: cache} = state, id, {:ok, _} = result, override) do
    ttl = if is_integer(override) and override > 0, do: override, else: state.ttl_ms
    # The instance name (an atom) is resolved by recent/0 through the same :persistent_term
    # entry, so both sides of a store see one cache.
    _ = Cache.with_dynamic_cache(cache, fn -> Cache.put(id, result, ttl: ttl) end)
    :ok
  end

  defp remember(_state, _id, _result, _override), do: :ok
end

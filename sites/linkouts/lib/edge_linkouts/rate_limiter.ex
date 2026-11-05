defmodule EdgeLinkouts.RateLimiter do
  @moduledoc """
  RU budget for the web app: a sliding one-second window over `:atomics`.

  The free tier is 1000 RU/s shared with the worker (which paces ingestion at 90% of it);
  the
  web app is budgeted at `RU_BUDGET_WEB` (default 100). Two operations per Cosmos call:

  - `allow?/2` before the call, from an estimate - refuses with `false` when the current
    window cannot fit it, so the caller returns "rate limited" instead of blocking.
  - `charge/2` after the response, with the *actual* `x-ms-request-charge` (Cosmos bills
    404s and 429s too, and a refused request is charged nothing).

  On a cluster the limiter stays per-node and the budget is divided by the live node count
  (`EdgeLinkouts.Cluster.node_count/0`, one atomics read): the strict total is
  RU_BUDGET_WEB at any N, with no cross-node coordination to fail. A node join or leave
  briefly mis-splits by a window at worst.

  The window is not a leaky bucket: a burst spends its budget and is blocked for at most
  one second, never forever. The window state is one packed 64-bit atomics word
  (window start milliseconds in the high 32 bits, RU spent in the low 32), updated with
  compare-and-exchange so concurrent readers cannot lose charges; the rejected counter is
  a second cell. No GenServer round trip on the hot path - the GenServer only owns the
  ref's lifecycle. The clock is injectable (`:now_fun`) so throttle tests do not sleep.
  """

  use GenServer

  import Bitwise

  @window_ms 1_000
  @default_budget 150
  @mask 0xFFFFFFFF

  def start_link(opts \\ []) do
    opts = Keyword.put_new(opts, :name, __MODULE__)
    GenServer.start_link(__MODULE__, opts, name: Keyword.fetch!(opts, :name))
  end

  # ---------------------------------------------------------------- hot path

  @doc """
  Whether an estimated spend fits in the current window. Increments the rejected counter
  when it does not.
  """
  @spec allow?(number(), GenServer.name()) :: boolean()
  def allow?(estimate, server \\ __MODULE__) do
    {ref, budget, now_fun} = :persistent_term.get(key(server))
    budget = per_node_budget(budget)
    now = now_fun.()
    word = :atomics.get(ref, 1)

    case unpack(word, now) do
      :stale ->
        # The old window's spend does not count; the next charge opens a fresh one.
        true

      {:fresh, spent} ->
        allowed? = spent + ceil(estimate) <= budget
        unless allowed?, do: :atomics.add(ref, 2, 1)
        allowed?
    end
  end

  @doc """
  Records actual spend. `ru` comes from `x-ms-request-charge`; fractional RU is rounded
  up so the budget is never optimistic.
  """
  @spec charge(number(), GenServer.name()) :: :ok
  def charge(ru, server \\ __MODULE__) do
    {ref, _budget, now_fun} = :persistent_term.get(key(server))
    cas_charge(ref, ceil(ru), now_fun.())
  end

  @doc "Current window spend, remaining budget, and how many `allow?/2` calls were refused."
  @spec stats(GenServer.name()) :: %{
          spend: non_neg_integer(),
          remaining: non_neg_integer(),
          rejected: non_neg_integer()
        }
  def stats(server \\ __MODULE__) do
    {ref, budget, now_fun} = :persistent_term.get(key(server))
    budget = per_node_budget(budget)
    word = :atomics.get(ref, 1)

    spend =
      case unpack(word, now_fun.()) do
        :stale -> 0
        {:fresh, spent} -> spent
      end

    %{spend: spend, remaining: max(budget - spend, 0), rejected: :atomics.get(ref, 2)}
  end

  # CAS loop: concurrent charges read-modify-write the same word, so a loser re-reads and
  # retries. Spent is capped at the 32-bit field so a pathological charge cannot wrap it.
  defp cas_charge(ref, spend, now) do
    word = :atomics.get(ref, 1)

    new_word =
      case unpack(word, now) do
        {:fresh, spent} -> pack(now, min(spent + spend, @mask))
        :stale -> pack(now, spend)
      end

    case :atomics.compare_exchange(ref, 1, word, new_word) do
      :ok -> :ok
      # On mismatch OTP returns the word's actual value (bare integer), so a loser re-reads.
      _actual -> cas_charge(ref, spend, now)
    end
  end

  # Word layout: high 32 bits = window start (ms, wraps like a sequence number), low
  # 32 bits = RU spent in that window. Age is computed modulo 2^32, which is correct for
  # any age under ~49.7 days.
  defp pack(ms, spent), do: ((ms &&& @mask) <<< 32) + spent

  defp unpack(word, now) do
    ms = word >>> 32
    spent = word &&& @mask

    if (now - ms &&& @mask) < @window_ms do
      {:fresh, spent}
    else
      :stale
    end
  end

  # ---------------------------------------------------------------- lifecycle

  @impl true
  def init(opts) do
    budget =
      Keyword.get_lazy(opts, :budget, fn ->
        Application.get_env(:edge_linkouts, :ru_budget_web, @default_budget)
      end)

    now_fun =
      Keyword.get_lazy(opts, :now_fun, fn -> fn -> System.system_time(:millisecond) end end)

    ref = :atomics.new(2, signed: false)
    # ms 0 is ancient, so the first charge/allow? opens a fresh window.
    :atomics.put(ref, 1, 0)
    :atomics.put(ref, 2, 0)
    :persistent_term.put(key(Keyword.fetch!(opts, :name)), {ref, budget, now_fun})

    {:ok, %{ref: ref}}
  end

  # Integer division keeps the cluster-wide sum at or under the configured total. The floor
  # of 1 RU/s is a liveness guard, not a usable budget: past `total` nodes the per-node
  # share is 1 RU/s and nearly every read is refused, which is the correct signal that the
  # cluster has outgrown RU_BUDGET_WEB and the budget (or the free tier) must be raised.
  defp per_node_budget(total) do
    max(div(total, EdgeLinkouts.Cluster.node_count()), 1)
  end

  defp key(server), do: {__MODULE__, server}
end

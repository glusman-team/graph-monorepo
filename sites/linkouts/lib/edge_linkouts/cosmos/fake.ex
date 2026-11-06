defmodule EdgeLinkouts.Cosmos.Fake do
  @moduledoc """
  In-memory Cosmos fake for tests: programmable documents, failures, latency and a call log.

  Started in the supervision tree only in `test` (`config :edge_linkouts,
  :start_cosmos_fake`), or per test with a unique name via `start_supervised!/1`. Stores
  documents in the same stored shape the real backends return, so tests can feed it the
  committed contract fixtures and assert through `EdgeLinkouts.Codec` unchanged.

  The call log is what lets tests assert read counts: "one read per page view, not one per
  component" is `length(Fake.calls()) == 1`, and a dedupe test proves N concurrent readers
  produced exactly one backend call.
  """

  @behaviour EdgeLinkouts.Cosmos

  use GenServer

  alias EdgeLinkouts.Cosmos

  def start_link(opts \\ []) do
    opts = Keyword.put_new(opts, :name, __MODULE__)
    GenServer.start_link(__MODULE__, opts, name: Keyword.fetch!(opts, :name))
  end

  # ---------------------------------------------------------------- behaviour

  @impl true
  def get_edge(id, server \\ __MODULE__), do: GenServer.call(server, {:get_edge, id})

  # ---------------------------------------------------------------- test controls

  @doc "Seeds stored document(s): a single stored map or a list of them."
  @spec seed(map() | [map()], GenServer.name()) :: :ok
  def seed(docs, server \\ __MODULE__), do: GenServer.call(server, {:seed, List.wrap(docs)})

  @doc """
  Seeds one release's random pool, encoded exactly as the worker writes it (pool JSON, zstd,
  base64), so a `/random` test exercises the real decode rather than a stubbed id list.
  """
  @spec seed_pool([String.t()], String.t(), String.t(), GenServer.name()) :: :ok
  def seed_pool(ids, slug, version_label, server \\ __MODULE__) do
    payload = %{
      "schema" => "edgelinkouts.pool/1",
      "key" => "infores:" <> slug <> "-" <> version_label,
      "ids" => ids
    }

    seed(%{"id" => Cosmos.pool_doc_id(slug, version_label), "b" => encode(payload)}, server)
  end

  @doc """
  Seeds the pool index from `%{slug => %{version_label => edge_count}}`.

  A release's sampled count defaults to its edge count and its timestamp to nil, which is the
  shape a test cares about: which graphs and releases exist, and how they are weighted.
  """
  @spec seed_pool_index(%{String.t() => %{String.t() => non_neg_integer()}}, GenServer.name()) ::
          :ok
  def seed_pool_index(kgs, server \\ __MODULE__) do
    versions =
      for {slug, releases} <- kgs, into: %{} do
        {slug,
         %{
           "versions" =>
             for {label, edges} <- releases, into: %{} do
               {label, %{"edges" => edges, "sampled" => edges}}
             end
         }}
      end

    payload = %{"schema" => "edgelinkouts.pool_index/1", "kgs" => versions}
    seed(%{"id" => Cosmos.pool_index_id(), "b" => encode(payload)}, server)
  end

  defp encode(payload) do
    payload
    |> JSON.encode!()
    |> :zstd.compress()
    |> IO.iodata_to_binary()
    |> Base.encode64()
  end

  @doc "Programs the next call to fail with `{:error, reason}`. Call again to queue more."
  @spec queue_error(term(), GenServer.name()) :: :ok
  def queue_error(reason, server \\ __MODULE__) do
    GenServer.call(server, {:queue_error, reason})
  end

  @doc "Delays every call by `ms` milliseconds (0 disables)."
  @spec set_latency(non_neg_integer(), GenServer.name()) :: :ok
  def set_latency(ms, server \\ __MODULE__), do: GenServer.call(server, {:set_latency, ms})

  @doc "Calls so far, oldest first: one `{:get_edge, id}` entry per point read."
  @spec calls(GenServer.name()) :: [{:get_edge, String.t()}]
  def calls(server \\ __MODULE__), do: GenServer.call(server, :calls)

  @doc "Clears documents, queued errors, latency and the call log."
  @spec reset(GenServer.name()) :: :ok
  def reset(server \\ __MODULE__), do: GenServer.call(server, :reset)

  # ---------------------------------------------------------------- server

  @impl true
  def init(_opts), do: {:ok, %{docs: %{}, errors: [], latency_ms: 0, log: []}}

  @impl true
  def handle_call({:get_edge, id}, _from, state) do
    {reply, state} =
      run({:get_edge, id}, state, fn docs ->
        case Map.fetch(docs, id) do
          {:ok, doc} -> {:ok, doc}
          :error -> {:error, :not_found}
        end
      end)

    {:reply, reply, state}
  end

  def handle_call({:seed, docs}, _from, state) do
    docs = Map.new(docs, fn doc -> {Map.fetch!(doc, "id"), doc} end)
    {:reply, :ok, %{state | docs: Map.merge(state.docs, docs)}}
  end

  def handle_call({:queue_error, reason}, _from, state) do
    {:reply, :ok, %{state | errors: state.errors ++ [reason]}}
  end

  def handle_call({:set_latency, ms}, _from, state) do
    {:reply, :ok, %{state | latency_ms: ms}}
  end

  def handle_call(:calls, _from, state), do: {:reply, Enum.reverse(state.log), state}

  def handle_call(:reset, _from, _state),
    do: {:reply, :ok, %{docs: %{}, errors: [], latency_ms: 0, log: []}}

  # Shared call pipeline: log entry, latency, queued failure, then the operation.
  defp run(entry, state, operation) do
    if state.latency_ms > 0, do: Process.sleep(state.latency_ms)

    {queued, errors} = pop_error(state.errors)

    reply =
      if queued do
        {:error, queued}
      else
        operation.(state.docs)
      end

    {reply, %{state | errors: errors, log: [entry | state.log]}}
  end

  defp pop_error([reason | rest]), do: {reason, rest}
  defp pop_error([]), do: {nil, []}
end

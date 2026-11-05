defmodule EdgeLinkouts.Cosmos.File do
  @moduledoc """
  NDJSON file backend: serves stored documents from the file the worker's file store writes.

  One stored document per line (`{"id":...,"b":...,"d":...}`), last line for an id wins -
  the same append-then-compact shape the worker produces, and the exact shape of the
  committed fixtures at `dagster/projects/linkouts/contracts/docs.ndjson`. The file is read once at
  startup into a map; dev files are small, so no per-request re-read. Reserved documents
  (the pool index, one release's pool) are lines like any other and are read by id, so
  `/random` works against a dev file with no extra machinery.

  The path comes from `config :edge_linkouts, :cosmos_file` (set it from `COSMOS_DOCS` in
  runtime.exs). A nil path starts the backend empty; a configured path that does not exist
  fails loudly, since serving a truncated dataset silently would be worse.
  """

  @behaviour EdgeLinkouts.Cosmos

  use GenServer

  def start_link(opts) do
    opts = Keyword.put_new(opts, :name, __MODULE__)
    GenServer.start_link(__MODULE__, opts, name: Keyword.fetch!(opts, :name))
  end

  @impl true
  def get_edge(id, server \\ __MODULE__), do: GenServer.call(server, {:get_edge, id})

  @impl true
  def init(opts) do
    path =
      Keyword.get_lazy(opts, :path, fn ->
        Application.get_env(:edge_linkouts, :cosmos_file)
      end)

    {:ok, %{docs: load_docs(path)}}
  end

  @impl true
  def handle_call({:get_edge, id}, _from, state) do
    {:reply, fetch_edge(state.docs, id), state}
  end

  defp fetch_edge(docs, id) do
    case Map.fetch(docs, id) do
      {:ok, doc} -> {:ok, doc}
      :error -> {:error, :not_found}
    end
  end

  defp load_docs(nil), do: %{}

  defp load_docs(path) do
    path
    |> File.stream!()
    |> Enum.reduce(%{}, fn line, docs ->
      case String.trim(line) do
        "" -> docs
        trimmed -> put_doc(docs, path, trimmed)
      end
    end)
  end

  defp put_doc(docs, path, line) do
    case JSON.decode(line) do
      {:ok, %{"id" => id} = doc} ->
        # Later lines overwrite earlier ones: last line for an id wins.
        Map.put(docs, id, doc)

      {:ok, doc} ->
        raise ArgumentError, "#{path}: line without an id: #{inspect(Map.keys(doc))}"

      {:error, reason} ->
        raise ArgumentError, "#{path}: corrupt NDJSON line (#{inspect(reason)})"
    end
  end
end

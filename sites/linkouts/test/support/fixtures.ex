defmodule EdgeLinkoutsWeb.Fixtures do
  @moduledoc """
  The committed contract fixtures, plus stored-document builders for synthetic blobs.

  Tests seed `EdgeLinkouts.Cosmos.Fake` from the real stored data (docs.ndjson) rather
  than inventing documents. The blob builder exists for cases the fixtures cannot
  express - a hostile subject name, or a blob with a specific added/removed/changed
  field triple - and encodes exactly the documented ADR 0001 wire format.

  docs.ndjson holds both kinds of document the Go worker writes: edge blobs, and the reserved
  pool documents behind `/random` (one index plus one pool per release). `edge_docs/0` and
  `pool_ids/2` split them, so a test can assert on the real sample rather than a hand-made
  list of ids that drifts from what the worker produces.
  """

  alias EdgeLinkouts.{Codec, Cosmos}

  @docs_path Path.expand("../../../../dagster/projects/linkouts/contracts/docs.ndjson", __DIR__)

  # The canonical load keys: the infores the graph is registered under, plus the release.
  @v1 "infores:drugapprovals-kp-1.11.2"
  @v2 "infores:drugapprovals-kp-1.16.0"
  # The slug - the name without its infores prefix - is what is stored on a document, what a
  # pool document id is built from, and what a URL carries.
  @slug "drugapprovals-kp"

  def v1, do: @v1
  def v2, do: @v2
  def slug, do: @slug

  @doc "Every stored document in docs.ndjson, edges and reserved pool documents alike."
  def docs, do: read(@docs_path)

  @doc "The edge documents in docs.ndjson: everything that is not a reserved pool document."
  def edge_docs do
    Enum.reject(docs(), &String.starts_with?(&1["id"], "__random_pool__"))
  end

  def first_edge_id, do: hd(edge_docs())["id"]

  def edge_doc(id), do: Enum.find(edge_docs(), &(&1["id"] == id))

  @doc """
  The releases the fixture pool index lists for a graph, newest first.

  Read from the fixture rather than written out here, so a regenerated fixture changes the
  tests that depend on it instead of quietly disagreeing with them.
  """
  def fixture_releases(slug \\ @slug) do
    fixture_index()
    |> Map.get(slug, %{})
    |> Map.keys()
    |> Enum.sort(fn a, b -> Codec.compare_versions(a, b) == :gt end)
  end

  @doc "The decoded fixture pool index: `%{slug => %{label => %{edges:, sampled:, sampled_at:}}}`."
  def fixture_index do
    with %{} = doc <- Enum.find(docs(), &(&1["id"] == Cosmos.pool_index_id())),
         {:ok, index} <- Cosmos.decode_pool_index_doc(doc) do
      index
    else
      nil -> %{}
      {:error, reason} -> raise "fixture pool index does not decode: #{inspect(reason)}"
    end
  end

  @doc "The sampled edge ids the fixture pool holds for one release."
  def pool_ids(label, slug \\ @slug) do
    case Enum.find(docs(), &(&1["id"] == Cosmos.pool_doc_id(slug, label))) do
      nil ->
        []

      doc ->
        case Cosmos.decode_pool_doc(doc) do
          {:ok, pool} ->
            pool.ids

          {:error, reason} ->
            raise "fixture pool for #{slug} #{label} does not decode: #{inspect(reason)}"
        end
    end
  end

  @doc "The resolved document for a stored fixture document at a version key."
  def resolved(%{"b" => b64}, key) do
    {:ok, blob} = Codec.decode(b64)
    {:ok, doc} = Codec.resolve(blob, key)
    doc
  end

  @doc """
  Encodes version payloads as one stored blob document (`{"id", "b"}`), the way the worker
  writes them: canonical-ish JSON, one zstd frame, base64. Only decode is exercised by
  the reader, so building is plain `JSON.encode!/1`.
  """
  def stored(id, versions) do
    frame =
      %{"schema" => "edgelinkouts.blob/1", "versions" => versions}
      |> JSON.encode!()
      |> :zstd.compress()
      |> IO.iodata_to_binary()

    %{"id" => id, "b" => Base.encode64(frame)}
  end

  defp read(path) do
    path
    |> File.stream!()
    |> Stream.map(&String.trim/1)
    |> Stream.reject(&(&1 == ""))
    |> Enum.map(&JSON.decode!/1)
  end
end

defmodule EdgeLinkouts.Cosmos.FileTest do
  use ExUnit.Case, async: true

  # The app-level backend instance (config/test.exs) is pointed at the committed contract
  # fixtures, so these tests read real worker-written data end to end.
  alias EdgeLinkouts.{Codec, Cosmos}

  @docs Path.expand("../../../../dagster/projects/linkouts/contracts/docs.ndjson", __DIR__)
  @edge_id "575af3e8-8015-3718-be03-4da18a0bacfc"
  @slug "drugapprovals-kp"
  @v1 "infores:drugapprovals-kp-1.11.2"
  @v2 "infores:drugapprovals-kp-1.16.0"

  defp stored_docs do
    @docs
    |> File.stream!()
    |> Stream.map(&String.trim/1)
    |> Stream.reject(&(&1 == ""))
    |> Enum.map(&JSON.decode!/1)
  end

  describe "get_edge/1" do
    test "returns the stored document for a fixture id" do
      assert {:ok, doc} = Cosmos.File.get_edge(@edge_id)
      assert doc["id"] == @edge_id
      assert is_binary(doc["b"]) and doc["b"] != ""
      assert Map.keys(doc) -- ["id", "b", "d", "k"] == []
      # `k` is the graph's slug, the one field that says which graph an edge belongs to
      # without decoding it.
      assert doc["k"] == @slug
    end

    test "an unknown id is :not_found" do
      assert Cosmos.File.get_edge("no-such-id") == {:error, :not_found}
    end
  end

  # The reserved documents are lines in the same file, read by id like any edge: that is what
  # keeps /random a point read against a container with no indexes.
  describe "reserved pool documents" do
    test "the index lists every stored release with its counts" do
      assert {:ok, doc} = Cosmos.File.get_edge(Cosmos.pool_index_id())
      assert {:ok, index} = Cosmos.decode_pool_index_doc(doc)

      releases = index[@slug]
      assert Map.keys(releases) |> Enum.sort() == ["1.11.2", "1.16.0"]
      # Counts only: no ids in the index, which is what keeps it about a kilobyte.
      assert releases["1.16.0"].edges == 6
      assert releases["1.16.0"].sampled == 6
    end

    test "one release's pool decodes into ids that are all stored" do
      id = Cosmos.pool_doc_id(@slug, "1.16.0")
      assert {:ok, doc} = Cosmos.File.get_edge(id)
      assert {:ok, pool} = Cosmos.decode_pool_doc(doc)

      assert pool.key == @v2
      assert pool.ids != []

      stored = stored_docs() |> Enum.map(& &1["id"]) |> MapSet.new()
      assert Enum.all?(pool.ids, &MapSet.member?(stored, &1))
    end

    test "a release nobody loaded has no pool document" do
      assert Cosmos.File.get_edge(Cosmos.pool_doc_id(@slug, "9.9.9")) == {:error, :not_found}
    end
  end

  describe "through Codec" do
    test "a fixture edge resolves to both stored versions" do
      {:ok, doc} = Cosmos.File.get_edge(@edge_id)
      # The committed fixtures are dictionary-free (d absent), matching the contract test.
      assert {:ok, blob} = Codec.decode(doc["b"], doc["d"])
      assert Codec.versions(blob) == [@v1, @v2]

      for version <- Codec.versions(blob) do
        assert {:ok, resolved} = Codec.resolve(blob, version)
        assert resolved["id"] == @edge_id
        assert is_binary(resolved["subject"]) and is_binary(resolved["object"])
      end
    end
  end

  describe "last line for an id wins" do
    test "a later line for the same id replaces the earlier one" do
      path = Path.join(System.tmp_dir!(), "edge_linkouts_file_test_#{System.unique_integer()}")
      on_exit(fn -> File.rm(path) end)

      File.write!(path, """
      {"id":"edge-1","b":"first"}
      {"id":"edge-1","b":"second"}
      {"id":"edge-2","b":"only"}
      """)

      name = :"file_backend_#{System.unique_integer()}"
      start_supervised!({Cosmos.File, path: path, name: name})

      assert Cosmos.File.get_edge("edge-1", name) == {:ok, %{"id" => "edge-1", "b" => "second"}}
      assert Cosmos.File.get_edge("edge-2", name) == {:ok, %{"id" => "edge-2", "b" => "only"}}
    end
  end

  describe "an unconfigured path" do
    test "starts empty instead of failing" do
      name = :"file_backend_#{System.unique_integer()}"
      start_supervised!({Cosmos.File, path: nil, name: name})

      assert Cosmos.File.get_edge("anything", name) == {:error, :not_found}
      assert Cosmos.File.get_edge(Cosmos.pool_index_id(), name) == {:error, :not_found}
    end
  end

  describe "a corrupt store" do
    test "a line that is not a document fails loudly at startup" do
      path = Path.join(System.tmp_dir!(), "edge_linkouts_file_test_#{System.unique_integer()}")
      on_exit(fn -> File.rm(path) end)
      File.write!(path, "not json\n")

      name = :"file_backend_#{System.unique_integer()}"

      # Unlinked start: init raises, and the failure comes back as an error tuple instead
      # of killing the test process through the start_link.
      assert {:error, {%ArgumentError{message: message}, _stack}} =
               GenServer.start(EdgeLinkouts.Cosmos.File, path: path, name: name)

      assert message =~ "corrupt NDJSON"
    end
  end
end

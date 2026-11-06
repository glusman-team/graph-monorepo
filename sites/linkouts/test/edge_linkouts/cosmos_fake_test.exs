defmodule EdgeLinkouts.Cosmos.FakeTest do
  use ExUnit.Case, async: true

  alias EdgeLinkouts.Cosmos

  setup do
    name = :"fake_#{System.unique_integer()}"
    start_supervised!({Cosmos.Fake, name: name})
    %{fake: name}
  end

  test "returns seeded documents by id", %{fake: fake} do
    Cosmos.Fake.seed(%{"id" => "edge-1", "b" => "frame"}, fake)

    assert Cosmos.Fake.get_edge("edge-1", fake) == {:ok, %{"id" => "edge-1", "b" => "frame"}}
  end

  test "an unknown id is :not_found", %{fake: fake} do
    assert Cosmos.Fake.get_edge("nope", fake) == {:error, :not_found}
  end

  test "the next queued failure is consumed exactly once, then the real result follows", %{
    fake: fake
  } do
    Cosmos.Fake.seed(%{"id" => "edge-1", "b" => "frame"}, fake)
    Cosmos.Fake.queue_error({:throttled, 42}, fake)
    Cosmos.Fake.queue_error(:boom, fake)
    Cosmos.Fake.queue_error(:boom, fake)

    assert Cosmos.Fake.get_edge("edge-1", fake) == {:error, {:throttled, 42}}
    assert Cosmos.Fake.get_edge("edge-1", fake) == {:error, :boom}
    assert Cosmos.Fake.get_edge("edge-1", fake) == {:error, :boom}
    assert Cosmos.Fake.get_edge("edge-1", fake) == {:ok, %{"id" => "edge-1", "b" => "frame"}}
  end

  # Reserved documents are ordinary documents read by id: the fake needs no special path for
  # them, which is the point of the pool-doc design (no query, no scan, one point read).
  test "a seeded pool is readable by its reserved id and decodes like the worker writes it", %{
    fake: fake
  } do
    Cosmos.Fake.seed_pool(["a", "b"], "drugapprovals-kp", "1.16.0", fake)
    id = Cosmos.pool_doc_id("drugapprovals-kp", "1.16.0")

    assert {:ok, doc} = Cosmos.Fake.get_edge(id, fake)
    assert {:ok, pool} = Cosmos.decode_pool_doc(doc)
    assert pool.ids == ["a", "b"]
    assert pool.key == "infores:drugapprovals-kp-1.16.0"
  end

  test "a seeded pool index carries the counts a weighted pick needs", %{fake: fake} do
    Cosmos.Fake.seed_pool_index(%{"drugapprovals-kp" => %{"1.11.2" => 6, "1.16.0" => 8}}, fake)

    assert {:ok, doc} = Cosmos.Fake.get_edge(Cosmos.pool_index_id(), fake)
    assert {:ok, index} = Cosmos.decode_pool_index_doc(doc)

    assert index["drugapprovals-kp"]["1.16.0"] == %{edges: 8, sampled: 8, sampled_at: nil}
    assert index["drugapprovals-kp"]["1.11.2"].edges == 6
  end

  test "a reserved id nobody seeded is :not_found", %{fake: fake} do
    assert Cosmos.Fake.get_edge(Cosmos.pool_index_id(), fake) == {:error, :not_found}
  end

  test "set_latency delays every call", %{fake: fake} do
    Cosmos.Fake.set_latency(5, fake)
    start = System.monotonic_time(:millisecond)
    Cosmos.Fake.get_edge("edge-1", fake)
    assert System.monotonic_time(:millisecond) - start >= 5
  end

  test "the call log records operations in order", %{fake: fake} do
    Cosmos.Fake.seed(%{"id" => "edge-1", "b" => "frame"}, fake)
    Cosmos.Fake.get_edge("edge-1", fake)
    Cosmos.Fake.get_edge("edge-2", fake)
    Cosmos.Fake.get_edge(Cosmos.pool_index_id(), fake)

    assert Cosmos.Fake.calls(fake) == [
             {:get_edge, "edge-1"},
             {:get_edge, "edge-2"},
             {:get_edge, "__random_pool__"}
           ]
  end

  test "reset clears documents, failures, latency and the log", %{fake: fake} do
    Cosmos.Fake.seed(%{"id" => "edge-1", "b" => "frame"}, fake)
    Cosmos.Fake.queue_error(:boom, fake)
    Cosmos.Fake.set_latency(1, fake)
    Cosmos.Fake.get_edge("edge-1", fake)

    assert Cosmos.Fake.reset(fake) == :ok
    assert Cosmos.Fake.get_edge("edge-1", fake) == {:error, :not_found}
    assert Cosmos.Fake.calls(fake) == [{:get_edge, "edge-1"}]
  end
end

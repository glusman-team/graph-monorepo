defmodule EdgeLinkouts.DedupeTest do
  use ExUnit.Case, async: true

  alias EdgeLinkouts.{Cache, Cosmos, Dedupe}

  setup do
    fake = :"fake_#{System.unique_integer()}"
    start_supervised!({Cosmos.Fake, name: fake})
    %{fake: fake}
  end

  # Coalescing tests run with the result cache off, so a cached result cannot hide a backend call.
  # The cache has its own describe block below.
  defp start_dedupe(opts \\ []) do
    name = :"dedupe_#{System.unique_integer([:positive])}"
    opts = opts |> Keyword.put(:name, name) |> Keyword.put_new(:ttl_ms, 0)
    start_supervised!({Dedupe, opts})
    name
  end

  test "N concurrent readers of one id produce exactly one backend call", %{fake: fake} do
    dedupe = start_dedupe()
    id = "edge-1"
    doc = %{"id" => id, "b" => "frame"}
    Cosmos.Fake.seed(doc, fake)
    # Long enough that every spawned reader lands inside the leader's call.
    Cosmos.Fake.set_latency(50, fake)

    results =
      for _ <- 1..8 do
        Task.async(fn -> Dedupe.execute(id, fn -> Cosmos.Fake.get_edge(id, fake) end, dedupe) end)
      end
      |> Task.await_many()

    assert Enum.uniq(results) == [{:ok, doc}]
    # The point: one read per page view, not one per component.
    assert Cosmos.Fake.calls(fake) == [{:get_edge, id}]
  end

  test "different ids are not collapsed", %{fake: fake} do
    dedupe = start_dedupe()
    ids = for n <- 1..5, do: "edge-#{n}"
    Cosmos.Fake.set_latency(20, fake)

    for id <- ids do
      Cosmos.Fake.seed(%{"id" => id, "b" => "frame"}, fake)
    end

    results =
      for id <- ids do
        Task.async(fn -> Dedupe.execute(id, fn -> Cosmos.Fake.get_edge(id, fake) end, dedupe) end)
      end
      |> Task.await_many()

    assert length(results) == 5
    assert Cosmos.Fake.calls(fake) |> length() == 5
  end

  test "a waiter whose leader fails receives the leader's error without a second call", %{
    fake: fake
  } do
    dedupe = start_dedupe()
    id = "edge-1"
    Cosmos.Fake.set_latency(50, fake)
    Cosmos.Fake.queue_error(:boom, fake)

    tasks =
      for _ <- 1..3 do
        Task.async(fn -> Dedupe.execute(id, fn -> Cosmos.Fake.get_edge(id, fake) end, dedupe) end)
      end
      |> Task.await_many()

    assert Enum.uniq(tasks) == [{:error, :boom}]
    assert Cosmos.Fake.calls(fake) |> length() == 1
  end

  test "a waiter falls through to a direct read when its wait bound expires", %{fake: fake} do
    dedupe = start_dedupe(wait_ms: 50)
    id = "edge-1"
    Cosmos.Fake.seed(%{"id" => id, "b" => "frame"}, fake)

    leader =
      Task.async(fn ->
        Dedupe.execute(
          id,
          fn ->
            Process.sleep(300)
            Cosmos.Fake.get_edge(id, fake)
          end,
          dedupe
        )
      end)

    # Let the leader claim the id, then join it as a waiter.
    Process.sleep(20)

    {waiter_ms, waiter_result} =
      :timer.tc(fn ->
        Dedupe.execute(id, fn -> Cosmos.Fake.get_edge(id, fake) end, dedupe)
      end)

    assert waiter_result == {:ok, %{"id" => id, "b" => "frame"}}
    # Did not wait for the leader's full 300 ms.
    assert waiter_ms < 250_000

    Task.await(leader, 2_000)
    assert Cosmos.Fake.calls(fake) |> length() == 2
  end

  test "a waiter falls through when the leader crashes mid-read", %{fake: fake} do
    dedupe = start_dedupe()
    id = "edge-1"
    Cosmos.Fake.seed(%{"id" => id, "b" => "frame"}, fake)

    spawn(fn ->
      Dedupe.execute(id, fn -> raise "leader explodes" end, dedupe)
    end)

    Process.sleep(20)

    assert Dedupe.execute(id, fn -> Cosmos.Fake.get_edge(id, fake) end, dedupe) ==
             {:ok, %{"id" => id, "b" => "frame"}}

    # Only the fall-through read hit the backend; the crashed leader never did.
    assert Cosmos.Fake.calls(fake) == [{:get_edge, id}]
  end

  test "a finished claim is removed, so a later read runs again", %{fake: fake} do
    dedupe = start_dedupe()
    id = "edge-1"
    Cosmos.Fake.seed(%{"id" => id, "b" => "frame"}, fake)

    fun = fn -> Cosmos.Fake.get_edge(id, fake) end
    assert {:ok, _} = Dedupe.execute(id, fun, dedupe)
    assert {:ok, _} = Dedupe.execute(id, fun, dedupe)

    assert Cosmos.Fake.calls(fake) |> length() == 2
  end

  describe "recent results" do
    setup %{fake: fake} do
      Cosmos.Fake.seed(%{"id" => "e1", "b" => "x"}, fake)
      :ok
    end

    # Cache tests run against a Dedupe instance wired to its own cache: the app-wide cache is
    # shared by every async test, and storing into it would leak documents between cases.
    # gc_interval is pushed far past any test so no generation swap can evict behind a test's
    # back; expiry is read-based and deterministic.
    defp start_cached_dedupe(opts \\ []) do
      n = System.unique_integer([:positive])
      cache = :"cache_#{n}"
      name = :"dedupe_#{n}"
      start_supervised!({Cache, name: cache, primary: [gc_interval: :timer.hours(1)]})

      opts =
        opts
        |> Keyword.put(:name, name)
        |> Keyword.put(:cache, cache)
        |> Keyword.put_new(:ttl_ms, 30_000)

      start_supervised!({Dedupe, opts})
      name
    end

    defp read(dedupe, fake, id, opts \\ []),
      do: Dedupe.execute(id, fn -> Cosmos.Fake.get_edge(id, fake) end, dedupe, opts)

    test "the LiveView double mount costs one backend read, not two", %{fake: fake} do
      # Static render, then the connected mount a moment later: sequential, so coalescing alone
      # cannot merge them.
      dedupe = start_cached_dedupe()

      assert {:ok, _} = read(dedupe, fake, "e1")
      assert {:ok, _} = read(dedupe, fake, "e1")
      assert Cosmos.Fake.calls(fake) == [{:get_edge, "e1"}]
    end

    test "not-found is briefly remembered; a throttle never is", %{fake: fake} do
      dedupe = start_cached_dedupe()

      # A 404 IS replayed for the short negative ttl: crawlers hammering dead links must
      # not re-spend a request unit per hit.
      assert {:error, :not_found} = read(dedupe, fake, "missing")
      assert {:error, :not_found} = read(dedupe, fake, "missing")
      assert Cosmos.Fake.calls(fake) == [{:get_edge, "missing"}]

      # A capacity refusal is never replayed - the next view retries immediately.
      Cosmos.Fake.queue_error({:throttled, 100}, fake)
      assert {:error, {:throttled, 100}} = read(dedupe, fake, "e1")
      assert {:ok, _} = read(dedupe, fake, "e1")
      assert length(Cosmos.Fake.calls(fake)) == 3
    end

    test "not-found expires after the negative ttl", %{fake: fake} do
      dedupe = start_cached_dedupe(ttl_ms: 30_000, negative_ttl_ms: 20)

      assert {:error, :not_found} = read(dedupe, fake, "missing")
      Process.sleep(40)
      assert {:error, :not_found} = read(dedupe, fake, "missing")
      assert length(Cosmos.Fake.calls(fake)) == 2
    end

    test "a long caller ttl override never pins a not-found" do
      # The pool read path passes ttl_ms: 15 min; a missing pool must not be replayed for
      # 15 minutes - the negative ttl is independent of the override.
      dedupe = start_cached_dedupe(ttl_ms: 30_000, negative_ttl_ms: 20)

      assert {:error, :not_found} =
               Dedupe.execute("missing", fn -> {:error, :not_found} end, dedupe, ttl_ms: 900_000)

      Process.sleep(40)

      assert {:error, :not_found} =
               Dedupe.execute("missing", fn -> {:error, :not_found} end, dedupe, ttl_ms: 900_000)
    end

    test "a negative ttl of zero disables 404 replay like ttl 0 disables success replay", %{
      fake: fake
    } do
      dedupe = start_cached_dedupe(ttl_ms: 30_000, negative_ttl_ms: 0)

      assert {:error, :not_found} = read(dedupe, fake, "missing")
      assert {:error, :not_found} = read(dedupe, fake, "missing")
      assert length(Cosmos.Fake.calls(fake)) == 2
    end

    test "results expire after the ttl", %{fake: fake} do
      dedupe = start_cached_dedupe(ttl_ms: 20)

      assert {:ok, _} = read(dedupe, fake, "e1")
      Process.sleep(40)
      assert {:ok, _} = read(dedupe, fake, "e1")

      assert length(Cosmos.Fake.calls(fake)) == 2
    end

    test "clear/1 forgets everything", %{fake: fake} do
      dedupe = start_cached_dedupe()

      assert {:ok, _} = read(dedupe, fake, "e1")
      :ok = Dedupe.clear(dedupe)
      assert {:ok, _} = read(dedupe, fake, "e1")

      assert length(Cosmos.Fake.calls(fake)) == 2
    end

    test "a caller's ttl override cannot revive caching the server disabled", %{fake: fake} do
      # The app cache in this env runs with ttl 0 (config/test.exs): tests reseed the shared
      # Fake between cases, so nothing may be replayed. An override must not switch that back on.
      dedupe = start_dedupe()

      assert {:ok, _} = read(dedupe, fake, "e1", ttl_ms: 30_000)
      assert {:ok, _} = read(dedupe, fake, "e1", ttl_ms: 30_000)

      assert length(Cosmos.Fake.calls(fake)) == 2
    end
  end
end

defmodule EdgeLinkouts.CacheTest do
  use ExUnit.Case, async: true

  # Behavior tests for the Nebulex local adapter as configured for this app (see
  # config/config.exs): what TTL expiry, generation eviction and stats mean here. The adapter
  # itself is Nebulex's to test; these pin the semantics the read path relies on.
  #
  # Named instances take process-scoped dynamic caches (with_dynamic_cache/2): the leading-arg
  # call forms collide with the public default arities and would silently hit the default cache.

  alias EdgeLinkouts.Cache

  defp start_cache(opts \\ []) do
    name = :"cache_#{System.unique_integer([:positive])}"
    # Local-storage options (gc tuning) live under :primary now that the adapter is
    # partitioned; the distributed layer's own options stay at the top level.
    {primary, opts} = Keyword.split(opts, [:gc_cleanup_delay, :gc_interval, :max_size])
    start_supervised!({Cache, [name: name, primary: primary] ++ opts})
    name
  end

  defp with_cache(cache, fun), do: Cache.with_dynamic_cache(cache, fun)

  # Generation GC lives in the primary storage (the Local adapter); the partitioned cache
  # has no generations of its own, so the test drives the primary by its derived name.
  defp new_generation(cache) do
    primary = Module.concat(Macro.camelize("#{cache}"), "Primary")

    Cache.__primary__().with_dynamic_cache(primary, fn -> Cache.__primary__().new_generation() end)
  end

  test "an expired entry reads as a miss" do
    cache = start_cache()

    :ok = with_cache(cache, fn -> Cache.put("k", :v, ttl: 20) end)
    assert {:ok, :v} = with_cache(cache, fn -> Cache.fetch("k") end)

    Process.sleep(40)
    assert {:error, %Nebulex.KeyError{}} = with_cache(cache, fn -> Cache.fetch("k") end)
  end

  test "an entry no generation kept alive is evicted" do
    cache = start_cache(gc_cleanup_delay: 20)

    :ok = with_cache(cache, fn -> Cache.put("k", :v, ttl: :timer.hours(1)) end)
    # Two swaps with no reads: "k" never moves generations, so its generation is purged.
    _ = new_generation(cache)
    _ = new_generation(cache)

    Process.sleep(100)
    assert {:error, %Nebulex.KeyError{}} = with_cache(cache, fn -> Cache.fetch("k") end)
  end

  test "reading an entry promotes it into the newer generation" do
    cache = start_cache(gc_cleanup_delay: 20)

    :ok = with_cache(cache, fn -> Cache.put("hot", :v, ttl: :timer.hours(1)) end)
    :ok = with_cache(cache, fn -> Cache.put("cold", :v, ttl: :timer.hours(1)) end)

    # First swap: both entries fall into the old generation.
    _ = new_generation(cache)
    # Reading "hot" moves it into the newer generation; "cold" stays behind.
    assert {:ok, :v} = with_cache(cache, fn -> Cache.fetch("hot") end)

    # Second swap schedules the old generation's deletion; only the promoted entry survives it.
    _ = new_generation(cache)
    Process.sleep(100)

    assert {:ok, :v} = with_cache(cache, fn -> Cache.fetch("hot") end)
    assert {:error, %Nebulex.KeyError{}} = with_cache(cache, fn -> Cache.fetch("cold") end)
  end

  test "stats count hits and misses" do
    cache = start_cache()

    assert {:error, %Nebulex.KeyError{}} = with_cache(cache, fn -> Cache.fetch("k") end)
    :ok = with_cache(cache, fn -> Cache.put("k", :v, ttl: 60_000) end)
    assert {:ok, :v} = with_cache(cache, fn -> Cache.fetch("k") end)

    stats = with_cache(cache, fn -> Cache.info!(:stats) end)
    assert stats.hits >= 1
    assert stats.misses >= 1
  end
end

defmodule EdgeLinkouts.RateLimiterTest do
  use ExUnit.Case, async: true

  alias EdgeLinkouts.RateLimiter

  # Injectable clock (throttle tests do not sleep). Bump it to slide the window.
  setup do
    clock = start_supervised!({Agent, fn -> 0 end})
    now = fn -> Agent.get(clock, & &1) end

    name = :"limiter_#{System.unique_integer()}"
    start_supervised!({RateLimiter, name: name, budget: 10, now_fun: now})

    %{clock: clock, limiter: name}
  end

  test "allows spending up to the budget exactly", %{limiter: limiter} do
    assert RateLimiter.allow?(4, limiter)
    assert RateLimiter.charge(4, limiter) == :ok
    assert RateLimiter.allow?(6, limiter)
    assert RateLimiter.charge(6, limiter) == :ok

    assert RateLimiter.allow?(1, limiter) == false
    assert %{spend: 10, remaining: 0} = RateLimiter.stats(limiter)
  end

  test "a zero estimate is allowed even on an empty budget", %{limiter: limiter} do
    RateLimiter.charge(10, limiter)
    assert RateLimiter.allow?(0, limiter)
  end

  test "refusals are counted", %{limiter: limiter} do
    RateLimiter.charge(10, limiter)

    assert RateLimiter.allow?(1, limiter) == false
    assert RateLimiter.allow?(2, limiter) == false
    assert %{rejected: 2} = RateLimiter.stats(limiter)
  end

  test "the window slides: spend older than one second does not count", %{
    clock: clock,
    limiter: limiter
  } do
    RateLimiter.charge(10, limiter)
    assert RateLimiter.allow?(1, limiter) == false

    Agent.update(clock, &(&1 + 999))
    assert RateLimiter.allow?(1, limiter) == false

    Agent.update(clock, &(&1 + 1))
    assert RateLimiter.allow?(1, limiter)
    assert %{spend: 0, remaining: 10} = RateLimiter.stats(limiter)
  end

  test "a charge after the window expired starts a fresh window instead of accumulating", %{
    clock: clock,
    limiter: limiter
  } do
    RateLimiter.charge(5, limiter)
    Agent.update(clock, &(&1 + 1_001))
    RateLimiter.charge(5, limiter)

    assert %{spend: 5} = RateLimiter.stats(limiter)
  end

  test "fractional RU is rounded up, never optimistic", %{limiter: limiter} do
    RateLimiter.charge(1.2, limiter)
    assert %{spend: 2} = RateLimiter.stats(limiter)
  end

  test "concurrent charges do not lose updates", %{limiter: limiter} do
    tasks =
      for _ <- 1..50 do
        Task.async(fn -> RateLimiter.charge(10, limiter) end)
      end

    Task.await_many(tasks)

    assert %{spend: 500} = RateLimiter.stats(limiter)
  end

  test "stats report the configured budget's remainder", %{limiter: limiter} do
    RateLimiter.charge(3, limiter)
    assert %{spend: 3, remaining: 7, rejected: 0} = RateLimiter.stats(limiter)
  end
end

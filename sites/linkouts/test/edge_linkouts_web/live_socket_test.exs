defmodule EdgeLinkoutsWeb.LiveSocketTest do
  @moduledoc """
  The origin lockdown on the socket transports (WebSocket and longpoll). Phoenix dispatches
  these before any plug, so the only check is `LiveSocket.connect/3` itself; a regression
  here reopens the CDN bypass the plug exists to close.
  """

  use ExUnit.Case, async: false

  alias EdgeLinkoutsWeb.{LiveSocket, OriginCheck}

  @key "test-origin-key-0123456789"

  setup do
    Application.put_env(:edge_linkouts, :origin_check_key, @key)
    on_exit(fn -> Application.delete_env(:edge_linkouts, :origin_check_key) end)
    :ok
  end

  test "a connect without the key is refused before any LiveView mounts" do
    assert :error = LiveSocket.connect(%{}, %Phoenix.Socket{}, %{x_headers: []})

    assert :error =
             LiveSocket.connect(%{}, %Phoenix.Socket{}, %{
               x_headers: [{"x-origin-key", "wrong-#{@key}"}]
             })
  end

  test "a connect with the key mounts and keeps its connect_info" do
    connect_info = %{x_headers: [{"x-origin-key", @key}]}

    assert {:ok, %Phoenix.Socket{} = socket} =
             LiveSocket.connect(%{}, %Phoenix.Socket{}, connect_info)

    assert socket.private[:connect_info] == connect_info
  end

  test "a repeated X-Origin-Key is refused, not a crash" do
    assert :error =
             LiveSocket.connect(%{}, %Phoenix.Socket{}, %{
               x_headers: [{"x-origin-key", @key}, {"x-origin-key", @key}]
             })
  end

  test "the plug refuses a repeated header with a bare 403, not a 500" do
    # put_req_header replaces, so build the duplicate directly - this is the shape a
    # client sending the header twice actually produces.
    conn = Plug.Test.conn("GET", "/")

    conn =
      %{conn | req_headers: [{"x-origin-key", @key}, {"x-origin-key", @key} | conn.req_headers]}
      |> OriginCheck.call([])

    assert conn.status == 403
    assert conn.halted
  end

  test "headers_allowed? covers the shared contract both transports use" do
    assert OriginCheck.headers_allowed?([{"x-origin-key", @key}])
    refute OriginCheck.headers_allowed?([])
    refute OriginCheck.headers_allowed?([{"x-origin-key", "nope"}])
    refute OriginCheck.headers_allowed?([{"x-origin-key", @key}, {"x-origin-key", @key}])
  end

  test "no configured key means development behavior: everything allowed" do
    Application.delete_env(:edge_linkouts, :origin_check_key)

    assert OriginCheck.headers_allowed?([])
    assert {:ok, %Phoenix.Socket{}} = LiveSocket.connect(%{}, %Phoenix.Socket{}, %{})
  end
end

defmodule EdgeLinkoutsWeb.LiveSocket do
  @moduledoc """
  The LiveView socket, behind the same origin lockdown as the HTTP routes.

  Phoenix dispatches socket requests (WebSocket and longpoll on `/live`) before any user
  plug runs, so `EdgeLinkoutsWeb.OriginCheck` can never see them; the check has to live in
  `connect/3`. Same rule, same constant-time compare: a connection without the shared
  `X-Origin-Key` never mounts a LiveView, never spends Cosmos RU. Refusing the handshake
  returns `:error`, which the transport answers with a 403 before a channel process exists.
  """

  use Phoenix.LiveView.Socket

  alias EdgeLinkoutsWeb.OriginCheck

  # Declared here only because defining connect/3 with @impl makes the compiler require
  # @impl on every Phoenix.Socket callback in this module, including the id/1 that
  # `use Phoenix.LiveView.Socket` injects. Same behavior: delegate to LiveView's own id/1.
  @impl Phoenix.Socket
  defdelegate id(socket), to: Phoenix.LiveView.Socket

  @impl Phoenix.Socket
  def connect(params, %Phoenix.Socket{} = socket, connect_info) do
    if OriginCheck.headers_allowed?(connect_info[:x_headers] || []) do
      super(params, socket, connect_info)
    else
      :error
    end
  end
end

defmodule EdgeLinkoutsWeb.OriginCheck do
  @moduledoc """
  Origin lockdown: only requests carrying the shared `X-Origin-Key` header reach the app.

  `edge-linkouts.fly.dev` is Fly platform plumbing; it cannot be deleted while the app
  exists, and Cloudflare reaches the origin through it. Without a check, an attacker can
  bypass the CDN (and its DDoS protection) by hitting the fly.dev hostname directly,
  spending the Cosmos RU budget nobody is watching. A Cloudflare Transform Rule on the
  zone adds the header to every proxied request; everything else gets a bare 403 before
  any route, LiveView, or store read runs.

  Enabled only in production, where `config/runtime.exs` copies `X_ORIGIN_KEY` into
  `config :edge_linkouts, :origin_check_key`. Local development and the test suite never
  set it, so they pass through untouched - and a stray `X_ORIGIN_KEY` in a developer's
  shell (direnv) cannot leak into a test run the way a per-request `System.get_env/1`
  read once did.

  Two things legitimately bypass this plug: `Plug.Static` (public digested assets carry no
  data and must not gate the health of crawlers) and the LiveView socket transports, which
  Phoenix dispatches before any plug - those repeat the identical check in
  `EdgeLinkoutsWeb.LiveSocket.connect/3`, so nothing that can spend RU runs without the
  key on any transport.

  The comparison is `Plug.Crypto.secure_compare/2` (constant time): the header is a
  shared secret, and a plain `==` would let a timing oracle leak it byte by byte.
  """

  @behaviour Plug

  @impl true
  def init(opts), do: opts

  @doc """
  Whether a `{lowercase-name, value}` header list carries the configured origin key exactly
  once. Both callers hand in that shape: the plug's `conn.req_headers` and the socket's
  `connect_info[:x_headers]`. No key configured means allowed (development and tests never
  configure one); a repeated `X-Origin-Key` is refused rather than erroring, because a
  malformed request deserves the same bare 403 as a missing key.
  """
  @spec headers_allowed?([{binary(), binary()}]) :: boolean()
  def headers_allowed?(headers) do
    case Application.get_env(:edge_linkouts, :origin_check_key) do
      nil ->
        true

      key ->
        case for({"x-origin-key", value} <- headers, do: value) do
          [sent] -> Plug.Crypto.secure_compare(key, sent)
          _zero_or_repeated -> false
        end
    end
  end

  @impl true
  def call(conn, _opts) do
    if headers_allowed?(conn.req_headers), do: conn, else: forbid(conn)
  end

  defp forbid(conn) do
    conn
    |> Plug.Conn.send_resp(403, "forbidden")
    |> Plug.Conn.halt()
  end
end

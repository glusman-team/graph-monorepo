defmodule EdgeLinkoutsWeb.HealthCheck do
  @moduledoc """
  `GET /healthz` answers 200 "ok" and nothing else.

  Fly's machine health checks (and any load balancer that ends up in front of the app)
  probe the origin directly, without Cloudflare's `X-Origin-Key` header and over plain
  HTTP, so this plug sits in the endpoint BEFORE `EdgeLinkoutsWeb.OriginCheck`, and
  `force_ssl` excludes the path. It touches nothing - no Cosmos read, no RU spend, no
  session - so a misconfigured prober cannot cost anything or learn anything beyond
  "the VM is up".
  """

  @behaviour Plug

  @impl true
  def init(opts), do: opts

  @impl true
  def call(%Plug.Conn{request_path: "/healthz", method: "GET"} = conn, _opts) do
    conn
    |> Plug.Conn.put_resp_content_type("text/plain")
    |> Plug.Conn.send_resp(200, "ok")
    |> Plug.Conn.halt()
  end

  def call(conn, _opts), do: conn
end

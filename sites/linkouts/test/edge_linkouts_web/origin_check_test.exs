defmodule EdgeLinkoutsWeb.OriginCheckTest do
  @moduledoc """
  Origin lockdown and liveness endpoint behavior.

  Why this exists: the fly.dev hostname is reachable directly and bypasses Cloudflare
  (and its DDoS protection); without the shared-header check anyone could spend the
  Cosmos RU budget. These tests pin the three properties that keep that check both
  strict and harmless:

  - configured key + missing/wrong header -> 403 before any store read;
  - configured key + right header -> the page renders normally;
  - /healthz answers 200 with no header at all (Fly's checks carry none), and a stray
    X_ORIGIN_KEY in the shell cannot arm the check, because the key is read from
    application config (set only in prod by runtime.exs), never from the environment
    per request.
  """

  use EdgeLinkoutsWeb.ConnCase, async: false

  @key "test-origin-key"

  setup do
    Application.put_env(:edge_linkouts, :origin_check_key, @key)
    on_exit(fn -> Application.delete_env(:edge_linkouts, :origin_check_key) end)
  end

  test "a request without the header is forbidden before any route runs", %{conn: conn} do
    conn = get(conn, "/")
    assert conn.status == 403
    assert conn.resp_body == "forbidden"
  end

  test "a request with the wrong header is forbidden", %{conn: conn} do
    conn = conn |> put_req_header("x-origin-key", "not-the-key") |> get("/")
    assert conn.status == 403
  end

  test "the matching header reaches the page", %{conn: conn} do
    conn = conn |> put_req_header("x-origin-key", @key) |> get("/")
    assert html_response(conn, 200)
  end

  test "/healthz answers 200 without the header", %{conn: conn} do
    conn = get(conn, "/healthz")
    assert conn.status == 200
    assert conn.resp_body == "ok"
  end

  test "an unconfigured key leaves the app open (dev and test)", %{conn: conn} do
    Application.delete_env(:edge_linkouts, :origin_check_key)
    conn = get(conn, "/healthz")
    assert conn.status == 200
  end
end

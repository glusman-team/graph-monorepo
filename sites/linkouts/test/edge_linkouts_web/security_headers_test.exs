defmodule EdgeLinkoutsWeb.SecurityHeadersTest do
  @moduledoc """
  Content-Security-Policy wiring.

  Why: the whole point of the nonce is that header and template agree - if the
  `content-security-policy` header names a nonce that is not on the inline theme script,
  the page's dark-mode pre-paint silently breaks, and if the script's nonce is not in the
  header, the browser blocks it. The one assertion pair below is what catches either.
  """

  use EdgeLinkoutsWeb.ConnCase, async: false

  alias EdgeLinkouts.Cosmos
  alias EdgeLinkoutsWeb.Fixtures

  setup do
    Cosmos.Fake.reset()
    Cosmos.Fake.seed(Fixtures.docs())
  end

  test "every HTML answer carries the CSP with a nonce, and the layout script uses it", %{
    conn: conn
  } do
    conn = get(conn, "/")
    [csp] = get_resp_header(conn, "content-security-policy")
    html = response(conn, 200)

    ["script-src 'self' 'nonce-" <> rest] =
      String.split(csp, "; ") |> Enum.filter(&String.starts_with?(&1, "script-src"))

    [nonce | _] = String.split(rest, "'")
    assert nonce != ""
    assert html =~ ~s(nonce="#{nonce}")

    # The pinned hardening directives.
    assert csp =~ "default-src 'self'"
    assert csp =~ "connect-src 'self' wss://www.example.com"
    assert csp =~ "frame-ancestors 'none'"
    assert csp =~ "object-src 'none'"
  end

  test "the standard secure headers still come along", %{conn: conn} do
    conn = get(conn, "/")
    assert get_resp_header(conn, "x-content-type-options") == ["nosniff"]
    assert get_resp_header(conn, "referrer-policy") == ["strict-origin-when-cross-origin"]
  end

  test "the LiveView static render carries the same nonce agreement", %{conn: conn} do
    # The controller test above covers PageController; this pins the LiveView path
    # (`live "/edges/:id"`), the one where the root layout renders from the socket's
    # static dispatch. If the nonce stops reaching the layout here, the theme pre-paint
    # script is blocked and nobody notices until dark mode flashes.
    id = Fixtures.first_edge_id()
    conn = get(conn, "/edges/#{id}")
    [csp] = get_resp_header(conn, "content-security-policy")
    html = response(conn, 200)

    [script_src] =
      csp |> String.split("; ") |> Enum.filter(&String.starts_with?(&1, "script-src"))

    ["script-src 'self' 'nonce-" <> rest] = [script_src]
    [nonce | _] = String.split(rest, "'")
    assert html =~ ~s(nonce="#{nonce}")
  end
end

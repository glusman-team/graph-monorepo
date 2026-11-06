defmodule EdgeLinkoutsWeb.ErrorHTMLTest do
  use EdgeLinkoutsWeb.ConnCase, async: true

  # Bring render_to_string/4 for testing custom views
  import Phoenix.Template, only: [render_to_string: 4]

  test "renders 404.html" do
    content = render_to_string(EdgeLinkoutsWeb.ErrorHTML, "404", "html", [])

    assert content =~ "Page not found"
    assert content =~ "edge id was not found"
    assert content =~ ~s(href="/random")
  end

  test "renders 500.html" do
    assert render_to_string(EdgeLinkoutsWeb.ErrorHTML, "500", "html", []) ==
             "Internal Server Error"
  end
end

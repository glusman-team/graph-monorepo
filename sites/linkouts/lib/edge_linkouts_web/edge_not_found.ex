defmodule EdgeLinkoutsWeb.EdgeNotFound do
  @moduledoc """
  Raised when a page or download asks for an edge id the store does not have.

  `plug_status` makes the endpoint's error rendering answer with a genuine HTTP 404
  (Phoenix renders `EdgeLinkoutsWeb.ErrorHTML` "404"), so an id typo or a dead link reads
  as failure to a crawler or a monitor instead of looking like a rendered success page.
  """

  defexception message: "the edge id was not found", plug_status: 404
end

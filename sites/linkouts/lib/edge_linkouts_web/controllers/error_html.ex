defmodule EdgeLinkoutsWeb.ErrorHTML do
  @moduledoc """
  This module is invoked by the endpoint in case of errors on HTML requests.

  See config/config.exs.
  """
  use EdgeLinkoutsWeb, :html

  embed_templates "error_html/*"

  # Any error status without a template falls back to the status text.
  def render(template, _assigns) do
    Phoenix.Controller.status_message_from_template(template)
  end
end

defmodule EdgeLinkoutsWeb.Router do
  use EdgeLinkoutsWeb, :router

  pipeline :browser do
    plug :accepts, ["html"]
    plug :fetch_session
    plug :fetch_live_flash
    plug :put_root_layout, html: {EdgeLinkoutsWeb.Layouts, :root}
    plug :protect_from_forgery
    plug EdgeLinkoutsWeb.SecurityHeaders
  end

  scope "/", EdgeLinkoutsWeb do
    pipe_through :browser

    # The root is a redirect, not a page: a visitor with nothing in hand wants one real
    # edge, and legacy KGinfo permalinks arrive as /?id=<uuid>.
    get "/", PageController, :home
    live "/edges/:id", EdgeLive

    get "/random", RandomController, :random

    # A random edge inside one knowledge graph, with an optional ?version=<label> to stay in
    # one release. Declared after the fixed routes above so "/random" and "/edges/:id" keep
    # winning the match; the :kg segment is the KG slug ("drugapprovals-kp"), and the full
    # infores form is accepted too.
    get "/:kg/random", RandomController, :random

    # A controller rather than a LiveView: a file download needs a real HTTP response
    # carrying Content-Disposition, which a LiveView cannot produce for a top-level
    # navigation.
    get "/edges/:id/download", EdgeController, :download
  end
end

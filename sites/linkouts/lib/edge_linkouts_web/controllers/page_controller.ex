defmodule EdgeLinkoutsWeb.PageController do
  use EdgeLinkoutsWeb, :controller

  require Logger

  alias EdgeLinkouts.{Codec, Display}
  alias EdgeLinkoutsWeb.Edges

  @moduledoc """
  The root of the app is the bar of knowledge graphs this store holds: one row per graph, its
  releases as pills, each pill a random relationship from that release and the row's name a
  random one from the graph's latest release.

  The page is one point read of the pool index — counts, no ids, about a kilobyte — so it costs
  roughly 1 RU cold and nothing warm, and it grows by a few bytes per release rather than by a
  document. Nothing here queries the container: its indexing policy is `none`, so a query would
  be a full scan (ADR 0001 on the docs site). Every label, ordering and count is derived on
  the server from that index plus `kgs/*.exs`; nothing derived is stored.

  The legacy KGinfo entry point was `/?id=<uuid>`, and permalinks of that shape still
  circulate in issue trackers and papers, so an `id` parameter here is a redirect to the
  edge page rather than a 404.
  """

  def home(conn, %{"id" => id}) when is_binary(id) do
    # The legacy CGI accepted ids straight from the query string; a verified route would
    # raise on an id containing a slash, so trim to the characters an id can hold first.
    case String.replace(id, ~r/[^\w.:+-]/, "") do
      "" -> redirect(conn, to: ~p"/random")
      clean -> redirect(conn, to: ~p"/edges/#{clean}")
    end
  end

  def home(conn, _params) do
    case Edges.fetch_pool_index() do
      {:ok, index} ->
        render(conn, :home, rows: bar_rows(index), notice: nil)

      # No index document is an empty store, not a broken one: an install nobody has loaded
      # data into yet gets the empty state, which says what to run next.
      {:error, :not_found} ->
        render(conn, :home, rows: [], notice: nil)

      # A store at its read budget is a fact about this second, not an outage: the page still
      # renders, with the reason on it, instead of claiming there is no data.
      {:error, :rate_limited} ->
        render(conn, :home, rows: [], notice: :rate_limited)

      {:error, reason} ->
        Logger.warning("home: pool index read failed: #{inspect(reason)}")
        render(conn, :home, rows: [], notice: :unavailable)
    end
  end

  # ---------------------------------------------------------------- the bar

  # One row per graph, releases newest first. A graph whose index entry lists no releases is
  # dropped rather than rendered as a name that leads to a 404.
  defp bar_rows(index) do
    index
    |> Enum.filter(fn {slug, versions} -> is_binary(slug) and is_map(versions) end)
    |> Enum.map(&row/1)
    |> Enum.reject(&(&1.releases == []))
    |> Enum.sort_by(& &1.name)
  end

  defp row({slug, versions}) do
    canonical = Display.kg_from_slug(slug)
    config = Display.get(canonical)

    releases =
      versions
      |> Enum.filter(fn {label, stats} -> is_binary(label) and is_map(stats) end)
      |> Enum.map(fn {label, stats} ->
        %{
          label: label,
          edges: stats.edges,
          sampled: stats.sampled
        }
      end)
      # Newest first, by the same numeric comparison the edge page orders its timeline with:
      # "1.16.0" before "1.11.2", not after it as a string sort would put it.
      |> Enum.sort(fn a, b -> Codec.compare_versions(a.label, b.label) == :gt end)

    # The latest badge tags the newest stored release, computed from the versions the
    # store actually holds rather than a number maintained by hand in kgs/*.exs.
    newest = Codec.newest_label(Enum.map(releases, & &1.label))
    releases = Enum.map(releases, &Map.put(&1, :latest?, &1.label == newest))

    %{
      slug: slug,
      name: (config && config.display_name) || canonical,
      canonical: canonical,
      url: config && config.url,
      releases: releases,
      # The newest release's edge count, not a sum: releases of one graph re-assert largely the
      # same edges, so adding them would report a graph several times its real size.
      edges: edges_in(releases),
      newest: newest
    }
  end

  defp edges_in([%{edges: edges} | _rest]), do: edges
  defp edges_in([]), do: 0
end

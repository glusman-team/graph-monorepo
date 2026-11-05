defmodule EdgeLinkoutsWeb.RandomController do
  use EdgeLinkoutsWeb, :controller

  require Logger

  alias EdgeLinkouts.{Codec, Display}
  alias EdgeLinkoutsWeb.Edges

  @moduledoc """
  `/random` and `/<kg>/random` answer with an HTTP 302 so an external link can point at them;
  a LiveView would mount a socket and render just to bounce the browser. These are the only
  non-LiveView routes that read data.

  A pick costs point reads only, never a query — the container's indexing policy is `none`, so
  a query would be a full scan at full price (ADR 0001 on the docs site):

  - `/<kg>/random?version=<label>` reads exactly one pool document and picks an id from it.
  - `/random`, `/<kg>/random` read the pool index (counts, no ids), choose a release weighted
    by how many edges it holds, then read that one release's pool.

  A pick without an explicit `?version=` means each graph's *latest* stored release, never an
  older one: `/random` draws only the newest release of every graph in the store, and
  `/<kg>/random` only the newest of that graph. An older release is reachable at random
  solely through an explicit version selection — a pill on the home bar, a timeline step on
  an edge page, or the header's random button while an older release is being read.

  Weighting by the true edge count is what keeps "a random edge" uniform over the store: a
  release holding twice the edges is twice as likely to be chosen, and a uniform pick inside
  its reservoir is uniform over those edges. The index is about a kilobyte and a pool is read
  once per node per cache TTL, so serving a thousand randoms costs roughly one read.

  The redirect carries `?version=<key>`, which opens the release the id was sampled from
  rather than whatever the blob's newest version happens to be.

  When there is nothing to pick, the honest answer is a page that says so. Redirecting to `/`
  instead would loop, because `/` is the KG bar that links here.
  """

  # How many times a weighted pick may fall through to another release. A release can be
  # listed by a cached index and have had its pool purged since; re-picking is a map lookup,
  # while giving up on a store that is full of data is a dead end for the reader.
  @pick_attempts 3

  def random(conn, %{"kg" => kg} = params) do
    pick(conn, Display.slug(kg), params["version"])
  end

  def random(conn, params) do
    pick(conn, nil, params["version"])
  end

  # One named release: a single pool read, with no index involved at all.
  defp pick(conn, slug, version) when is_binary(slug) and is_binary(version) do
    label = normalize_version(version, slug)

    case Edges.fetch_pool(slug, label) do
      {:ok, %{ids: [_ | _] = ids} = pool} -> bounce(conn, ids, pool.key, slug, label)
      {:ok, %{ids: []}} -> nothing(conn, slug, label)
      {:error, :not_found} -> nothing(conn, slug, label)
      {:error, :rate_limited} -> busy(conn)
      {:error, reason} -> failed(conn, reason)
    end
  end

  # Everything else — the whole store, or one graph's releases — goes through the index,
  # reduced to each graph's latest stored release: a no-version pick never lands on an
  # older release.
  defp pick(conn, slug, _version) do
    case Edges.fetch_pool_index() do
      {:ok, index} ->
        case index |> releases(slug) |> latest_only() do
          [] -> nothing_at_all(conn, slug)
          candidates -> weighted(conn, candidates, slug, @pick_attempts)
        end

      # No index document at all: an install nobody has loaded data into yet. On a scoped
      # route the same read answers "is this graph here?", so the page says that instead.
      {:error, :not_found} ->
        nothing_at_all(conn, slug)

      {:error, :rate_limited} ->
        busy(conn)

      {:error, reason} ->
        failed(conn, reason)
    end
  end

  # Picks a release in proportion to its edge count, then an id inside that release. A release
  # whose pool turns out to be empty or gone is dropped and the pick is made again.
  defp weighted(conn, candidates, slug, attempts)

  defp weighted(conn, [], slug, _attempts), do: nothing_at_all(conn, slug)

  defp weighted(conn, _candidates, slug, 0) do
    Logger.warning(
      "random edge: no usable pool for #{inspect(slug)} after #{@pick_attempts} tries"
    )

    nothing_at_all(conn, slug)
  end

  defp weighted(conn, candidates, slug, attempts) do
    {{{chosen_slug, label}, _release}, rest} = take_weighted(candidates)

    case Edges.fetch_pool(chosen_slug, label) do
      {:ok, %{ids: [_ | _] = ids} = pool} ->
        bounce(conn, ids, pool.key, chosen_slug, label)

      # Missing, unreadable or empty: try another release rather than 404 on a full store.
      _other ->
        weighted(conn, rest, slug, attempts - 1)
    end
  end

  # One weighted draw. A release with zero stored edges carries zero weight and is never drawn,
  # which is right: its reservoir is empty. With every weight zero — an index of releases that
  # all reported no edges — the draw falls back to uniform, because refusing to choose would
  # 404 a store that does hold pools.
  defp take_weighted(candidates) do
    weights = Enum.map(candidates, fn {{_slug, _label}, %{edges: edges}} -> max(edges, 0) end)
    total = Enum.sum(weights)

    chosen =
      if total > 0 do
        roll = :rand.uniform(total)

        Enum.reduce_while(Enum.zip(candidates, weights), roll, fn {candidate, weight}, left ->
          if left <= weight, do: {:halt, candidate}, else: {:cont, left - weight}
        end)
      else
        Enum.random(candidates)
      end

    {chosen, List.delete(candidates, chosen)}
  end

  # The releases in scope, as {{slug, label}, release}, sorted so the draw and any log line are
  # reproducible. The weights decide the pick, not the order.
  defp releases(index, nil) do
    candidates =
      for {slug, versions} when is_binary(slug) and is_map(versions) <- index,
          {label, release} when is_binary(label) and is_map(release) <- versions do
        {{slug, label}, release}
      end

    Enum.sort(candidates)
  end

  defp releases(index, slug) do
    case Map.get(index, slug) do
      versions when is_map(versions) ->
        candidates =
          for {label, release} when is_binary(label) and is_map(release) <- versions do
            {{slug, label}, release}
          end

        Enum.sort(candidates)

      _other ->
        []
    end
  end

  # One candidate per graph: its newest stored release, by the same numeric comparison the
  # home bar sorts its pills with. This is what makes every no-version random entry point
  # mean "latest" — an older release survives here only as an explicit ?version= pick.
  defp latest_only(candidates) do
    candidates
    |> Enum.group_by(fn {{slug, _label}, _release} -> slug end)
    |> Enum.map(fn {_slug, group} ->
      labels = for {{_slug, label}, _release} <- group, do: label
      newest = Codec.newest_label(labels)
      Enum.find(group, fn {{_slug, label}, _release} -> label == newest end)
    end)
  end

  defp bounce(conn, ids, key, slug, label) do
    id = Enum.random(ids)

    # The pool carries the exact key the worker loaded this release under, the only form certain
    # to match a version stored inside the blob. Deriving it from the slug is the fallback for
    # a pool written without one.
    case key || derive_key(slug, label) do
      nil -> redirect(conn, to: ~p"/edges/#{id}")
      version_key -> redirect(conn, to: ~p"/edges/#{id}?version=#{version_key}")
    end
  end

  defp derive_key(slug, label) when is_binary(slug) and is_binary(label) do
    Display.kg_from_slug(slug) <> "-" <> label
  end

  defp derive_key(_slug, _label), do: nil

  # A version pill carries a bare label ("1.16.0"); a copied permalink may carry the whole
  # version key ("infores:drugapprovals-kp-1.16.0"). Both name one release, so the longer form
  # is reduced to its label instead of 404ing on a URL this app itself produced.
  defp normalize_version(version, slug) when is_binary(slug) do
    [Display.kg_from_slug(slug) <> "-", slug <> "-"]
    |> Enum.reduce(version, fn prefix, acc -> String.replace_prefix(acc, prefix, "") end)
  end

  # --------------------------------------------------------------- state pages

  # Which "nothing" a reader gets depends on what they asked for: a scoped route names the
  # graph, the whole-store route says the store holds nothing.
  defp nothing_at_all(conn, nil), do: empty(conn)
  defp nothing_at_all(conn, slug), do: nothing(conn, slug, nil)

  defp nothing(conn, slug, label) do
    conn
    |> put_status(:not_found)
    |> render(:unknown_kg,
      layout: {EdgeLinkoutsWeb.Layouts, :root},
      kg: kg_label(slug),
      version: label
    )
  end

  defp empty(conn) do
    conn
    |> put_status(:not_found)
    |> render(:empty, layout: {EdgeLinkoutsWeb.Layouts, :root})
  end

  defp busy(conn) do
    conn
    |> put_status(:too_many_requests)
    |> render(:busy, layout: {EdgeLinkoutsWeb.Layouts, :root})
  end

  defp failed(conn, reason) do
    # The store itself failed (network, auth). Retryable, and distinct from "no data".
    Logger.warning("random edge: pool read failed: #{inspect(reason)}")

    conn
    |> put_status(:service_unavailable)
    |> render(:unavailable, layout: {EdgeLinkoutsWeb.Layouts, :root})
  end

  # The name a reader recognises: the config's display name when this graph has one, else the
  # canonical name, else the slug exactly as it arrived.
  defp kg_label(slug) do
    case Display.get(Display.kg_from_slug(slug)) do
      nil -> Display.kg_from_slug(slug)
      config -> config.display_name
    end
  end
end

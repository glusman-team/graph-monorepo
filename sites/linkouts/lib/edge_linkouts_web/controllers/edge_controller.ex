defmodule EdgeLinkoutsWeb.EdgeController do
  use EdgeLinkoutsWeb, :controller

  require Logger

  alias EdgeLinkouts.{Codec, Cosmos}
  alias EdgeLinkoutsWeb.Edges

  @moduledoc """
  The KGX download for one edge document.

  This is a controller rather than a LiveView because a file download needs a real HTTP
  response with a Content-Disposition header, which a LiveView cannot produce for a
  top-level navigation. The body is `Codec.canonical_binary/1` of the resolved document:
  exactly the canonical bytes the worker would store, so a downloaded file is stable across
  versions, languages and repeated requests.
  """

  def download(conn, %{"id" => id} = params) do
    with {:ok, stored} <- Edges.fetch_edge(id),
         {:ok, dict} <- Cosmos.dictionary_for(stored),
         {:ok, blob} <- Codec.decode(stored["b"], dict),
         {:ok, key} <- select_version(blob, params["version"]),
         {:ok, doc} <- Codec.resolve(blob, key) do
      body = Codec.canonical_binary(doc)
      send_cacheable(conn, body, filename: "edge-#{id}-#{filename_key(key)}.ndjson")
    else
      # The controller can answer 404 without raising: put_status + the ErrorHTML view
      # produce the same page the LiveView's EdgeNotFound exception renders, but as a
      # plain response a test can assert on with html_response/2.
      {:error, :not_found} ->
        render_not_found(conn)

      {:error, {:unknown_version, version}} ->
        Logger.warning("download for edge #{inspect(id)}: unknown version #{inspect(version)}")
        render_not_found(conn)

      {:error, :rate_limited} ->
        conn
        |> put_status(:too_many_requests)
        |> text("The Cosmos read budget is exhausted; retry shortly.")

      {:error, reason} ->
        # A blob that will not decode or resolve is a data bug. Answer plainly with the
        # reason instead of dressing it up as a missing document.
        conn
        |> put_status(:internal_server_error)
        |> text("corrupt stored document: #{inspect(reason)}")
    end
  end

  # The download is a pure function of the stored document (canonical bytes), so repeat
  # requests can be answered from caches instead of re-shipping the file:
  # Cache-Control public 300 lets a shared cache hold it briefly, and a strong ETag (the
  # content's own hash) turns a repeat request into a 304 with no body. That is egress
  # the origin never pays again; the underlying Cosmos read is already replayed from the
  # 30 s result cache, so the RU side is unchanged.
  defp send_cacheable(conn, body, filename: filename) do
    etag = "\"#{Base.encode16(:crypto.hash(:sha256, body), case: :lower) |> binary_part(0, 32)}\""

    conn =
      conn
      |> put_resp_header("etag", etag)
      |> put_resp_header("cache-control", "public, max-age=300")

    if if_none_match(conn) |> Enum.any?(&(&1 == etag or &1 == "*")) do
      conn |> send_resp(304, "") |> halt()
    else
      send_download(conn, {:binary, body},
        filename: filename,
        content_type: "application/x-ndjson"
      )
    end
  end

  # If-None-Match is a comma-separated list of validators, and RFC 9110 uses weak
  # comparison for GET, so a client's W/"..." form matches our strong tag. Browsers echo
  # the single strong tag verbatim; the split keeps compliant non-browser clients from
  # paying for a full re-download.
  defp if_none_match(conn) do
    conn
    |> Plug.Conn.get_req_header("if-none-match")
    |> Enum.flat_map(&String.split(&1, ","))
    |> Enum.map(&String.trim/1)
    |> Enum.map(&String.trim_leading(&1, "W/"))
  end

  defp render_not_found(conn) do
    # render_errors renders error templates with layout: false; match that, so the
    # standalone 404 document is not wrapped in the app root layout a second time.
    conn
    |> put_status(:not_found)
    |> put_root_layout(false)
    |> put_view(EdgeLinkoutsWeb.ErrorHTML)
    |> render("404.html")
  end

  defp select_version(blob, requested) when is_binary(requested) do
    if requested in Codec.versions(blob) do
      {:ok, requested}
    else
      {:error, {:unknown_version, requested}}
    end
  end

  defp select_version(blob, nil), do: {:ok, Codec.newest(blob)}

  # A canonical version key carries its registry scheme ("infores:drugapprovals-kp-1.16.0"), and
  # a colon is illegal in a Windows filename and awkward in a shell. Everything that is safe on
  # every filesystem stays; the rest becomes a hyphen, so the name still says which release the
  # download is and still sorts beside its siblings.
  defp filename_key(key), do: String.replace(key, ~r/[^A-Za-z0-9._+-]/, "-")
end

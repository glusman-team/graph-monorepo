defmodule EdgeLinkoutsWeb.EdgeLive do
  @moduledoc """
  The linkout page for one edge: the display-config claim, the version switcher,
  the diff against the previous version, and the evidence panel.

  One Cosmos read per page view: `mount/3` reads nothing, and `handle_params/3` only calls
  the backend when the id changed. The blob carries every stored version, so switching
  `?version=` re-resolves from the assigns and never triggers a second read.
  """

  use EdgeLinkoutsWeb, :live_view

  require Logger

  alias EdgeLinkouts.{Codec, Cosmos, Display}
  alias EdgeLinkoutsWeb.{Diff, EdgeComponents, Edges}

  @impl true
  def mount(_params, _session, socket) do
    # The header's "Random edge" button is scoped to this edge's graph, which is not known
    # until a document has been read; nil keeps it global for every state without one.
    # random_version pins it to the release being read, so random clicks stay in the version
    # the reader is on until they switch versions; nil means the graph's every release.
    {:ok, assign(socket, kg_slug: nil, random_version: nil, scope: nil)}
  end

  @impl true
  def handle_params(%{"id" => id} = params, uri, socket) do
    if socket.assigns[:edge_id] == id and socket.assigns[:blob] do
      {:noreply, select_version(socket, params)}
    else
      {:noreply, load(socket, id, params, uri)}
    end
  end

  @impl true
  def handle_event("retry", _params, socket) do
    {:noreply,
     load(socket, socket.assigns.edge_id, socket.assigns.params, socket.assigns.permalink)}
  end

  defp load(socket, id, params, uri) do
    socket = assign(socket, edge_id: id, params: params, permalink: uri)

    case Edges.fetch_edge(id) do
      {:ok, stored} ->
        case Cosmos.dictionary_for(stored) do
          {:ok, dict} ->
            case Codec.decode(stored["b"], dict) do
              {:ok, blob} ->
                select_version(assign(socket, blob: blob), params)

              {:error, reason} ->
                assign(socket, view: {:corrupt, human_reason(reason)}, blob: nil)
            end

          {:error, reason} ->
            assign(socket, view: {:corrupt, human_reason(reason)}, blob: nil)
        end

      {:error, :rate_limited} ->
        assign(socket, view: :rate_limited, blob: nil)

      {:error, :not_found} ->
        # plug_status 404 makes the endpoint render the 404 page with a genuine 404
        # status, so an id typo does not look like success to a crawler or a monitor.
        # (Phoenix re-raises the exception after sending the response; that is its
        # standard behaviour for every rendered error except NoRouteError, whose special
        # handling needs a conn a LiveView does not have.)
        raise EdgeLinkoutsWeb.EdgeNotFound, message: "no edge with id #{inspect(id)} is stored"

      {:error, reason} ->
        # The store itself failed (network, auth): retryable like throttling, but the
        # page must not claim the RU budget was hit. The reason stays in the logs.
        Logger.warning("edge #{inspect(id)} read failed: #{inspect(reason)}")
        assign(socket, view: :store_error, blob: nil)
    end
  end

  defp select_version(socket, params) do
    blob = socket.assigns.blob
    versions = Codec.versions(blob)
    requested = params["version"]

    if is_binary(requested) and requested not in versions do
      assign(socket,
        view: {:corrupt, "the blob does not store a version named #{inspect(requested)}"},
        diff: nil,
        scope: nil
      )
    else
      key = if is_binary(requested), do: requested, else: Codec.newest(blob)
      render_version(socket, blob, versions, key, scope(params))
    end
  end

  # The diff scoping param (?change=publications): the raw stored field key, never the
  # display label, so a link stays stable however the vocabulary moves. The panel itself
  # falls back to the full diff when the key matches nothing in this release.
  defp scope(%{"change" => field}) when is_binary(field) and field != "", do: field
  defp scope(_params), do: nil

  defp render_version(socket, blob, versions, key, scope) do
    case Codec.resolve(blob, key) do
      {:ok, doc} ->
        config = Display.for_key(key)
        prev_key = previous_version(versions, key)

        assign(socket,
          view: :ok,
          versions: versions,
          history: history(blob, versions),
          key: key,
          version_label: version_label(key),
          doc: doc,
          kg_name: config.display_name || Codec.kg_name(key),
          kg_slug: Display.slug(Codec.kg_name(key)),
          random_version: version_label(key),
          sentence: sentence(config, doc, key),
          asserts: asserts(config, doc, key),
          methods: Display.methods(config, doc, key),
          evidence: evidence(config, doc, key),
          footnote: footnote(config, doc, key),
          known_errors: known_errors(config, key),
          diff: diff(blob, prev_key, doc),
          prev_key: prev_key,
          scope: scope,
          config: config,
          feedback_url:
            Display.feedback_url(config, socket.assigns.edge_id, socket.assigns.permalink),
          page_title: page_title(config, doc, key, socket.assigns.edge_id)
        )

      {:error, reason} ->
        assign(socket, view: {:corrupt, human_reason(reason)}, diff: nil)
    end
  end

  defp sentence(config, doc, key), do: Display.edge(config, doc, key)

  defp asserts(config, doc, key), do: Display.asserts(config, doc, key)

  defp evidence(config, doc, key), do: Display.evidence(config, doc, key)

  defp footnote(config, doc, key), do: Display.footnote(config, doc, key)

  defp known_errors(config, key), do: Display.known_errors(config, key)

  defp page_title(config, doc, key, _id), do: Display.title(config, doc, key)

  defp previous_version(versions, key) do
    versions
    |> Enum.split_while(&(&1 != key))
    |> elem(0)
    |> List.last()
  end

  @doc false
  # The timeline beside the diff: every stored version with the number of fields its release
  # changed. Resolving each version is a local blob lookup, never a store read, so the whole
  # history costs nothing extra and a reader can see at a glance which release moved.
  def history(blob, versions) do
    [nil | versions]
    |> Enum.chunk_every(2, 1)
    |> Enum.map(fn
      # The final one-element chunk is the newest version, already covered as a `key`.
      [_] ->
        nil

      [nil, key] ->
        %{key: key, label: version_label(key), changes: nil}

      [prev, key] ->
        changes =
          with {:ok, prev_doc} <- Codec.resolve(blob, prev),
               {:ok, doc} <- Codec.resolve(blob, key) do
            length(Diff.diff(prev_doc, doc))
          else
            _ -> nil
          end

        %{key: key, label: version_label(key), changes: changes}
    end)
    |> Enum.reject(&is_nil/1)
    # The blob's order is the order versions were merged, which is load history, not
    # version order; a release ingested late but numbered lower would sit at the wrong end.
    # The timeline reads newest first and decreases to the right ("1.23.4, 1.23.3, ..."),
    # matching the home page pill row, no matter how the store accumulated the releases.
    |> Enum.sort_by(& &1.label, fn a, b -> Codec.compare_versions(a, b) in [:gt, :eq] end)
  end

  # "infores:drugapprovals-kp-1.16.0" carries the KG name on every pill, which is noise once
  # the page already says which KG it is. The release number is the part a reader compares.
  def version_label(key) do
    case Display.name_of(key) do
      nil -> key
      name -> String.replace_prefix(key, name <> "-", "")
    end
  end

  # nil means "the previous version could not be resolved", which the diff panel states
  # explicitly rather than pretending nothing changed.
  defp diff(_blob, nil, _doc), do: nil

  defp diff(blob, prev_key, doc) do
    case Codec.resolve(blob, prev_key) do
      {:ok, prev_doc} -> Diff.diff(prev_doc, doc)
      {:error, _reason} -> nil
    end
  end

  defp human_reason(:invalid_base64), do: "the stored frame is not valid base64"
  defp human_reason(:no_versions), do: "the blob stores no versions"

  defp human_reason({:zstd, detail}) do
    "the stored frame failed to decompress (#{detail})"
  end

  defp human_reason({:json, detail}) do
    "the stored frame is not valid JSON (#{detail})"
  end

  defp human_reason({:schema, found, expected}) do
    "unknown blob schema #{inspect(found)} (expected #{inspect(expected)})"
  end

  defp human_reason({:not_a_blob, keys}),
    do: "the document is not a versioned blob (#{inspect(keys)})"

  defp human_reason({:unknown_version, version, _order}) do
    "the blob does not store a version named #{inspect(version)}"
  end

  defp human_reason({:cycle, version}), do: "the delta chain for #{inspect(version)} is circular"

  defp human_reason({:unknown_dict, id}),
    do: "the document was compressed with a dictionary this build does not carry (#{id})"

  defp human_reason({:null_at, path}), do: "a null value at #{path}"
  defp human_reason({:null_at, path, version}), do: "a null value at #{path} (#{version})"

  defp human_reason(other), do: "the blob is malformed (#{inspect(other)})"
end

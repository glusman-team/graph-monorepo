defmodule EdgeLinkouts.Codec do
  @moduledoc """
  The Elixir half of the blob wire format.

  This module must agree with the Go worker's codec (dagster/projects/linkouts/workers/go/
  internal/codec) byte for byte, because that worker writes the
  documents this reads and hashes canonical bytes to decide what changed. The format is
  specified in ADR 0001 (wire format) on the documentation site; `test/contract_test.exs` checks the two
  implementations against committed fixtures rather than trusting this comment.

  The reader only ever decodes: it never writes a document back. That keeps one side of the
  contract authoritative and means a display bug cannot corrupt stored evidence.
  """

  @blob_schema "edgelinkouts.blob/1"
  @pool_schema "edgelinkouts.pool/1"
  @pool_index_schema "edgelinkouts.pool_index/1"

  @target "$t"
  @set "$set"
  @add "$add"
  @del "$del"

  defstruct versions: %{}, order: []

  @type version_key :: String.t()
  @type doc :: %{optional(String.t()) => term()}
  @type entry ::
          %{required(:kind) => :full | :delta}
          | %{required(:kind) => :delta, required(:base) => version_key()}
  @type t :: %__MODULE__{versions: %{version_key() => entry()}, order: [version_key()]}

  @type pool :: %{ids: [String.t()], key: String.t() | nil, sampled_at: String.t() | nil}

  @type release :: %{
          edges: non_neg_integer(),
          sampled: non_neg_integer(),
          sampled_at: String.t() | nil
        }
  @type pool_index :: %{String.t() => %{String.t() => release()}}

  # ---------------------------------------------------------------- decoding

  @doc """
  Decodes one stored document: base64, then zstd, then canonical JSON.

  `dictionary` must be the dictionary the frame was written with, or `nil` for a frame written
  without one. A dict-compressed frame decoded with no dictionary fails here rather than
  returning garbage, which is the difference between a 500 and a wrong answer on screen.
  """
  @spec decode(String.t(), binary() | nil) :: {:ok, t()} | {:error, term()}
  def decode(encoded, dictionary \\ nil) when is_binary(encoded) do
    with {:ok, frame} <- decode64(encoded),
         {:ok, raw} <- decompress(frame, dictionary),
         {:ok, %{"schema" => schema, "versions" => versions}} <- decode_json(raw),
         :ok <- check_schema(schema, @blob_schema),
         {:ok, parsed} <- parse_versions(versions) do
      {:ok, %__MODULE__{versions: parsed, order: sort_keys(Map.keys(parsed))}}
    else
      # `with` returns the value that failed to match, so without this clause a pool document
      # (no "versions" key) would come back as {:ok, <the raw map>} and be treated as a blob.
      {:ok, %{"schema" => other}} -> {:error, {:schema, other, @blob_schema}}
      {:ok, payload} when is_map(payload) -> {:error, {:not_a_blob, Map.keys(payload)}}
      {:error, reason} -> {:error, reason}
      other -> {:error, {:unexpected_payload, other}}
    end
  end

  @doc "Decodes one release's random pool: the sampled edge ids behind `/<kg>/random`."
  @spec decode_pool(String.t(), binary() | nil) :: {:ok, pool()} | {:error, term()}
  def decode_pool(encoded, dictionary \\ nil) when is_binary(encoded) do
    with {:ok, frame} <- decode64(encoded),
         {:ok, raw} <- decompress(frame, dictionary),
         {:ok, %{"schema" => schema} = payload} <- decode_json(raw),
         :ok <- check_schema(schema, @pool_schema) do
      ids = payload |> Map.get("ids", []) |> Enum.filter(&is_binary/1)

      {:ok, %{ids: ids, key: payload["key"], sampled_at: payload["sampled_at"]}}
    else
      {:ok, %{"schema" => other}} -> {:error, {:schema, other, @pool_schema}}
      {:ok, payload} when is_map(payload) -> {:error, {:not_a_pool, Map.keys(payload)}}
      {:error, reason} -> {:error, reason}
      other -> {:error, {:unexpected_payload, other}}
    end
  end

  @doc """
  Decodes the reserved pool index: which releases have a random pool, and how big each one is.

  Returns `%{kg_slug => %{version_label => %{edges: n, sampled: n, sampled_at: iso}}}`. The index
  carries counts and no ids, which is what keeps it under a kilobyte however many graphs are
  loaded and lets a weighted pick stay uniform across releases without reading any of their ids.

  A wrong schema is refused like every other document (ADR 0001). A right schema with a malformed
  entry is not: one unusable release is skipped rather than failing the page that lists all of
  them, because the index is metadata the worker rewrites on every load.
  """
  @spec decode_pool_index(String.t(), binary() | nil) :: {:ok, pool_index()} | {:error, term()}
  def decode_pool_index(encoded, dictionary \\ nil) when is_binary(encoded) do
    with {:ok, frame} <- decode64(encoded),
         {:ok, raw} <- decompress(frame, dictionary),
         {:ok, %{"schema" => schema} = payload} <- decode_json(raw),
         :ok <- check_schema(schema, @pool_index_schema) do
      {:ok, parse_pool_index(Map.get(payload, "kgs"))}
    else
      {:ok, %{"schema" => other}} -> {:error, {:schema, other, @pool_index_schema}}
      {:ok, payload} when is_map(payload) -> {:error, {:not_a_pool_index, Map.keys(payload)}}
      {:error, reason} -> {:error, reason}
      other -> {:error, {:unexpected_payload, other}}
    end
  end

  defp parse_pool_index(kgs) when is_map(kgs) do
    for {slug, %{"versions" => versions}} when is_binary(slug) and is_map(versions) <- kgs,
        into: %{} do
      {slug, parse_releases(versions)}
    end
  end

  defp parse_pool_index(_other), do: %{}

  defp parse_releases(versions) do
    for {label, stats} when is_binary(label) and is_map(stats) <- versions,
        into: %{} do
      {label,
       %{
         edges: count(stats["edges"]),
         sampled: count(stats["sampled"]),
         sampled_at: nilify(stats["sampled_at"])
       }}
    end
  end

  defp count(n) when is_integer(n) and n >= 0, do: n
  defp count(_other), do: 0

  defp nilify(v) when is_binary(v), do: v
  defp nilify(_other), do: nil

  defp decode64(encoded) do
    case Base.decode64(encoded) do
      {:ok, frame} -> {:ok, frame}
      :error -> {:error, :invalid_base64}
    end
  end

  defp decompress(frame, nil) do
    {:ok, frame |> :zstd.decompress() |> IO.iodata_to_binary()}
  rescue
    e -> {:error, {:zstd, Exception.message(e)}}
  end

  defp decompress(frame, dictionary) when is_binary(dictionary) do
    {:ok, frame |> :zstd.decompress(%{dictionary: dictionary}) |> IO.iodata_to_binary()}
  rescue
    e ->
      # The single most likely cause is the wrong dictionary, so say that instead of
      # surfacing a bare Erlang argument error.
      {:error,
       {:zstd,
        "cannot decompress (is this the dictionary the document was written with?): #{Exception.message(e)}"}}
  end

  defp decode_json(raw) do
    {:ok, JSON.decode!(raw)}
  rescue
    e -> {:error, {:json, Exception.message(e)}}
  end

  defp check_schema(found, expected) when found == expected, do: :ok
  defp check_schema(found, expected), do: {:error, {:schema, found, expected}}

  defp parse_versions(versions) when is_map(versions) do
    if versions == %{} do
      {:error, :no_versions}
    else
      Enum.reduce_while(versions, {:ok, %{}}, fn {key, payload}, {:ok, acc} ->
        case parse_entry(payload) do
          {:ok, entry} -> {:cont, {:ok, Map.put(acc, key, entry)}}
          {:error, reason} -> {:halt, {:error, {:version, key, reason}}}
        end
      end)
    end
  end

  defp parse_versions(_), do: {:error, :versions_not_an_object}

  defp parse_entry(%{} = payload) do
    case Map.fetch(payload, @target) do
      :error -> parse_full(payload)
      {:ok, base} when is_binary(base) and base != "" -> parse_delta(payload, base)
      {:ok, other} -> {:error, {:bad_target, other}}
    end
  end

  defp parse_entry(other), do: {:error, {:not_an_object, other}}

  # A full document carries no "$" keys at all. Nulls are rejected because a stored null is exactly
  # what this pipeline exists to prevent, and one here means the writer regressed.
  defp parse_full(payload) do
    case reject_nulls(payload) do
      :ok -> {:ok, %{kind: :full, doc: payload}}
      {:error, path} -> {:error, {:null_at, path}}
    end
  end

  defp parse_delta(payload, base) do
    with {:ok, set} <- fetch_object(payload, @set, :allow_empty),
         {:ok, add} <- fetch_add(payload),
         {:ok, del} <- fetch_strings(payload, @del) do
      if set == nil and add == nil and del == nil do
        # An empty-but-present $set means "identical to base"; omitting all three is corrupt.
        {:error, :delta_without_operations}
      else
        {:ok, %{kind: :delta, base: base, set: set || %{}, add: add || %{}, del: del || []}}
      end
    end
  end

  # An absent key is nil; a present key must be an object. The distinction matters because
  # {"$t": "v", "$set": {}} is legal and means "unchanged".
  defp fetch_object(payload, key, :allow_empty) do
    case Map.fetch(payload, key) do
      :error -> {:ok, nil}
      {:ok, value} when is_map(value) -> {:ok, value}
      {:ok, other} -> {:error, {key, :not_an_object, other}}
    end
  end

  defp fetch_add(payload) do
    with {:ok, raw} <- fetch_object(payload, @add, :allow_empty) do
      case raw do
        nil ->
          {:ok, nil}

        map ->
          Enum.reduce_while(map, {:ok, %{}}, fn
            {k, v}, {:ok, acc} when is_list(v) -> {:cont, {:ok, Map.put(acc, k, v)}}
            {k, v}, _ -> {:halt, {:error, {@add, k, :not_a_list, v}}}
          end)
      end
    end
  end

  defp fetch_strings(payload, key) do
    case Map.fetch(payload, key) do
      :error ->
        {:ok, nil}

      {:ok, list} when is_list(list) ->
        if Enum.all?(list, &is_binary/1), do: {:ok, list}, else: {:error, {key, :not_all_strings}}

      {:ok, other} ->
        {:error, {key, :not_a_list, other}}
    end
  end

  @doc """
  Rejects nulls at any depth, returning the JSON-ish path of the first one found.

  Elixir decodes JSON `null` to `nil`, and `nil` is also how Elixir spells "absent", so this
  check is what keeps a missing field from being indistinguishable from a stored null.
  """
  @spec reject_nulls(term()) :: :ok | {:error, String.t()}
  def reject_nulls(term), do: reject_nulls(term, "$")

  defp reject_nulls(nil, path), do: {:error, path}

  defp reject_nulls(map, path) when is_map(map) do
    map
    |> Enum.sort_by(fn {k, _} -> k end)
    |> Enum.reduce_while(:ok, fn {k, v}, :ok ->
      case reject_nulls(v, path <> "." <> k) do
        :ok -> {:cont, :ok}
        {:error, _} = err -> {:halt, err}
      end
    end)
  end

  defp reject_nulls(list, path) when is_list(list) do
    list
    |> Enum.with_index()
    |> Enum.reduce_while(:ok, fn {v, i}, :ok ->
      case reject_nulls(v, "#{path}[#{i}]") do
        :ok -> {:cont, :ok}
        {:error, _} = err -> {:halt, err}
      end
    end)
  end

  defp reject_nulls(_scalar, _path), do: :ok

  # ---------------------------------------------------------------- resolving

  @doc "Version keys, oldest first, in the same order the worker and the UI use."
  @spec versions(t()) :: [version_key()]
  def versions(%__MODULE__{order: order}), do: order

  @doc "The newest stored version key."
  @spec newest(t()) :: version_key() | nil
  def newest(%__MODULE__{order: []}), do: nil
  def newest(%__MODULE__{order: order}), do: List.last(order)

  @doc """
  Expands one version into a full document, walking the delta chain.

  Cycles and missing targets are errors: a corrupt blob must produce a 500 with a clear reason,
  not an infinite loop that takes the endpoint down.
  """
  @spec resolve(t(), version_key()) :: {:ok, doc()} | {:error, term()}
  def resolve(%__MODULE__{} = blob, version), do: do_resolve(blob, version, %{})

  @spec do_resolve(t(), version_key(), %{optional(version_key()) => true}) ::
          {:ok, doc()} | {:error, term()}
  defp do_resolve(blob, version, seen) do
    cond do
      Map.has_key?(seen, version) ->
        {:error, {:cycle, version}}

      not is_map_key(blob.versions, version) ->
        {:error, {:unknown_version, version, blob.order}}

      true ->
        # Marked for this path only, so two versions sharing one base (a diamond) still resolve.
        seen = Map.put(seen, version, true)

        case Map.fetch!(blob.versions, version) do
          %{kind: :full, doc: doc} ->
            {:ok, doc}

          %{kind: :delta, base: base} = entry ->
            with {:ok, base_doc} <- do_resolve(blob, base, seen) do
              apply_delta(base_doc, entry, version)
            end
        end
    end
  end

  # Application order is $del, then $set, then $add. Changing it changes results when one
  # operation removes a key another adds to, so the order is part of the contract.
  defp apply_delta(base, %{set: set, add: add, del: del}, version) do
    merged = base |> Map.drop(del) |> Map.merge(set)

    with {:ok, doc} <- apply_add(merged, add, version) do
      case reject_nulls(doc) do
        :ok -> {:ok, doc}
        {:error, path} -> {:error, {:null_at, path, version}}
      end
    end
  end

  defp apply_add(doc, add, _version) when add == %{}, do: {:ok, doc}

  defp apply_add(doc, add, version) do
    Enum.reduce_while(add, {:ok, doc}, fn {key, extra}, {:ok, acc} ->
      case Map.fetch(acc, key) do
        {:ok, existing} when is_list(existing) ->
          {:cont, {:ok, Map.put(acc, key, existing ++ extra)}}

        {:ok, other} ->
          {:halt, {:error, {:add_not_a_list, version, key, other}}}

        :error ->
          # $add against a key the base does not have cannot be applied honestly: inventing an
          # empty list would hide a writer bug behind a plausible-looking document.
          {:halt, {:error, {:add_missing_key, version, key}}}
      end
    end)
  end

  # ---------------------------------------------------------------- encoding

  @doc """
  Encodes a term as canonical JSON: sorted keys at every depth, compact, no HTML escaping.

  Used for KGX downloads and for hashing. Key order is sorted explicitly rather than relying
  on map iteration order, which is term order only for small maps and is not part of any
  documented guarantee.
  """
  @spec canonical(term()) :: iodata()
  def canonical(nil), do: "null"
  def canonical(true), do: "true"
  def canonical(false), do: "false"
  def canonical(value) when is_integer(value), do: Integer.to_string(value)
  def canonical(value) when is_float(value), do: float_to_canonical(value)
  def canonical(value) when is_atom(value), do: encode_string(Atom.to_string(value))
  def canonical(value) when is_binary(value), do: encode_string(value)

  def canonical(value) when is_list(value) do
    ["[", value |> Enum.map(&canonical/1) |> Enum.intersperse(","), "]"]
  end

  def canonical(value) when is_map(value) do
    pairs =
      value
      |> Enum.sort_by(fn {k, _} -> to_string(k) end)
      |> Enum.map(fn {k, v} -> [encode_string(to_string(k)), ":", canonical(v)] end)
      |> Enum.intersperse(",")

    ["{", pairs, "}"]
  end

  @doc "Encodes to canonical JSON as a single binary."
  @spec canonical_binary(term()) :: binary()
  def canonical_binary(term), do: term |> canonical() |> IO.iodata_to_binary()

  # Floats must not gain or lose precision against the worker's json.Number text, so only
  # integers-valued floats are emitted bare; anything else goes through :erlang.float_to_binary
  # with :short, which matches Go's strconv shortest-form output.
  defp float_to_canonical(value) when value == trunc(value) and abs(value) < 1.0e15 do
    Integer.to_string(trunc(value))
  end

  defp float_to_canonical(value) do
    :erlang.float_to_binary(value, [:short])
  end

  defp encode_string(string) do
    [?", escape(string, []), ?"]
  end

  # Only the JSON-mandatory escapes. Notably '/' is not escaped and neither are '<', '>' or
  # '&': the Go side runs with EscapeHTML off so the two agree byte for byte.
  defp escape(<<>>, acc), do: Enum.reverse(acc)
  defp escape(<<?", rest::binary>>, acc), do: escape(rest, [~S(\") | acc])
  defp escape(<<?\\, rest::binary>>, acc), do: escape(rest, [~S(\\) | acc])
  defp escape(<<?\n, rest::binary>>, acc), do: escape(rest, ["\\n" | acc])
  defp escape(<<?\r, rest::binary>>, acc), do: escape(rest, ["\\r" | acc])
  defp escape(<<?\t, rest::binary>>, acc), do: escape(rest, ["\\t" | acc])
  defp escape(<<?\b, rest::binary>>, acc), do: escape(rest, ["\\b" | acc])
  defp escape(<<?\f, rest::binary>>, acc), do: escape(rest, ["\\f" | acc])

  defp escape(<<c, rest::binary>>, acc) when c < 0x20 do
    escape(rest, [:io_lib.format("\\u~4.16.0B", [c]) | acc])
  end

  defp escape(<<c::utf8, rest::binary>>, acc), do: escape(rest, [<<c::utf8>> | acc])
  defp escape(<<c, rest::binary>>, acc), do: escape(rest, [<<c>> | acc])

  # ---------------------------------------------------------------- version keys

  @doc """
  Orders version keys the way the worker does: by KG name, then by numeric version parts.

  String comparison would put 1.9.0 after 1.16.0 and make the version switcher list releases
  out of order, so the numeric parts are compared as integers.
  """
  @spec sort_keys([version_key()]) :: [version_key()]
  def sort_keys(keys), do: Enum.sort(keys, &(compare_keys(&1, &2) != :gt))

  @doc "Compares two `<kg>-<version>` keys, returning `:lt`, `:eq` or `:gt`."
  @spec compare_keys(version_key(), version_key()) :: :lt | :eq | :gt
  def compare_keys(a, b) do
    {kg_a, _parts_a, _suffix_a} = split_key(a)
    {kg_b, _parts_b, _suffix_b} = split_key(b)
    if kg_a == kg_b, do: compare_versions(a, b), else: compare_terms(kg_a, kg_b)
  end

  @doc """
  Compares two version strings numerically, component by component.

  Accepts either bare versions (`"1.16.0"`) or full keys (`"infores:drugapprovals-kp-1.16.0"`),
  so the display layer can gate on `{:version, ">1.0.0"}` using the same ordering that picks the
  newest stored version. Two orderings that disagree would make a config's gate mean something different
  from the version list beside it.

  `1.9.0 < 1.16.0` (numeric, not lexicographic), and a final release sorts after its own
  pre-releases, so `1.16.0-rc.1` does not become the version a page defaults to.
  """
  @spec compare_versions(String.t(), String.t()) :: :lt | :eq | :gt
  def compare_versions(a, b) do
    {_kg_a, parts_a, suffix_a} = split_key(a)
    {_kg_b, parts_b, suffix_b} = split_key(b)

    if parts_a == parts_b,
      do: compare_suffix(suffix_a, suffix_b),
      else: compare_parts(parts_a, parts_b)
  end

  @doc """
  The newest release label among the ones given, or nil for an empty list.

  Accepts bare versions or full keys. Both places that tag a release "latest"
  (the home bar's pill row and the edge page's version timeline) derive it from
  this one function, so the two pages can never disagree about which release is
  newest.
  """
  @spec newest_label([version_key()]) :: version_key() | nil
  def newest_label([]), do: nil

  def newest_label([label | rest]) do
    Enum.reduce(rest, label, fn next, acc ->
      if compare_versions(next, acc) == :gt, do: next, else: acc
    end)
  end

  @doc "Splits `<kg>-<version>` into `{kg, numeric_parts, suffix_text}`."
  @spec split_key(version_key()) :: {String.t(), [non_neg_integer()], String.t()}
  def split_key(key) when is_binary(key) do
    case Regex.run(~r/^(.*)-(\d[0-9a-zA-Z.\-+]*)$/, key) do
      [_, kg, version] ->
        {dotted, suffix} = split_suffix(version)

        parts =
          dotted
          |> String.split(".")
          |> Enum.map(&parse_part/1)

        {kg, parts, suffix}

      # A bare version ("1.16.0") has no KG prefix to split off, but it is still a
      # version: parse its parts so numeric comparison applies. Anything that is not
      # version-shaped keeps the fallthrough ordering.
      _ ->
        case Regex.run(~r/^(\d[0-9a-zA-Z.\-+]*)$/, key) do
          [_, version] ->
            {dotted, suffix} = split_suffix(version)

            parts =
              dotted
              |> String.split(".")
              |> Enum.map(&parse_part/1)

            {key, parts, suffix}

          _ ->
            {key, [], key}
        end
    end
  end

  defp split_suffix(version) do
    case Regex.run(~r/^([0-9.]+)(.*)$/, version) do
      [_, dotted, suffix] -> {dotted, suffix}
      _ -> {version, ""}
    end
  end

  defp parse_part(part) do
    case Integer.parse(part) do
      {n, ""} -> n
      _ -> 0
    end
  end

  # Shorter version lists are zero-padded so 1.2 compares equal to 1.2.0. The padding has to
  # live in a different function from the element-wise walk, or the catch-all clause shadows
  # the recursive ones and loops forever.
  defp compare_parts(a, b) do
    len = max(length(a), length(b))
    compare_padded(pad(a, len), pad(b, len))
  end

  defp pad(list, len), do: list ++ List.duplicate(0, len - length(list))

  defp compare_padded([], []), do: :eq
  defp compare_padded([h | t1], [h | t2]), do: compare_padded(t1, t2)
  defp compare_padded([a | _], [b | _]), do: compare_terms(a, b)

  # A final release (no suffix) sorts after any pre-release of the same number, which a plain
  # string compare gets backwards.
  defp compare_suffix("", ""), do: :eq
  defp compare_suffix("", _pre), do: :gt
  defp compare_suffix(_pre, ""), do: :lt
  defp compare_suffix(a, b), do: compare_terms(a, b)

  defp compare_terms(a, b) when a < b, do: :lt
  defp compare_terms(a, b) when a > b, do: :gt
  defp compare_terms(_, _), do: :eq

  @doc "The KG name part of a key, which selects the display configuration."
  @spec kg_name(version_key()) :: String.t()
  def kg_name(key) do
    {kg, _parts, _suffix} = split_key(key)
    kg
  end
end

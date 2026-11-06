defmodule EdgeLinkouts.Dicts do
  @moduledoc """
  The zstd dictionary registry: `%{dictionary_id => dictionary_bytes}` for every `.dict`
  file under `priv/zstd/` (or the directory in `:zstd_dict_dir`).

  Storage format v2 writes each document's blob compressed with a trained dictionary and
  stamps the dictionary's id into the envelope's `d` field. A read resolves the id through
  this registry; an id the app does not know is a loud error, never a guess. The registry
  is keyed by the id embedded in each dictionary's header (bytes 4-8 little-endian after
  the magic `0xEC30A437`), not by filename, so the file on disk cannot disagree with the
  identity it claims.

  Multiple dictionaries coexist - one per KG, or an old and a new training of the same KG -
  which is what makes retraining a drop-in operation: old documents keep decoding against
  the old id for as long as its file ships.

  Loaded once at boot (a `persistent_term` entry); the files are public data derived from
  public knowledge graphs, so carrying them in the release costs nothing and a read never
  touches the filesystem.
  """

  # https://github.com/facebook/zstd/blob/dev/doc/zstd_compression_format.md
  @magic 0xEC30A437

  @key {__MODULE__, :registry}

  @doc """
  Loads every `.dict` file in `dir` into `%{id => bytes}`. Missing directory returns an
  empty map; a malformed file or a duplicated id raises, because booting with a registry
  that answers the wrong dictionary is worse than not booting.
  """
  @spec load!(String.t()) :: %{non_neg_integer() => binary()}
  def load!(dir) do
    if File.dir?(dir) do
      dir
      |> Path.join("*.dict")
      |> Path.wildcard()
      |> Enum.reduce(%{}, fn path, acc ->
        bytes = File.read!(path)
        id = dict_id!(bytes, path)

        if Map.has_key?(acc, id) do
          raise ArgumentError,
                "duplicate zstd dictionary id #{id} (#{path}); " <>
                  "two different dictionaries with one id would decode each other's frames"
        end

        Map.put(acc, id, bytes)
      end)
    else
      %{}
    end
  end

  @doc """
  (Re)builds the live registry from the configured directory and stores it for readers.
  Called at application start; exposed for tests that point the app at a fixture dir.
  """
  @spec reload!() :: %{non_neg_integer() => binary()}
  def reload! do
    registry =
      case Application.get_env(:edge_linkouts, :zstd_dict_dir) do
        nil -> load!(Path.join(:code.priv_dir(:edge_linkouts) |> to_string(), "zstd"))
        dir -> load!(dir)
      end

    :persistent_term.put(@key, registry)
    registry
  end

  @doc "The live registry; `%{}` until `reload!/0` has run."
  @spec registry() :: %{non_neg_integer() => binary()}
  def registry, do: :persistent_term.get(@key, %{})

  @doc """
  The dictionary for one stored document: `{:ok, nil}` when the document declares none
  (`d` absent or 0), `{:ok, bytes}` for a known id, and `{:error, {:unknown_dict, id}}`
  when the document was written against a dictionary this build does not carry - decoding
  it with anything else would produce garbage, so the read fails loudly.
  """
  @spec for_doc(map(), %{non_neg_integer() => binary()}) ::
          {:ok, binary() | nil}
          | {:error, {:unknown_dict, non_neg_integer()} | {:bad_dict_field, term()}}
  def for_doc(doc, registry \\ registry()) do
    case Map.get(doc, "d") do
      nil ->
        {:ok, nil}

      0 ->
        {:ok, nil}

      id when is_integer(id) ->
        case registry do
          %{^id => dict} -> {:ok, dict}
          _ -> {:error, {:unknown_dict, id}}
        end

      # The writer only emits a uint32 or omits the field; anything else is a corrupted or
      # hand-edited document, and it gets the same per-request error as an unknown id
      # instead of a CaseClauseError crash.
      other ->
        {:error, {:bad_dict_field, other}}
    end
  end

  defp dict_id!(<<@magic::little-32, id::little-32, _rest::binary>>, _path), do: id

  defp dict_id!(_bytes, path),
    do: raise(ArgumentError, "#{path} is not a full zstd dictionary (no magic/id header)")
end

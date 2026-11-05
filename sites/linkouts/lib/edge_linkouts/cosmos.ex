defmodule EdgeLinkouts.Cosmos do
  @moduledoc """
  Read-only access to the Cosmos DB edge documents.

  The behaviour has exactly one operation: a point read of one document by id. The web app
  never writes, and never queries: the container's indexing policy is `none`, so a query
  would be a full scan at full price (ADR 0001 on the docs site). The only credential
  configured here is the account's read-only key.

  `get_edge/1` returns the *stored* document (`%{"id" => ..., "b" => ..., "d" => ...}`),
  not a decoded blob: decoding needs the display configuration and the zstd dictionary,
  which is `EdgeLinkouts.Codec`'s concern, not transport's. Its name says "edge" because
  that is what it was written for, but it reads any reserved document id too — the pool
  index and one release's random pool are documents in the same container, which is how
  `/random` stays a point read instead of a query.

  Reserved document ids (`__random_pool__` and friends) never collide with an edge id,
  because an edge id is a UUID and a reserved id starts with two underscores.

  Backends:

  - `EdgeLinkouts.Cosmos.HTTP` - Finch + master-key HMAC, selected in production.
  - `EdgeLinkouts.Cosmos.File` - NDJSON file written by the worker's file store, for dev.
  - `EdgeLinkouts.Cosmos.Fake` - in-memory, programmable failures, started only in tests.

  The backend is resolved per call from `config :edge_linkouts, :cosmos_backend` so tests
  can swap it without recompiling.
  """

  @callback get_edge(id :: String.t()) :: {:ok, stored :: map()} | {:error, term()}

  @pool_index_id "__random_pool__"
  @pool_id_separator ":"

  @doc """
  The reserved document id holding the pool index: which releases have a random pool.

  One point read answers "what graphs exist and what are their versions", so the home page
  needs no query against an unindexed container.
  """
  @spec pool_index_id() :: String.t()
  def pool_index_id, do: @pool_index_id

  @doc """
  The reserved document id holding one release's sampled edge ids.

  `slug` is the KG name without its `infores:` prefix, the same form used in URLs, so the id
  doubles as the thing a person can read in the portal:
  `__random_pool__:drugapprovals-kp:1.16.0`.
  """
  @spec pool_doc_id(String.t(), String.t()) :: String.t()
  def pool_doc_id(slug, version_label) do
    Enum.join([@pool_index_id, slug, version_label], @pool_id_separator)
  end

  @doc """
  Whether a document id belongs to the app rather than to an edge.

  Every reserved id starts with the pool index's, so the pool index and each release's pool
  match, and no edge id does: an edge id is a UUID. The worker applies the same rule
  (`cosmos.IsReservedID`), which is what lets a test split a fixture file into edges and
  metadata without knowing the naming scheme twice.
  """
  @spec reserved_id?(String.t()) :: boolean()
  def reserved_id?(id) when is_binary(id), do: String.starts_with?(id, @pool_index_id)

  @doc "The configured backend module; defaults to the real HTTP client."
  @spec impl() :: module()
  def impl do
    Application.get_env(:edge_linkouts, :cosmos_backend, EdgeLinkouts.Cosmos.HTTP)
  end

  @doc """
  The trained zstd dictionary a stored document needs, resolved by the id in its `d` field
  through the `EdgeLinkouts.Dicts` registry (`priv/zstd/*.dict`, loaded at boot). `{:ok, nil}`
  for a document that declares none; `{:error, {:unknown_dict, id}}` when the registry does
  not carry that dictionary, which fails the read loudly instead of decoding garbage.
  """
  @doc since: "0.2.0"
  @spec dictionary_for(map()) ::
          {:ok, binary() | nil} | {:error, {:unknown_dict, non_neg_integer()}}
  def dictionary_for(doc), do: EdgeLinkouts.Dicts.for_doc(doc)

  @doc """
  Decodes one release's stored pool document into its sampled edge ids.

  The dictionary is only loaded when the document declares one (`d` present and non-zero);
  a missing dictionary for a dict-compressed frame is an error from `:zstd`, which is the
  loud failure ADR 0001 asks for.
  """
  @doc since: "0.1.0"
  @doc section: :internal
  @spec decode_pool_doc(map()) :: {:ok, EdgeLinkouts.Codec.pool()} | {:error, term()}
  def decode_pool_doc(%{"b" => b64} = doc) do
    case dictionary_for(doc) do
      {:ok, dict} -> EdgeLinkouts.Codec.decode_pool(b64, dict)
      {:error, _} = err -> err
    end
  end

  def decode_pool_doc(other), do: {:error, {:not_a_pool_doc, other}}

  @doc "Decodes the stored pool index document. See `decode_pool_doc/1` for the dictionary rule."
  @doc since: "0.1.0"
  @doc section: :internal
  @spec decode_pool_index_doc(map()) :: {:ok, EdgeLinkouts.Codec.pool_index()} | {:error, term()}
  def decode_pool_index_doc(%{"b" => b64} = doc) do
    case dictionary_for(doc) do
      {:ok, dict} -> EdgeLinkouts.Codec.decode_pool_index(b64, dict)
      {:error, _} = err -> err
    end
  end

  def decode_pool_index_doc(other), do: {:error, {:not_a_pool_index_doc, other}}
end

defmodule EdgeLinkoutsWeb.Edges do
  @moduledoc """
  The single Cosmos read path for the web app.

  Every page view goes through `fetch_edge/1` exactly once, no matter how many components need
  the document: the LiveView keeps the decoded blob in its assigns, so switching `?version=`
  re-resolves locally and never touches the backend again. `/random` adds two more point reads,
  `fetch_pool_index/0` and `fetch_pool/2`. Reads are coalesced (`Dedupe.execute/3`), so N
  browsers asking for the same id within a millisecond produce one backend call. The RU budget is
  enforced by the transport (`EdgeLinkouts.Cosmos.HTTP`), which knows the real request charge.

  Nothing here queries the container: its indexing policy is `none`, so a query would be a full
  scan billed as such. Every read is by document id, which is 1 RU for a document up to 1 KB.
  """

  alias EdgeLinkouts.{Codec, Cosmos, Dedupe}

  # An edge document may go stale after 30 s without anyone noticing, but the pool index and one
  # release's pool are rewritten only when the worker loads a release, so their results can be
  # replayed far longer. Fifteen minutes turns "one read per home page view" into "one read per
  # machine per quarter hour" for documents that are under a kilobyte each. Configurable via
  # :pool_ttl_ms (env POOL_TTL_MS).
  @default_pool_ttl_ms 15 * 60 * 1000

  @doc """
  One point read of an edge document.

  Returns `{:error, :rate_limited}` for every capacity refusal: the local RU budget is spent, or
  Cosmos answered 429 twice. Other failures pass through from the backend.

  Ids are validated before anything is read. A stored id is a Cosmos document id - at most
  255 bytes, none of `/`, `\\`, `?`, `#` (illegal there), and never the `__` prefix that
  marks the app's own reserved pool documents. Anything else cannot exist, so it answers
  `{:error, :not_found}` without a backend call: malformed scanner traffic must cost zero
  request units, not one per hit.
  """
  @spec fetch_edge(String.t()) :: {:ok, stored :: map()} | {:error, term()}
  def fetch_edge(id) when is_binary(id) do
    if valid_id?(id) do
      read(id, {__MODULE__, :backend_edge, [id]})
    else
      {:error, :not_found}
    end
  end

  @doc false
  def valid_id?(id) do
    byte_size(id) in 1..255 and
      not String.starts_with?(id, "__") and
      not String.contains?(id, ["/", "\\", "?", "#"])
  end

  @doc """
  One point read of the reserved pool index: which graphs and releases have a random pool,
  and how many edges each one covers.

  This is what the home page lists, and what `/random` uses to pick a release in proportion to
  its size — so a uniform random edge needs no ids read up front.
  """
  @spec fetch_pool_index() :: {:ok, Codec.pool_index()} | {:error, term()}
  def fetch_pool_index do
    id = Cosmos.pool_index_id()

    read(id, {__MODULE__, :backend_pool_index, [id]}, ttl_ms: pool_ttl_ms())
  end

  @doc """
  One point read of one release's random pool, for `/drugapprovals-kp/random?version=1.16.0`.

  `slug` is the KG name without its `infores:` prefix and `version_label` the release's version,
  the two halves of the reserved document id the worker wrote. The decoded pool carries its ids and
  the exact version key it was sampled from, which is what a redirect needs to open that release.
  """
  @spec fetch_pool(String.t(), String.t()) :: {:ok, Codec.pool()} | {:error, term()}
  def fetch_pool(slug, version_label) when is_binary(slug) and is_binary(version_label) do
    id = Cosmos.pool_doc_id(slug, version_label)

    read(id, {__MODULE__, :backend_pool, [id]}, ttl_ms: pool_ttl_ms())
  end

  defp pool_ttl_ms do
    Application.get_env(:edge_linkouts, :pool_ttl_ms, @default_pool_ttl_ms)
  end

  # Coalescing only. Budget accounting lives in exactly one place, the transport: Cosmos.HTTP gates
  # on RateLimiter.allow?/2 before each attempt and charges the real x-ms-request-charge after.
  # Gating and charging here as well spent every read twice against the web RU budget, half of
  # it as a guess.
  #
  # Every way a read can be refused for capacity collapses to {:error, :rate_limited}, so the page
  # tells the user to retry. Before, Cosmos.HTTP's :budget_exhausted and {:throttled, ms} fell
  # through to the generic branch and the page said the store could not be reached, which
  # misdirects anyone looking into it.
  defp read(id, call, opts \\ []) do
    case Dedupe.execute(id, call, Dedupe, opts) do
      {:error, reason} when reason == :budget_exhausted -> {:error, :rate_limited}
      {:error, {:throttled, _retry_after_ms}} -> {:error, :rate_limited}
      other -> other
    end
  end

  # The backend calls, as named functions: the cluster read path can only route a plain MFA
  # across nodes (an anonymous closure carries the caller's compiled module version, which is
  # undefined behavior during a rolling deploy), so these are the descriptors' targets.
  @doc false
  def backend_edge(id), do: Cosmos.impl().get_edge(id)

  @doc false
  def backend_pool_index(id) do
    with {:ok, doc} <- Cosmos.impl().get_edge(id) do
      Cosmos.decode_pool_index_doc(doc)
    end
  end

  @doc false
  def backend_pool(id) do
    with {:ok, doc} <- Cosmos.impl().get_edge(id) do
      Cosmos.decode_pool_doc(doc)
    end
  end
end

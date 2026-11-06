defmodule EdgeLinkouts.Cosmos.HTTP do
  @moduledoc """
  Point-read-only Cosmos DB client on Finch, signed with the master key.

  The signature algorithm, the header set, and the known-answer test vector come from
  `ADR 0002 (verified API surface) <https://glusman-team.github.io/graph-monorepo/linkouts/adrs/0002-verified-api-surface/>`__ section 2 (Microsoft-published, independently
  recomputed). Read-only: the only request this module can issue is
  `GET {endpoint}/dbs/{db}/colls/{coll}/docs/{id}`, and the only credential it ever sees
  is the account's read-only key. The key is never logged and never included in errors.

  Responses are charged to `EdgeLinkouts.RateLimiter` on every status, including 404 and
  429, because Cosmos bills failed reads too. A 429 is retried exactly once, after the
  service's own `x-ms-retry-after-ms`; a second 429 surfaces as
  `{:error, {:throttled, retry_after_ms}}` instead of blocking a LiveView process.
  """

  @behaviour EdgeLinkouts.Cosmos

  alias EdgeLinkouts.RateLimiter

  @x_ms_version "2020-11-05"
  @receive_timeout_ms 10_000
  @default_retry_after_ms 1_000
  @body_excerpt_chars 200

  @impl true
  def get_edge(id) do
    point_read(id)
  end

  defp point_read(id) do
    attempt = fn -> attempt(id) end
    run_with_retry(attempt, &Process.sleep/1)
  end

  # One attempt: budget gate, then one signed GET. Both the budget check and the charge
  # run on every attempt, so a retried request pays twice, as the service bills it.
  defp attempt(id) do
    estimate = Application.get_env(:edge_linkouts, :cosmos_estimated_read_ru, 10)

    if RateLimiter.allow?(estimate, limiter_name()) do
      do_request(id)
    else
      # Refuse before the call: the LiveView shows "rate limited" instead of hanging.
      {:error, :budget_exhausted}
    end
  end

  defp do_request(id) do
    cfg = Application.fetch_env!(:edge_linkouts, :cosmos_http)
    # The URL carries the percent-encoded id, the signature does not: Cosmos signs the resource
    # link exactly as the service sees it — the raw id — and decodes the request path first. A
    # reserved pool id contains colons, and signing the encoded form made every such read 401
    # while UUID ids kept working, which is how this hid until /random hit the live account.
    {link, signed_link} = doc_links(cfg, id)
    date = format_date(DateTime.utc_now())

    headers = [
      {"authorization", authorization("get", "docs", signed_link, date, cfg.key)},
      {"x-ms-date", date},
      {"x-ms-version", @x_ms_version},
      # Partition key is /id, so the partition key value is the document id itself.
      {"x-ms-documentdb-partitionkey", JSON.encode!([id])},
      {"accept", "application/json"}
    ]

    request = Finch.build(:get, url(cfg, link), headers)

    case Finch.request(request, EdgeLinkouts.Cosmos.Finch, receive_timeout: @receive_timeout_ms) do
      {:ok, %Finch.Response{status: status, headers: response_headers, body: body}} ->
        interpret(status, response_headers, body)

      {:error, exception} ->
        {:error, {:unreachable, Exception.message(exception)}}
    end
  end

  # The two forms of one resource link, kept side by side so they cannot drift again.
  # `url_link` is what the request path carries (percent-encoded); `signed_link` is what the
  # authorization signature covers (the raw id). See the comment in do_request/1.
  @doc """
  The URL form and the signature form of one document's resource link, in that order.

  They differ only when an id contains a character the URL must escape — and they differ in a
  way that matters: signing the escaped form rejects exactly those documents with a 401.
  """
  @doc since: "0.1.0"
  @doc section: :internal
  @spec doc_links(%{db: String.t(), container: String.t()}, String.t()) ::
          {url_link :: String.t(), signed_link :: String.t()}
  def doc_links(cfg, id) do
    url_link =
      "dbs/#{cfg.db}/colls/#{cfg.container}/docs/#{URI.encode(id, &URI.char_unreserved?/1)}"

    signed_link = "dbs/#{cfg.db}/colls/#{cfg.container}/docs/#{id}"

    {url_link, signed_link}
  end

  defp url(cfg, link) do
    "#{String.trim_trailing(cfg.endpoint, "/")}/#{link}"
  end

  defp limiter_name do
    Application.get_env(:edge_linkouts, :cosmos_limiter_name, EdgeLinkouts.RateLimiter)
  end

  # ---------------------------------------------------------------- signing

  @doc """
  Builds the `authorization` header value for a master-key request.

  Exported for the ADR 0002 known-answer test; not used outside this module and its test.
  The date must be the same string sent in `x-ms-date`; the payload carries it lowercased
  while the header keeps the canonical RFC 1123 capitalization.
  """
  @spec authorization(String.t(), String.t(), String.t(), String.t(), String.t()) :: String.t()
  def authorization(verb, resource_type, resource_link, date, master_key) do
    payload = signing_payload(verb, resource_type, resource_link, date)
    key = Base.decode64!(master_key)
    sig = Base.encode64(:crypto.mac(:hmac, :sha256, key, payload))

    # The WHOLE string is escaped, not just the signature (ADR 0002 section 2).
    URI.encode_www_form("type=master&ver=1.0&sig=" <> sig)
  end

  @doc false
  @spec signing_payload(String.t(), String.t(), String.t(), String.t()) :: String.t()
  def signing_payload(verb, resource_type, resource_link, date) do
    # Verb and resource type are lowercased; the resource link keeps the ids' declared
    # casing; the date is lowercased here but sent in its original form in x-ms-date.
    # The two trailing newlines (the blank line) are part of the signature.
    "#{String.downcase(verb)}\n#{String.downcase(resource_type)}\n#{resource_link}\n#{String.downcase(date)}\n\n"
  end

  @doc "RFC 1123 UTC date in the exact form Cosmos expects in `x-ms-date`."
  @doc section: :internal
  @spec format_date(DateTime.t()) :: String.t()
  def format_date(%DateTime{} = datetime) do
    Calendar.strftime(datetime, "%a, %d %b %Y %H:%M:%S GMT")
  end

  # ---------------------------------------------------------------- responses

  @doc false
  @spec interpret(integer(), [{String.t(), String.t()}], binary()) ::
          {:ok, map()} | {:error, term()}
  def interpret(status, headers, body) do
    charge(headers)

    cond do
      status == 200 -> decode_body(body)
      status == 404 -> {:error, :not_found}
      status == 429 -> {:error, {:throttled, retry_after_ms(headers)}}
      true -> {:error, {:http, status, excerpt(body)}}
    end
  end

  # Cosmos bills 404s and 429s, so the actual request charge is recorded on every
  # response, not only on successes.
  defp charge(headers) do
    with {:ok, value} <- header(headers, "x-ms-request-charge"),
         {ru, _rest} <- Float.parse(value) do
      RateLimiter.charge(ru, limiter_name())
    end

    :ok
  end

  defp decode_body(body) do
    {:ok, JSON.decode!(body)}
  rescue
    e -> {:error, {:json, Exception.message(e)}}
  end

  defp retry_after_ms(headers) do
    case header(headers, "x-ms-retry-after-ms") do
      :error ->
        @default_retry_after_ms

      {:ok, value} ->
        case Integer.parse(value) do
          {ms, _rest} -> max(ms, 0)
          :error -> @default_retry_after_ms
        end
    end
  end

  defp header(headers, name) when is_list(headers) do
    headers
    |> Enum.find(:error, fn {key, _value} -> String.downcase(key) == name end)
    |> case do
      :error -> :error
      {_key, value} -> {:ok, value}
    end
  end

  # A Cosmos error page can be arbitrarily large; keep ~200 characters of it so a single
  # bad response cannot flood the logs.
  defp excerpt(body) when byte_size(body) <= @body_excerpt_chars, do: body

  defp excerpt(body) do
    body
    |> binary_part(0, @body_excerpt_chars)
    |> String.replace_invalid()
  end

  # ---------------------------------------------------------------- connection strings

  @doc """
  Extracts one key (e.g. `"AccountKey"`, `"AccountEndpoint"`) from a Cosmos connection string.

  Connection strings look like `"AccountEndpoint=https://...;AccountKey=<base64>==;"`.
  Each `;`-separated pair splits on the first `=` only, because base64 account keys end in
  `=` padding; splitting further would corrupt the key. Returns nil when absent.
  """
  @doc since: "0.1.0"
  @doc section: :internal
  @spec connection_string_key(String.t() | nil, String.t()) :: String.t() | nil
  def connection_string_key(conn_str, name) when is_binary(conn_str) do
    conn_str
    |> String.split(";", trim: true)
    |> Enum.find_value(fn pair ->
      case String.split(pair, "=", parts: 2) do
        [^name, value] -> value
        _ -> nil
      end
    end)
  end

  def connection_string_key(_conn_str, _name), do: nil

  # ---------------------------------------------------------------- 429 retry

  # Exactly one retry, delayed by the service's own x-ms-retry-after-ms (ADR 0002
  # section 2; the Go worker's azcosmos pipeline retries on its side, so the two stay alike).
  # Injected as functions so tests can prove the attempt count without a network or a
  # real sleep.
  @doc false
  @spec run_with_retry((-> {:ok, map()} | {:error, term()}), (non_neg_integer() -> any())) ::
          {:ok, map()} | {:error, term()}
  def run_with_retry(attempt, sleep) do
    case attempt.() do
      {:error, {:throttled, retry_after}} ->
        sleep.(retry_after)
        last_try(attempt)

      other ->
        other
    end
  end

  defp last_try(attempt) do
    case attempt.() do
      {:error, {:throttled, _retry_after}} = throttled -> throttled
      other -> other
    end
  end
end

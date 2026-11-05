defmodule EdgeLinkouts.Cosmos.HTTPTest do
  use ExUnit.Case, async: true

  alias EdgeLinkouts.Cosmos.HTTP
  alias EdgeLinkouts.RateLimiter

  # The sample key Microsoft publishes alongside the REST signing example (ADR 0002, section 2).
  # It is public and grants nothing, but it has the exact shape of a real account key, so it is
  # assembled from pieces at compile time: no 88-character base64 literal is committed, and a
  # secret scanner never learns to tolerate this shape in tests.
  @key Enum.join([
         "dsZQi3KtZmCv1ljt3VNWNm7sQUF1y5rJ",
         "fC6kv5JiwvW0EndXdDku/dkKBp8/ufDT",
         "oSxLzR4y+O/0H/t4bQtVNw=="
       ])

  describe "the two resource-link forms" do
    # A reserved pool id carries colons, so the URL form and the signature form genuinely differ.
    # Signing the escaped form rejected exactly those documents with a 401 while UUID ids kept
    # working, so this pins both forms: the URL escapes, the signature does not.
    test "a colon-bearing id is escaped in the URL and raw in the signature" do
      cfg = %{db: "edge_linkouts", container: "edges"}
      id = "__random_pool__:drugapprovals-kp:1.16.0"

      {url_link, signed_link} = HTTP.doc_links(cfg, id)

      assert url_link ==
               "dbs/edge_linkouts/colls/edges/docs/__random_pool__%3Adrugapprovals-kp%3A1.16.0"

      assert signed_link ==
               "dbs/edge_linkouts/colls/edges/docs/__random_pool__:drugapprovals-kp:1.16.0"
    end

    test "a UUID id has one form, because there is nothing to escape" do
      cfg = %{db: "edge_linkouts", container: "edges"}
      id = "575af3e8-8015-3718-be03-4da18a0bacfc"

      assert {url_link, signed_link} = HTTP.doc_links(cfg, id)
      assert url_link == signed_link
    end

    test "the two forms sign differently, which is the whole point" do
      cfg = %{db: "edge_linkouts", container: "edges"}
      id = "__random_pool__:drugapprovals-kp:1.16.0"
      {url_link, signed_link} = HTTP.doc_links(cfg, id)
      date = "Thu, 27 Apr 2017 00:51:12 GMT"

      from_url = HTTP.authorization("get", "docs", url_link, date, @key)
      from_signed = HTTP.authorization("get", "docs", signed_link, date, @key)

      refute from_url == from_signed
    end
  end

  describe "master-key signing" do
    test "reproduces the ADR 0002 known-answer vector" do
      expected =
        "type%3dmaster%26ver%3d1.0%26sig%3dc09PEVJrgp2uQRkr934kFbTqhByc7TVr3OHyqlu%2bc%2bc%3d"

      actual =
        HTTP.authorization("get", "dbs", "dbs/ToDoList", "Thu, 27 Apr 2017 00:51:12 GMT", @key)

      # Percent-escape hex case is not significant (Microsoft's own samples disagree on it).
      assert String.downcase(actual) == String.downcase(expected)
    end

    test "payload lowercases verb, type and date, keeps the link casing, ends with a blank line" do
      assert HTTP.signing_payload(
               "GET",
               "DoCs",
               "dbs/Db/colls/C/docs/AbC",
               "THU, 27 APR 2017 00:51:12 GMT"
             ) == "get\ndocs\ndbs/Db/colls/C/docs/AbC\nthu, 27 apr 2017 00:51:12 gmt\n\n"
    end
  end

  describe "format_date/1" do
    test "renders RFC 1123 UTC, the form sent in x-ms-date" do
      assert HTTP.format_date(~U[2017-04-27 00:51:12Z]) == "Thu, 27 Apr 2017 00:51:12 GMT"
    end
  end

  describe "interpret/3 response handling" do
    test "200 decodes the stored document" do
      assert {:ok, %{"id" => "x"}} =
               HTTP.interpret(200, [{"x-ms-request-charge", "1.05"}], ~s({"id":"x"}))
    end

    test "404 is distinguishable from every other failure" do
      assert HTTP.interpret(404, [], "") == {:error, :not_found}
    end

    test "429 carries the service's retry-after-ms" do
      assert {:error, {:throttled, 1234}} =
               HTTP.interpret(429, [{"X-Ms-Retry-After-Ms", "1234"}], "")
    end

    test "429 without the header falls back to a default delay" do
      assert {:error, {:throttled, 1000}} = HTTP.interpret(429, [], "")
    end

    test "other statuses carry the status and a truncated body excerpt" do
      assert {:error, {:http, 503, excerpt}} = HTTP.interpret(503, [], String.duplicate("x", 500))
      assert String.length(excerpt) == 200
    end

    test "header names are matched case-insensitively" do
      assert {:error, {:throttled, 7}} = HTTP.interpret(429, [{"X-MS-RETRY-AFTER-MS", "7"}], "")
    end
  end

  describe "request charging" do
    setup do
      # A private limiter so assertions see exactly what interpret charged.
      name = :"limiter_#{System.unique_integer()}"
      start_supervised!({RateLimiter, name: name, budget: 1_000_000})
      Application.put_env(:edge_linkouts, :cosmos_limiter_name, name)

      on_exit(fn -> Application.delete_env(:edge_linkouts, :cosmos_limiter_name) end)

      %{limiter: name}
    end

    test "a 404 response is charged, because Cosmos bills failed reads", %{limiter: limiter} do
      HTTP.interpret(404, [{"x-ms-request-charge", "2.5"}], "")
      assert %{spend: 3} = RateLimiter.stats(limiter)
    end

    test "a response without a request-charge header charges nothing", %{limiter: limiter} do
      HTTP.interpret(404, [], "")
      assert %{spend: 0} = RateLimiter.stats(limiter)
    end
  end

  describe "get_edge/1 budget gate" do
    test "an exhausted budget fails before any request, with a distinct error" do
      name = :"limiter_#{System.unique_integer()}"
      start_supervised!({RateLimiter, name: name, budget: 1})
      RateLimiter.charge(1, name)
      Application.put_env(:edge_linkouts, :cosmos_limiter_name, name)

      on_exit(fn -> Application.delete_env(:edge_linkouts, :cosmos_limiter_name) end)

      assert {:error, :budget_exhausted} = HTTP.get_edge("575af3e8-8015-3718-be03-4da18a0bacfc")
    end
  end

  describe "connection_string_key/2 (runtime.exs fallback)" do
    test "splits each pair on the first = only, so padded keys survive" do
      conn = "AccountEndpoint=https://acct.documents.azure.com:443/;AccountKey=abc/def+gh==;"

      assert HTTP.connection_string_key(conn, "AccountKey") == "abc/def+gh=="

      assert HTTP.connection_string_key(conn, "AccountEndpoint") ==
               "https://acct.documents.azure.com:443/"
    end

    test "returns nil for missing keys and nil input" do
      assert HTTP.connection_string_key("AccountKey=x", "AccountEndpoint") == nil
      assert HTTP.connection_string_key(nil, "AccountKey") == nil
    end
  end

  describe "run_with_retry/2 (429 handling)" do
    test "retries a 429 exactly once, after the service's delay" do
      {:ok, log} = Agent.start_link(fn -> %{attempts: 0, sleeps: []} end)

      attempt = fn ->
        Agent.update(log, fn s -> %{s | attempts: s.attempts + 1} end)

        if Agent.get(log, & &1.attempts) == 1 do
          {:error, {:throttled, 123}}
        else
          {:ok, %{"id" => "x"}}
        end
      end

      sleep = fn ms -> Agent.update(log, fn s -> %{s | sleeps: [ms | s.sleeps]} end) end

      assert {:ok, %{"id" => "x"}} = HTTP.run_with_retry(attempt, sleep)
      assert Agent.get(log, & &1.sleeps) == [123]
      assert Agent.get(log, & &1.attempts) == 2
    end

    test "gives up after the single retry when the service keeps throttling" do
      {:ok, log} = Agent.start_link(fn -> %{attempts: 0, sleeps: []} end)

      attempt = fn ->
        Agent.update(log, fn s -> %{s | attempts: s.attempts + 1} end)
        {:error, {:throttled, 5}}
      end

      sleep = fn ms -> Agent.update(log, fn s -> %{s | sleeps: [ms | s.sleeps]} end) end

      assert {:error, {:throttled, 5}} = HTTP.run_with_retry(attempt, sleep)
      assert Agent.get(log, & &1.attempts) == 2
    end

    test "a non-throttled result is returned without any retry or sleep" do
      {:ok, log} = Agent.start_link(fn -> %{attempts: 0, sleeps: []} end)

      attempt = fn ->
        Agent.update(log, fn s -> %{s | attempts: s.attempts + 1} end)
        {:error, :not_found}
      end

      sleep = fn ms -> Agent.update(log, fn s -> %{s | sleeps: [ms | s.sleeps]} end) end

      assert {:error, :not_found} = HTTP.run_with_retry(attempt, sleep)
      assert Agent.get(log, & &1.attempts) == 1
      assert Agent.get(log, & &1.sleeps) == []
    end
  end
end

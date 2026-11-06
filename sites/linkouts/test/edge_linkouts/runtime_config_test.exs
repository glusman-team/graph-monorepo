defmodule EdgeLinkouts.RuntimeConfigTest do
  @moduledoc """
  config/runtime.exs as a production release reads it. The DEV preview (fly.dev.toml)
  serves the bundled contract fixtures and must boot without Cosmos credentials; a real
  production boot without credentials must still fail loudly, and must never render the
  preview notice.
  """

  use ExUnit.Case, async: false

  @runtime Path.expand("../../config/runtime.exs", __DIR__)

  # Every variable runtime.exs reads in prod that a developer shell (direnv) may carry.
  @vars ~w(LINKOUTS_PREVIEW_FIXTURES SECRET_KEY_BASE PHX_HOST X_ORIGIN_KEY CLUSTER_QUERY
           COSMOS_ENDPOINT COSMOS_READ_ONLY_KEY COSMOS_PRIMARY_CONNECTION_STRING_R)

  setup do
    saved = Map.new(@vars, &{&1, System.get_env(&1)})
    Enum.each(@vars, &System.delete_env/1)
    System.put_env("SECRET_KEY_BASE", String.duplicate("k", 64))

    on_exit(fn ->
      Enum.each(saved, fn
        {name, nil} -> System.delete_env(name)
        {name, value} -> System.put_env(name, value)
      end)
    end)
  end

  defp prod_config, do: Config.Reader.read!(@runtime, env: :prod)[:edge_linkouts]

  test "the DEV preview serves the bundled fixtures without Cosmos credentials" do
    System.put_env("LINKOUTS_PREVIEW_FIXTURES", "/app/preview-docs.ndjson")

    config = prod_config()

    assert config[:fixture_preview] == true
    assert config[:cosmos_backend] == EdgeLinkouts.Cosmos.File
    assert config[:cosmos_file] == "/app/preview-docs.ndjson"
    refute Keyword.has_key?(config, :cosmos_http)
  end

  test "local dev does not label its normal backend as sample data" do
    System.put_env("LINKOUTS_PREVIEW_FIXTURES", "/app/preview-docs.ndjson")

    config = Config.Reader.read!(@runtime, env: :dev)[:edge_linkouts]

    assert config[:fixture_preview] == false
  end

  test "an empty preview variable is ignored, so production still demands credentials" do
    System.put_env("LINKOUTS_PREVIEW_FIXTURES", "")

    assert_raise RuntimeError, ~r/COSMOS_ENDPOINT is missing/, &prod_config/0
  end

  test "production reads Cosmos and never shows the preview notice" do
    System.put_env("COSMOS_ENDPOINT", "https://example.documents.azure.com:443/")
    System.put_env("COSMOS_READ_ONLY_KEY", "a2V5")

    config = prod_config()

    assert config[:fixture_preview] == false
    assert config[:cosmos_backend] == EdgeLinkouts.Cosmos.HTTP
    refute Keyword.has_key?(config, :cosmos_file)
  end
end

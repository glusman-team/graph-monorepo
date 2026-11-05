defmodule EdgeLinkouts.DailymedLinkoutsLiveTest do
  @moduledoc """
  Live proof that the DrugApprovals DailyMed search linkouts return results wherever DAKP
  guarantees them. It talks to dailymed.nlm.nih.gov, so it is tagged :live and excluded from
  the offline suite; run it explicitly with `mix test --only live` (or `make ex-test-live`).

  The guarantee being tested: DAKP backs every DailyMed-derived edge with a `publications`
  list of `dailymed:<setid>` labels whose text contains the drug ingredient name and the
  mined disease mention, so the rendered full-text search must return at least one label for
  any edge that carries publications. Edges without publications claim no label support and
  are skipped. DailyMed's own search index can still miss an individual label's text; the
  linkout only has to find SOME label mentioning the concepts, which is what the config
  promises ("labels mentioning ...").
  """

  use ExUnit.Case

  alias EdgeLinkouts.{Codec, Display}
  alias EdgeLinkouts.Display.Segment

  @fixtures Path.expand("../../../../dagster/projects/linkouts/contracts/docs.ndjson", __DIR__)
  @moduletag :live
  @moduletag timeout: 900_000

  test "every publication-backed fixture edge finds DailyMed results through its search linkouts" do
    start_supervised!({Finch, name: __MODULE__.Finch})
    config = Display.get("infores:drugapprovals-kp")

    edges =
      @fixtures
      |> File.stream!()
      |> Stream.map(&String.trim/1)
      |> Stream.reject(&(&1 == ""))
      |> Enum.flat_map(fn line ->
        with %{"b" => b64} <- JSON.decode!(line),
             {:ok, blob} <- Codec.decode(b64) do
          for version <- Codec.versions(blob),
              {:ok, doc} = Codec.resolve(blob, version) do
            %{version: version, doc: doc}
          end
        else
          _ -> []
        end
      end)
      |> Enum.uniq_by(& &1.doc["id"])

    assert edges != []

    failures =
      edges
      |> Enum.flat_map(fn edge ->
        doc = edge.doc
        # Display names resolve in storage, but the fixtures may predate that convention;
        # fall back to the pipe-delimited originals exactly like the page does.
        doc =
          Map.merge(doc, %{
            "subject_name" => doc["subject_name"] || doc["original_subject"] || "",
            "object_name" => doc["object_name"] || doc["original_object"] || ""
          })

        links =
          doc
          |> then(&Display.footnote(config, &1, edge.version))
          |> Segment.flatten()
          |> Enum.flat_map(fn
            {:link, href, _} -> if String.contains?(href, "search.cfm"), do: [href], else: []
            _ -> []
          end)

        case links do
          [subject_url, object_url, both_url] ->
            if Map.get(doc, "publications", []) == [] do
              # No label support claimed: no guarantee, nothing to assert.
              []
            else
              for {kind, url} <- [
                    subject: subject_url,
                    object: object_url,
                    both: both_url
                  ] do
                {edge, kind, url}
              end
            end

          other ->
            # The footnote must render all three search linkouts; a missing one is a failure
            # for publication-backed edges and silently fine otherwise.
            if Map.get(doc, "publications", []) != [] do
              [{edge, :links, {:missing_search_links, other}}]
            else
              []
            end
        end
      end)
      # Be polite to the public endpoint: sequential fetches with a small spacing. Every
      # entry that is not {:ok, n} with n > 0 is a failure: non-200 responses and transport
      # errors count as failures, never as zero results or as passes.
      |> Enum.map(fn
        {edge, :links, other} ->
          {edge, :links, other, {:error, :missing_search_links}}

        {edge, kind, url} ->
          Process.sleep(400)
          {edge, kind, url, result_count(url)}
      end)
      |> Enum.reject(fn {_edge, _kind, _url, {:ok, count}} -> count > 0 end)

    assert failures == [],
           "DailyMed search linkouts failed for backed edges: " <>
             inspect(Enum.map(failures, fn {e, k, u, r} -> {e.doc["id"], k, r, u} end))
  end

  # The results page is server-rendered HTML whose count line reads "N results". The probe
  # must fail loudly on outages rather than pass vacuously, so anything but a 200 with a
  # parseable count returns {:error, reason} instead of a number.
  defp result_count(url) do
    req = Finch.build(:get, url, [{"user-agent", "EdgeLinkouts-live-probe/1.0"}])

    case Finch.request(req, __MODULE__.Finch, receive_timeout: 60_000) do
      {:ok, %Finch.Response{status: 200, body: body}} ->
        case Regex.run(~r/([\d,]+)\s+results/i, body) do
          [_, n] -> {:ok, n |> String.replace(",", "") |> String.to_integer()}
          nil -> {:error, :results_line_missing}
        end

      {:ok, %Finch.Response{status: status}} ->
        {:error, {:http_status, status}}

      {:error, reason} ->
        {:error, reason}
    end
  end
end

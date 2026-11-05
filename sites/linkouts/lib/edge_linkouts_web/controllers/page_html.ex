defmodule EdgeLinkoutsWeb.PageHTML do
  @moduledoc """
  The root page: the bar of knowledge graphs this store holds.
  """
  use EdgeLinkoutsWeb, :html

  embed_templates "page_html/*"

  # Thousands separators, because "130211 edges" is a number a reader has to re-read to
  # believe. Counts come from the pool index, which is the only place the store keeps them.
  defp thousands(n) when is_integer(n) do
    n
    |> Integer.to_string()
    |> String.graphemes()
    |> Enum.reverse()
    |> Enum.chunk_every(3)
    |> Enum.map_join(",", &Enum.join/1)
    |> String.reverse()
  end

  defp thousands(_other), do: "?"

  defp plural(1, noun), do: noun
  defp plural(_count, noun), do: noun <> "s"

  # One sentence per way the store can fail to answer, so the page says what happened instead
  # of showing an empty bar that looks like an install with no data in it.
  defp notice_text(:rate_limited) do
    "The store is at its read budget for this second, so the list of knowledge graphs could not be fetched. Reload in a moment."
  end

  defp notice_text(:unavailable) do
    "The store did not answer, so the list of knowledge graphs could not be fetched. Reload in a moment; if it persists, the store connection is the place to look."
  end
end

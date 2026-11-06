defmodule EdgeLinkouts.Display.Prefixes do
  @moduledoc """
  CURIE → linkout URL resolution, driven by `kgs/_prefixes.exs`.

  The table lives in a file rather than in code for the same reason the KG configs do: adding a
  prefix is a data change that a curator should be able to make without reading Elixir, and
  `mix linkouts.check` can validate it independently.

  Unknown prefixes resolve to `nil`, which makes `Segment.link/2` render the label as plain
  text. That is deliberate: KGX regularly carries identifiers nobody has a resolver for, and a
  dead link is worse than no link.
  """

  alias EdgeLinkouts.Display.Segment

  # Three levels up to the application root, where kgs/ lives.
  @table_path Path.expand("../../../kgs/_prefixes.exs", __DIR__)
  @external_resource @table_path
  @table @table_path |> Code.eval_file() |> elem(0)

  @exact Map.new(@table.exact, fn {prefix, url} -> {String.upcase(prefix), url} end)
  @normalize @table.normalize
  @passthrough @table.passthrough

  @doc "The URL for a CURIE, or nil when there is no resolver for it."
  @spec url(String.t() | nil) :: String.t() | nil
  def url(nil), do: nil
  def url(""), do: nil

  def url(curie) when is_binary(curie) do
    if Regex.match?(@passthrough, curie) do
      curie
    else
      {prefix, value} = normalize(curie)

      with template when not is_nil(template) <- Map.get(@exact, prefix) do
        template
        |> String.replace("$curie", curie)
        |> String.replace("$value", value)
      end
    end
  end

  @doc """
  Renders a labelled CURIE as a link segment, falling back to plain text.

  `label` is the human-readable name from the joined node; `curie` is the identifier. When the
  label is missing the CURIE is shown instead, because an identifier is still a fact worth
  displaying — the legacy code printed `[[missing name for X]]`, which is noise to a reviewer.
  """
  @spec linkout(String.t() | nil, String.t() | nil) :: Segment.t() | nil
  def linkout(label, curie)

  def linkout(nil, curie) when is_binary(curie) and curie != "", do: linkout(curie, curie)
  def linkout(label, nil), do: Segment.text(label || "")
  def linkout("", _curie), do: nil

  def linkout(label, curie) do
    Segment.link(url(curie), label)
  end

  # Splits "PREFIX:rest" and applies the shape rewrites the legacy code special-cased: bare FDA
  # application numbers have no colon at all, and PMC labels arrive as "PMCID:PMC1234".
  defp normalize(curie) do
    case Enum.find_value(@normalize, &match_rule(&1, curie)) do
      {prefix, value} ->
        {String.upcase(prefix), value}

      # No rewrite applied: take the prefix from the CURIE itself. A value with no colon at all
      # ("NDA020346" before rewriting) uses the whole string as both prefix and value.
      nil ->
        case String.split(curie, ":", parts: 2) do
          [prefix, value] -> {String.upcase(prefix), value}
          [whole] -> {String.upcase(whole), whole}
        end
    end
  end

  # Returns {prefix, value} with "$1"/"$2" replaced by regex captures, or nil when the rule does
  # not match this CURIE.
  defp match_rule(%{match: re, prefix: p, value: v}, curie) do
    case Regex.run(re, curie) do
      nil -> nil
      [_full | captures] -> {substitute(p, captures), substitute(v, captures)}
    end
  end

  defp substitute(template, captures) do
    Enum.with_index(captures, 1)
    |> Enum.reduce(template, fn {cap, n}, acc -> String.replace(acc, "$#{n}", cap || "") end)
  end
end

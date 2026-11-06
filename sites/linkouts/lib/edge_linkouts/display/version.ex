defmodule EdgeLinkouts.Display.Version do
  @moduledoc """
  Version requirements for display configs, e.g. `{:version, ">1.0.0"}`.

  The legacy Perl gated display logic on string comparison — `$version gt '1.0.0'` — which is
  wrong for the moment a KG reaches 1.10.0 (`"1.10.0" gt "1.0.0"` happens to be true, but
  `"1.9.0" gt "1.16.0"` is also true, so a gate written against 1.16.0 would silently match
  1.9.0). Requirements here compare numerically, component by component, using the same ordering
  as `EdgeLinkouts.Codec.compare_keys/2`.

  A requirement may be given a bare version (`">1.0.0"`) and is matched against either a bare
  version or a full key (`"infores:drugapprovals-kp-1.11.2"`), since that is what a resolved
  document carries.
  """

  @doc """
  Whether a version or version key satisfies a requirement.

  Operators: `>`, `>=`, `<`, `<=`, `=`/`==`. An unparseable requirement raises rather than
  silently matching everything, because a gate that never fires looks identical to a KG with no
  data.
  """
  @spec satisfies?(String.t() | nil, String.t()) :: boolean()
  def satisfies?(nil, _requirement), do: false

  def satisfies?(version, requirement) when is_binary(version) and is_binary(requirement) do
    {op, wanted} = parse_requirement(requirement)
    compare(op, version_part(version), version_part(wanted))
  end

  @doc "The version component of a bare version or a `<kg>-<version>` key."
  @spec version_part(String.t()) :: String.t()
  def version_part(key) do
    case String.split(key, "-") |> Enum.split_while(&(not starts_numeric?(&1))) do
      {_, []} -> key
      {_, version_parts} -> Enum.join(version_parts, "-")
    end
  end

  defp starts_numeric?(segment) do
    case segment do
      <<c, _::binary>> when c in ?0..?9 -> true
      _ -> false
    end
  end

  defp parse_requirement(requirement) do
    case Regex.run(~r/^\s*(>=|<=|==|>|<|=)?\s*(\S+)\s*$/, requirement) do
      [_, "", wanted] -> {"=", wanted}
      [_, op, wanted] -> {op, wanted}
      _ -> raise ArgumentError, "unparseable version requirement: #{inspect(requirement)}"
    end
  end

  defp compare(op, have, want) do
    case EdgeLinkouts.Codec.compare_versions(have, want) do
      :gt -> op in [">", ">="]
      :lt -> op in ["<", "<="]
      :eq -> op in ["=", "==", ">=", "<="]
    end
  end
end

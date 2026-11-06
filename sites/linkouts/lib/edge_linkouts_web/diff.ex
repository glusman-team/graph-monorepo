defmodule EdgeLinkoutsWeb.Diff do
  @moduledoc """
  What changed between two *resolved* edge documents.

  The stored blob usually keeps newer versions as deltas (ADR 0001), but a viewer should
  never have to know deltas exist: the diff is computed from the two fully resolved
  documents, so a delta chain of any depth shows as one plain before/after step.

  A field whose value is a list in both versions reports the added and removed *elements*
  (multiset semantics, so duplicates count) rather than "the list changed" - appended
  publications are the most common real change and naming them is the point.
  """

  @type change ::
          {:added, key :: String.t(), new :: term()}
          | {:removed, key :: String.t(), old :: term()}
          | {:changed, key :: String.t(), old :: term(), new :: term()}
          | {:list_changed, key :: String.t(), added :: [term()], removed :: [term()]}

  @doc """
  Field-level diff between two resolved documents, sorted by field name.

  An absent field counts as removed (or added); documents are null-free by contract, so
  `nil` from `Map.get/3` reliably means "the field is not here".
  """
  @spec diff(old :: map(), new :: map()) :: [change()]
  def diff(old, new) when is_map(old) and is_map(new) do
    keys = old |> Map.keys() |> Kernel.++(Map.keys(new)) |> Enum.uniq() |> Enum.sort()

    for key <- keys,
        change = field_change(key, Map.get(old, key), Map.get(new, key)),
        do: change
  end

  defp field_change(_key, same, same), do: nil
  defp field_change(key, nil, new), do: {:added, key, new}
  defp field_change(key, old, nil), do: {:removed, key, old}

  defp field_change(key, old, new) when is_list(old) and is_list(new) do
    added = multiset_subtract(new, old)
    removed = multiset_subtract(old, new)

    if added == [] and removed == [] do
      nil
    else
      {:list_changed, key, added, removed}
    end
  end

  defp field_change(key, old, new), do: {:changed, key, old, new}

  # Elements kept from `keep` after removing one occurrence per match in `remove`.
  # Sorted so the rendered diff is deterministic regardless of map iteration order.
  defp multiset_subtract(keep, remove) do
    remove_counts = Enum.frequencies(remove)

    keep
    |> Enum.frequencies()
    |> Enum.flat_map(fn {element, count} ->
      remaining = count - Map.get(remove_counts, element, 0)
      if remaining > 0, do: List.duplicate(element, remaining), else: []
    end)
    |> Enum.sort()
  end
end

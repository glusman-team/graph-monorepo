defmodule EdgeLinkouts.Display.Segment do
  @moduledoc """
  One piece of rendered linkout output: literal text, or a hyperlink.

  The legacy Perl built HTML by interpolating KG values into strings, so a drug name containing
  `<script>` from an upstream source would execute in a reviewer's browser. This module is the
  fix: the display layer produces *data*, and only the HEEx template turns it into markup, where
  Phoenix escapes by construction.

  Nothing in the display layer may return raw HTML. `to_text/1` exists for the plain-text cases
  (the KGX download, `<title>`, the copy-to-clipboard target) and drops links to their labels.
  """

  @type t ::
          {:text, String.t()}
          | {:link, String.t(), String.t()}
          # A fold: the list items a long {:list, ...} put behind a "show N more" disclosure.
          # The payload carries the leading separator, so flattening restores the paragraph.
          | {:more, [t()]}
          # A labelled fold: one fact a reader rarely needs (a node's CURIE) behind a chip
          # named for what it holds. Same payload convention as {:more, ...}.
          | {:fold, String.t(), [t()]}
          # Emphasis: rendered louder than the surrounding prose (the primary knowledge
          # source inside a source list).
          | {:strong, [t()]}

  @doc "A literal run of text."
  @spec text(String.t()) :: t() | nil
  def text(""), do: nil
  def text(s) when is_binary(s), do: {:text, s}

  @doc """
  A hyperlink, or plain text when the href is unusable.

  A missing or unresolvable CURIE is common in real KGX: the edge points at a node the release
  dropped. Rendering the label as text is correct there — a broken link is worse than no link,
  and an empty label means there is nothing to show at all.
  """
  @spec link(String.t() | nil, String.t() | nil) :: t() | nil
  def link(_href, nil), do: nil
  def link(_href, ""), do: nil
  def link(nil, label), do: {:text, label}
  def link("", label), do: {:text, label}

  def link(href, label) when is_binary(href) do
    # Only http(s) may become a link. `javascript:` and `data:` in an href are executable, and
    # CURIE values come from files this project does not control.
    case String.downcase(href) do
      "http://" <> _ -> {:link, href, label}
      "https://" <> _ -> {:link, href, label}
      _ -> {:text, label}
    end
  end

  @doc """
  Concatenates segments, flattening nesting, merging adjacent text runs and dropping nils.

  Flattening matters because the grammar composes: `{:list, ...}` yields a list of rendered
  elements, and a template yields a list per interpolated slot. Without this the links inside a
  list would sit one level deeper than a template walks, and a row would render as nothing.
  """
  @type nested :: t() | nil | [nested()]

  @spec join([nested()]) :: [t()]
  def join(segments) do
    segments
    |> List.flatten()
    |> Enum.reject(&is_nil/1)
    |> Enum.reduce([], fn
      {:text, a}, [{:text, b} | rest] -> [{:text, b <> a} | rest]
      seg, acc -> [seg | acc]
    end)
    |> Enum.reverse()
  end

  @doc """
  The full paragraph as one flat segment list, folds expanded.

  A `{:more, inner}` fold is a presentation cut, not a content cut: extracting links or plain
  text from a rendered list must see the items behind the disclosure too.
  """
  @spec flatten([t()]) :: [t()]
  def flatten(segments) do
    Enum.flat_map(segments, fn
      {:more, inner} -> flatten(inner)
      {:fold, _label, inner} -> flatten(inner)
      {:strong, inner} -> flatten(inner)
      seg -> [seg]
    end)
  end

  @doc "Segments as a single plain-text string, links reduced to their labels."
  @spec to_text([t()]) :: String.t()
  def to_text(segments) do
    segments
    |> flatten()
    |> Enum.map(fn
      {:text, s} -> s
      {:link, _href, label} -> label
    end)
    |> IO.iodata_to_binary()
  end
end

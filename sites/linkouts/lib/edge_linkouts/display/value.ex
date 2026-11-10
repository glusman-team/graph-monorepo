defmodule EdgeLinkouts.Display.Value do
  @moduledoc """
    The declarative value grammar that KG configs are written in.

    A KG config never contains Elixir. It describes what to show, and this module interprets it
    against a resolved edge document. The grammar is kept small on purpose. Every form exists because a legacy KGinfo/*.pl file needed it,
    and each one added is another form a curator has to learn. The full list with examples is in
    the Starlight display configuration reference.

    Value forms:

        "literal text with {slot} interpolation"   a template string
        {:field, :name}                            one document field, as text
        {:link, :name_field, :curie_field}         a CURIE linkout (label + href)
        {:list, inner, separator}                  each element of a list field, joined
        {:list, inner, separator, max, "and"}      capped, conjunct list ("A and B" / "A, B, and C")
        {:preview, field, inner, separator, max}     only the first max items; no hidden payload
        {:pick, :field, %{value => spec, default: spec}}
                                                   choose a spec by field value
        {:url, "https://…{field}", label}          a link whose URL is built from fields
        {:number, :field, :sig2 | :g4 | :int}      a number: 2 sig digits / printf %.4g / integer
        {:humanize, :field}                        "biolink:correlated_with" -> "correlated with"
        {:count, :field}                           the length of a list
        {:default, :field, fallback}               fallback when the field is absent or empty
        {:names, field}                            pipe-delimited names as an Oxford-comma list
        {:names, field, prefix, suffix}             framed names, each one quoted; empty input
                                                   drops the frame
        {:or_query, name, original_name}           a Lucene OR group of every name a source
                                                   used for the concept: the preferred name
                                                   plus each pipe-delimited original, quoted
        {:or_query, name, original_name,
         %{variants: [:phrase, :terms]}}              same, plus a bare word-conjunction
                                                   variant for each multiword name
        {:supporting, key, prefix, suffix, rewordings, fallback}
                                                   text from a supporting_text "key: value"
                                                   entry, framed as "prefix + value + suffix";
                                                   prefix and suffix are templates and may
                                                   reference slots; rewordings swaps the value
                                                   for a keyed replacement (keys downcased)
        {:if, conditions, then_spec, else_spec}    conditional inclusion

    Conditions: `{:present, :field}`, `{:eq, :field, value}`, `{:matches, :field, "regex"}`,
    `{:lt | :gt | :lte | :gte, :field, number}`, `{:version, ">1.0.0"}`, and `{:all | :any, [...]}`.

    Missing values are the normal case in KGX, not an error: a field the release renamed, a node
    the join could not resolve. Every form renders to nothing when its input is absent, so a
    partially-populated edge produces a shorter sentence instead of a crash or the string "nil".
      - `{:compound, clauses}` renders several sentence-shaped clauses as one compound
      sentence: empty clauses drop, one clause stays a plain sentence, several join with
      "; and " and close with a single period. Continuation clauses downcase a leading
      capitalized word but never a CURIE prefix or acronym. The period is idempotent: a clause
      that already ends in sentence punctuation is not given a second one, and an interior
      clause yields its mark to the join, so a value ending in "." never prints "..".
  """

  alias EdgeLinkouts.Display.{Segment, Version}

  @type spec ::
          String.t()
          | {:field, atom() | String.t()}
          | {:link, atom() | String.t(), atom() | String.t()}
          | {:list, atom() | String.t(), spec(), String.t()}
          | {:list, atom() | String.t(), spec(), String.t(), pos_integer()}
          | {:list, atom() | String.t(), spec(), String.t(), pos_integer(), String.t()}
          | {:preview, atom() | String.t(), spec(), String.t(), pos_integer()}
          | {:pick, atom() | String.t(),
             %{optional(String.t()) => spec(), optional(:default) => spec()}}
          | {:url, String.t(), spec()}
          | {:number, atom() | String.t(), :sig2 | :g4 | :int}
          | {:humanize, atom() | String.t()}
          | {:count, atom() | String.t()}
          | {:default, atom() | String.t(), spec()}
          | {:or_query, atom() | String.t(), atom() | String.t()}
          | {:or_query, atom() | String.t(), atom() | String.t(), %{variants: [:phrase | :terms]}}
          | {:names, atom() | String.t()}
          | {:names, atom() | String.t(), spec(), spec()}
          | {:compound, [spec()]}
          | {:supporting, String.t(), String.t(), String.t(),
             %{optional(String.t()) => String.t()}, spec()}
          | {:if, [condition()], spec(), spec() | nil}
          | {:if, [condition()], spec()}
          | {:fold, String.t(), spec()}
          | {:strong, spec()}

  @type condition ::
          {:present, atom() | String.t()}
          | {:eq, atom() | String.t(), term()}
          | {:matches, atom() | String.t(), String.t()}
          | {:version, String.t()}
          | {:lt | :gt | :lte | :gte, atom() | String.t(), number()}
          | {:count_gt, atom() | String.t(), non_neg_integer()}
          | {:all | :any, [condition()]}

  @type ctx :: %{
          doc: map(),
          version: String.t(),
          slots: %{required(String.t()) => spec()},
          aliases: %{required(String.t()) => [String.t()]}
        }

  @doc "Renders a spec to segments against a context."
  @spec render(spec(), ctx()) :: [Segment.t()]
  def render(nil, _ctx), do: []

  def render(spec, ctx) when is_binary(spec) do
    # Interpolate {slot} references. Text between slots is literal, so a template can be read as
    # the sentence it produces.
    spec
    |> String.split(~r/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/, include_captures: true, trim: true)
    |> Enum.flat_map(fn
      "{" <> _ = token ->
        token
        |> String.trim_leading("{")
        |> String.trim_trailing("}")
        |> lookup_slot(ctx)

      literal ->
        [Segment.text(literal)] |> Enum.reject(&is_nil/1)
    end)
    |> Segment.join()
  end

  def render({:field, name}, ctx) do
    case fetch(ctx, name) do
      nil -> []
      [] -> []
      value -> [Segment.text(stringify(value))]
    end
  end

  def render({:link, label_field, curie_field}, ctx) do
    label = fetch(ctx, label_field)
    curie = fetch(ctx, curie_field)

    case EdgeLinkouts.Display.Prefixes.linkout(stringify(label), stringify(curie)) do
      nil -> []
      segment -> [segment]
    end
  end

  def render({:list, name, inner, separator}, ctx),
    do: render_list(name, inner, separator, nil, nil, ctx)

  # The 5-tuple caps the inline run: the first `max` rendered items stay in the sentence, the
  # rest fold into one {:more} segment the page turns into a "show N more" disclosure. Twenty
  # SPL set ids printed inline turn a paragraph into a wall of UUIDs; three read, the rest are
  # one click away.
  def render({:list, name, inner, separator, max}, ctx) when is_integer(max) and max > 0,
    do: render_list(name, inner, separator, max, nil, ctx)

  # The 6-tuple adds a conjunction for prose lists: "A and B" for two items, "A, B, and C"
  # for more (Oxford comma). A fold suppresses it — the button would sit where the
  # conjunction belongs, and "A, B, [Show 2 more] and C" reads worse than plain commas.
  def render({:list, name, inner, separator, max, conjunction}, ctx)
      when is_integer(max) and max > 0 and is_binary(conjunction),
      do: render_list(name, inner, separator, max, conjunction, ctx)

  # A preview is a hard display bound, not a fold: large lists stay out of the initial HTML
  # instead of merely moving behind a disclosure. Configs pair it with {:count, name} and
  # point readers at the KGX download when the unabridged values matter.
  def render({:preview, name, inner, separator, max}, ctx)
      when is_integer(max) and max > 0 and is_binary(separator) do
    case fetch(ctx, name) do
      values when is_list(values) and values != [] ->
        values
        |> Stream.map(&render_element(&1, inner, ctx))
        |> Stream.reject(&(&1 == []))
        |> Enum.take(max)
        |> interleave(Segment.text(separator))
        |> Segment.join()

      _ ->
        []
    end
  end

  # {:strong, inner}: emphasis — rendered louder than the surrounding prose. Used for the
  # primary knowledge source in a source list, where bold carries the distinction a
  # "(primary)" parenthetical used to spell out. An empty inner means no emphasis.
  def render({:strong, inner}, ctx) do
    case render(inner, ctx) do
      [] -> []
      rendered -> [{:strong, rendered}]
    end
  end

  # {:fold, label, inner}: like a capped list's fold, but for a single fact the sentence is
  # better off without — a node's CURIE, which identifies but does not advance the claim. The
  # chip is named for what it holds ("curie"), not a count; expanding reveals the inner spec
  # in place. An inner render of nothing means no chip at all.
  def render({:fold, label, inner}, ctx) when is_binary(label) do
    case render(inner, ctx) do
      [] -> []
      rendered -> [{:fold, label, Segment.join([Segment.text(" ") | List.wrap(rendered)])}]
    end
  end

  def render({:pick, name, branches}, ctx) do
    case fetch(ctx, name) do
      nil -> render(Map.get(branches, :default), ctx)
      value -> render(Map.get(branches, stringify(value), Map.get(branches, :default)), ctx)
    end
  end

  # {:number, field, :sig2 | :int}: the legacy Perl used sprintf("%.2g", $p) and sprintf("%.0f", $n).
  # :sig2 renders two significant digits in scientific notation — a deliberate change from Perl's
  # %g, because "1.2e-9" is unambiguous at a glance where "0.0000000012" invites miscounting zeros.
  def render({:number, name, format}, ctx) do
    case fetch(ctx, name) do
      nil -> []
      value -> value |> format_number(format) |> Segment.text() |> List.wrap()
    end
  end

  # {:humanize, field}: KGX predicates arrive as "biolink:correlated_with". The Perl stripped the
  # prefix and swapped underscores for spaces to get prose; same here.
  def render({:humanize, name}, ctx) do
    case fetch(ctx, name) do
      nil -> []
      value -> [Segment.text(humanize(stringify(value)))]
    end
  end

  # {:local, name}: the part of a CURIE after its prefix — "biolink:applied_to_treat" renders
  # as "applied_to_treat". The biolink model's docs site names every term's page by this local
  # id, so a sentence can link "its predicate is applied to treat" straight at the term's own
  # page; a {:url} template needs the bare id, and a template string cannot strip a prefix.
  def render({:local, name}, ctx) do
    case fetch(ctx, name) do
      nil ->
        []

      value ->
        case stringify(value) do
          nil -> []
          curie -> [Segment.text(curie |> String.split(":", parts: 2) |> List.last())]
        end
    end
  end

  # {:count, field}: the length of a list, for "3 clinical trials" phrasing.
  def render({:count, name}, ctx) do
    case fetch(ctx, name) do
      nil -> []
      value when is_list(value) -> [Segment.text(Integer.to_string(length(value)))]
      _value -> [Segment.text("1")]
    end
  end

  # {:default, field, fallback}: the Perl `//` and `||` idiom. A field the release dropped falls
  # back rather than blanking the sentence; the fallback is itself a spec, so it can be a literal,
  # another field, or a link.
  def render({:default, name, fallback}, ctx) do
    case fetch(ctx, name) do
      nil -> render(fallback, ctx)
      "" -> render(fallback, ctx)
      [] -> render(fallback, ctx)
      _value -> render({:field, name}, ctx)
    end
  end

  # DAKP logs how every assertion was read into the supporting_text list as "key: value"
  # entries; each qualifier carries its readable source phrase under original_{qualifier}
  # ("breast", "pediatric patients", "daily"). Pipe-separated alternates keep the first —
  # the canonical read, the same convention original_subject/original_object use. A rewording
  # map lets the curator repair values that read as fragments in a sentence ("adult" becomes
  # "adults", "7 days" becomes "for 7 days") without touching the stored data; keys are
  # downcased and matched case-insensitively. When the entry is absent the fallback renders,
  # so older blobs fall back to the labelled CURIE.
  def render({:supporting, key, prefix, suffix, rewordings, fallback}, ctx) do
    case supporting_value(ctx.doc, key) do
      nil ->
        render(fallback, ctx)

      value ->
        reworded = Map.get(rewordings, String.downcase(value), value)
        # The frame is a template, not literal text: a role-worded prefix ("The {subject_role}
        # name matched ") has to follow the same predicate-aware slots the mapping sentences use.
        Segment.join([render(prefix, ctx), Segment.text(reworded), render(suffix, ctx)])
    end
  end

  # Source names share the search query's pipe splitting and deduplication, but read as
  # prose with the same Oxford-comma rule as source lists, without truncating originals.
  # Blank counts as absent (the {:default} convention), so an emptied field falls back to
  # the supporting_text log rather than printing nothing. The bare form renders the words as
  # they are; the framed form quotes them (see the 4-tuple below).
  def render({:names, name}, ctx) do
    names_segments(name, ctx, false)
  end

  # {:compound, clauses}: one sentence out of sentence-shaped clauses, each already framed
  # and self-dropping. Empty clauses vanish; one clause stays a plain sentence; several are
  # joined with "; and " and closed with one period. Continuation clauses drop their capital
  # so the join reads as prose, but only for word openers — a CURIE or acronym stays as-is.
  #
  # Exactly one terminal mark per sentence is the renderer's job, not each config's. A clause
  # can arrive already punctuated: the species clause is configured with a "." suffix, and any
  # stored value can end in one. Appending blindly printed "Homo sapiens (human).." (issue #58)
  # mid-paragraph, so an interior clause hands its mark to the "; and " join and the assembled
  # sentence only receives a period through close_sentence/1, which every sentence-shaped
  # render shares.
  def render({:compound, clauses}, ctx) do
    rendered =
      clauses
      |> Enum.map(&render(&1, ctx))
      |> Enum.reject(&blank_clause?/1)

    last_index = length(rendered) - 1

    rendered
    |> Enum.with_index()
    |> Enum.map(fn {clause, index} ->
      clause = if(index == 0, do: clause, else: downcase_opener(clause))
      if(index == last_index, do: clause, else: strip_terminal(clause))
    end)
    # Stripping an interior clause's mark can empty it outright - a clause that was nothing but
    # "." - and an empty side of a "; and " join prints "A; and ; and B".
    |> Enum.reject(&blank_clause?/1)
    |> Enum.intersperse(Segment.text("; and "))
    |> Segment.join()
    |> close_sentence()
  end

  # The framed form exists to put a source's own words inside a sentence this page wrote, so it
  # quotes them: "The original treatment FLUOXETINE mapped to CHEBI:5118" reads as if the
  # identifier were part of the source string (issue #63). Each pipe-delimited variant gets its
  # own pair of quotes, because they are separate strings the source wrote, not one
  # comma-separated phrase. Straight ASCII quotes: the page never introduces smart punctuation.
  def render({:names, name, prefix, suffix}, ctx) do
    case names_segments(name, ctx, true) do
      [] -> []
      names -> Segment.join([render(prefix, ctx), names, render(suffix, ctx)])
    end
  end

  # DailyMed's query box takes Lucene boolean syntax, so a search can reach every name the
  # source used for a concept: the preferred name plus each pipe-delimited original (the
  # same convention original_subject/original_object use), each a quoted phrase, joined with
  # OR. Deduplication is case-insensitive because dumps routinely carry "Pain" and "PAIN";
  # the first spelling wins, which keeps the preferred name's casing when it survives. The
  # 3-tuple is the phrase-only form; the 4-tuple opts into extra variant shapes per name.
  def render({:or_query, name, original_name}, ctx) do
    render({:or_query, name, original_name, %{variants: [:phrase]}}, ctx)
  end

  # %{variants: [:phrase, :terms]} adds, after each quoted phrase, the bare word-conjunction
  # form of a multiword name ("blood pressure increased" unquoted). DailyMed documents bare
  # multi-word groups as word conjunctions, and a conjunction survives reorderings a quoted
  # phrase cannot: DAKP corroborates a treats edge when the label names a substring of the
  # FAERS indication, so the stored phrase can be longer than any label wording and the
  # quoted phrase alone can match nothing. Parenthetical semantic qualifiers ("Asthma
  # (disorder)") are stripped from the bare form because labels never print them; a bare
  # form under two words is semantically its quoted phrase and is dropped.
  def render({:or_query, name, original_name, %{variants: variants}}, ctx) do
    terms = query_terms([fetch(ctx, name), fetch(ctx, original_name)])

    terms
    |> Enum.flat_map(&query_variants(&1, variants))
    |> Enum.uniq_by(&String.downcase/1)
    |> Enum.map_join(" OR ", & &1)
    |> Segment.text()
    |> List.wrap()
  end

  # A link whose URL is built from document fields: {:url, "https://…?query={subject_name}", label}.
  # Field values are percent-encoded, so a drug name with a space or an ampersand cannot break the
  # query or smuggle a parameter.
  def render({:url, template, label}, ctx) when is_binary(template) do
    # Slots resolve first, then fields, exactly like a string template: a URL may need a
    # transformed value (a CURIE's local id) just as much as a sentence does.
    url =
      Regex.replace(~r/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/, template, fn _full, name ->
        value =
          case Map.fetch(ctx.slots, name) do
            {:ok, spec} -> spec |> render(ctx) |> Segment.to_text()
            :error -> fetch(ctx, name) |> stringify()
          end

        URI.encode_www_form(value || "")
      end)

    text = label |> render(ctx) |> Segment.to_text()

    case Segment.link(url, text) do
      nil -> []
      segment -> [segment]
    end
  end

  def render({:if, conditions, then_spec, else_spec}, ctx) do
    if all?(conditions, ctx), do: render(then_spec, ctx), else: render(else_spec, ctx)
  end

  # The 3-tuple form is what configs naturally want: include this when true, otherwise nothing.
  def render({:if, conditions, then_spec}, ctx) do
    render({:if, conditions, then_spec, nil}, ctx)
  end

  def render(other, _ctx) do
    raise ArgumentError, """
    unsupported value spec: #{inspect(other)}

    Valid forms are a template string, {:field, name}, {:link, label_field, curie_field},
    {:list, name, inner, separator}, {:list, name, inner, separator, max}, {:list, name,
    inner, separator, max, conjunction}, {:preview, name, inner, separator, max},
    {:names, field}, {:names, field, prefix, suffix},
    {:compound, clauses}, {:fold, label, inner}, {:strong, inner}, {:pick,
    name, branches}, {:local, name} and {:if, conditions, then, else}.
    `mix linkouts.check` reports this at build time rather than on a page view.
    """
  end

  # Shared by both {:names} arities: the bare form quotes nothing, the framed form quotes each
  # term. Quoting happens per term, before the Oxford-comma join, so the conjunctions stay
  # outside the quotes ("A" and "B", never "A and B").
  defp names_segments(name, ctx, quoted?) do
    values =
      case fetch(ctx, name) do
        nil -> original_entries(ctx.doc, to_string(name))
        "" -> original_entries(ctx.doc, to_string(name))
        [] -> original_entries(ctx.doc, to_string(name))
        value -> [value]
      end

    values
    |> query_terms()
    |> Enum.map(&[Segment.text(term_phrase(&1, quoted?))])
    |> interleave_with(", ", "and", [])
    |> Segment.join()
  end

  # A term that already carries double quotes is not wrapped again. An original stored as
  # ~s(Pain "acute") would otherwise print as "Pain "acute"" - nested quotes the reader has to
  # unpick. Such a term is rare enough that printing it as written beats inventing a second
  # quoting style for it.
  defp term_phrase(term, true) do
    if String.contains?(term, "\""), do: term, else: "\"" <> term <> "\""
  end

  defp term_phrase(term, false), do: term

  # Sentence assembly for {:compound, clauses}. Kept below the render/2 multi-clause so the
  # public clauses stay grouped together (the compiler warns otherwise, and CI runs with
  # --warnings-as-errors).

  # Continuation clauses read as prose, not as sentences glued together: only a word opener is
  # downcased, so "Gene family MESH:D001 involved" keeps its acronym and a CURIE opener is
  # never mangled.
  defp downcase_opener([{:text, <<first::utf8, second::utf8, rest::binary>>} | tail])
       when first in ?A..?Z and second in ?a..?z do
    [{:text, String.downcase(<<first::utf8>>) <> <<second::utf8>> <> rest} | tail]
  end

  defp downcase_opener(clause), do: clause

  # An interior clause keeps no terminal mark: "...(human).; and the object mapped to X" reads
  # as two sentences fighting over one connector. The mark and any spacing around it go, so a
  # stored value ending in ". " cannot print "(human) ; and".
  @interior_mark ~r/[.;,]+\z/

  defp strip_terminal(clause) do
    update_trailing_text(clause, &interior_text/1)
  end

  defp interior_text(text) do
    text
    |> String.trim_trailing()
    |> strip_marks()
    |> String.trim_trailing()
  end

  defp strip_marks(text), do: Regex.replace(@interior_mark, text, "")

  defp blank_clause?(clause), do: clause |> Segment.to_text() |> String.trim() == ""

  @doc """
  Close a rendered sentence with exactly one terminal mark.

  A sentence-shaped render - a compound sentence, one provenance bullet - owns its punctuation,
  so a value that already ends in "." never prints ".." (issue #58). A clause ending in "!",
  "?", ";" or ":" is left exactly as written, because gluing a period onto it ("Really?.") is
  worse than the mark it carries, and an empty render stays empty rather than printing a bare
  ".".
  """
  @spec close_sentence([Segment.t()]) :: [Segment.t()]
  def close_sentence(body) do
    body = body |> update_trailing_text(&tidy_trailing/1) |> Segment.join()

    case body |> Segment.to_text() |> String.last() do
      nil -> body
      mark when mark in [".", "!", "?", ";", ":"] -> body
      _ -> Segment.join([body, Segment.text(".")])
    end
  end

  # "Foo ." from a value that ended in a space becomes "Foo.", so the mark the sentence already
  # has sits against its words instead of hanging off one.
  @space_before_mark ~r/\s+([.;!?])\z/

  defp tidy_trailing(text) do
    text |> String.trim_trailing() |> pull_mark_to_words()
  end

  defp pull_mark_to_words(text), do: Regex.replace(@space_before_mark, text, "\\1")

  # The last thing a sentence prints is not always a plain text segment: it can be a link label
  # or the inside of an emphasis chip or a "show more" fold. Punctuation and spacing decisions
  # have to see that trailing text where it actually lives, or the mark check and the rewrite
  # disagree and "...(human).." comes back through a different path.
  defp update_trailing_text(segments, fun) do
    case segments |> Enum.reverse() |> Enum.reject(&is_nil/1) do
      [{:text, text} | rest] ->
        Enum.reverse(rest, [Segment.text(fun.(text))])

      [{:link, href, label} | rest] ->
        Enum.reverse(rest, [Segment.link(href, fun.(label))])

      [{:strong, inner} | rest] ->
        Enum.reverse(rest, wrap(:strong, update_trailing_text(inner, fun)))

      [{:more, inner} | rest] ->
        Enum.reverse(rest, wrap(:more, update_trailing_text(inner, fun)))

      [{:fold, label, inner} | rest] ->
        Enum.reverse(rest, [{:fold, label, update_trailing_text(inner, fun)}])

      [] ->
        []

      [other | rest] ->
        Enum.reverse(rest, [other])
    end
    |> Segment.join()
  end

  defp wrap(_tag, []), do: nil
  defp wrap(tag, inner), do: [{tag, inner}]

  defp render_list(name, inner, separator, max, conjunction, ctx) do
    case fetch(ctx, name) do
      values when is_list(values) and values != [] ->
        rendered =
          values
          |> Enum.map(&render_element(&1, inner, ctx))
          |> Enum.reject(&(&1 == []))

        {shown, hidden} =
          if max && length(rendered) > max, do: Enum.split(rendered, max), else: {rendered, []}

        shown
        |> interleave_with(separator, conjunction, hidden)
        |> Kernel.++(more_segment(hidden, separator))
        |> Segment.join()

      _ ->
        []
    end
  end

  # A conjunction turns the comma run into prose: "KP and DailyMed" for two items, with the
  # Oxford comma from three up. Anything folded (or no conjunction) keeps plain separators.
  defp interleave_with(items, separator, conjunction, hidden)

  defp interleave_with(items, separator, conjunction, [])
       when is_binary(conjunction) and length(items) > 1 do
    {last, rest} = List.pop_at(items, -1)
    last_join = if length(rest) > 1, do: "#{separator}#{conjunction} ", else: " #{conjunction} "
    Enum.intersperse(rest, Segment.text(separator)) ++ [Segment.text(last_join), last]
  end

  defp interleave_with(items, separator, _conjunction, _hidden),
    do: interleave(items, Segment.text(separator))

  # The fold carries the leading separator so a flattened read (plain text, link extraction)
  # restores the exact sequence the open disclosure shows.
  defp more_segment([], _separator), do: []

  defp more_segment(hidden, separator) do
    [
      {:more,
       List.flatten([Segment.text(separator) | interleave(hidden, Segment.text(separator))])}
    ]
  end

  # A list element becomes the document for the inner spec: a map element (a KGX `sources` entry)
  # is read by field name, and a scalar element (a CURIE) is reachable as {self}. Keeping the
  # parent document out of scope is deliberate — inside a list, "resource_id" must mean this
  # element's, not one inherited from the edge.
  defp render_element(element, inner, ctx) do
    doc = if is_map(element), do: element, else: %{"__self__" => element}
    render(inner, %{ctx | doc: doc})
  end

  # Finds the value of a supporting_text "key: value" entry. The entry format is the
  # pipeline's log line, so the key is everything before the first colon and the value is
  # everything after it, trimmed. An empty value counts as absent: the fallback must win,
  # or a sentence would carry a dangling frame like ", in the ".
  defp supporting_value(doc, key) do
    doc
    |> Map.get("supporting_text")
    |> supporting_entries()
    |> Enum.find_value(fn entry ->
      case String.split(entry, ":", parts: 2) do
        [entry_key, value] when entry_key == key ->
          value
          |> String.split("|")
          |> List.first()
          |> String.trim()
          |> case do
            "" -> nil
            trimmed -> trimmed
          end

        _ ->
          nil
      end
    end)
  end

  defp supporting_entries(entries) when is_binary(entries), do: [entries]
  defp supporting_entries(entries) when is_list(entries), do: Enum.filter(entries, &is_binary/1)
  defp supporting_entries(_other), do: []

  defp original_entries(doc, key) do
    doc
    |> Map.get("supporting_text")
    |> supporting_entries()
    |> Enum.flat_map(fn entry ->
      case String.split(entry, ":", parts: 2) do
        [^key, value] -> [value]
        _ -> []
      end
    end)
  end

  defp query_variants(term, variants) do
    quoted = ~s("#{term}")

    bare =
      if :terms in variants do
        term
        # Leaf parentheticals first ("Asthma (disorder)" -> "Asthma"), then any residual
        # bracket characters: unbalanced or nested input cannot be balance-checked by regex,
        # and a leaked paren would turn the whole Lucene query into a parse error.
        |> String.replace(~r/[\(\[][^)\]]*[\)\]]/, " ")
        |> String.replace(~r/[\(\[\]\)]/, "")
        |> String.replace(~r/\s+/, " ")
        |> String.trim()
      end

    if is_binary(bare) and bare != "" and bare =~ " ", do: [quoted, bare], else: [quoted]
  end

  defp query_terms(fields) do
    fields
    |> Enum.flat_map(fn
      value when is_binary(value) -> [value]
      value when is_list(value) -> Enum.filter(value, &is_binary/1)
      _ -> []
    end)
    |> Enum.flat_map(&String.split(&1, "|"))
    |> Enum.map(&String.trim/1)
    |> Enum.reject(&(&1 == ""))
    |> Enum.uniq_by(&String.downcase/1)
  end

  @doc "Evaluates every condition; an empty list is true."
  @spec all?([condition()], ctx()) :: boolean()
  def all?(conditions, ctx), do: Enum.all?(conditions, &condition?(&1, ctx))

  @doc "Evaluates one condition."
  @spec condition?(condition(), ctx()) :: boolean()
  def condition?({:present, name}, ctx) do
    case fetch(ctx, name) do
      nil -> false
      "" -> false
      [] -> false
      _ -> true
    end
  end

  def condition?({:eq, name, expected}, ctx) do
    case fetch(ctx, name) do
      nil -> false
      value -> stringify(value) == stringify(expected)
    end
  end

  def condition?({:version, requirement}, ctx) do
    Version.satisfies?(ctx.version, requirement)
  end

  # A regex condition, for predicates that arrive as a family rather than one value: the drug
  # approvals KG has several contraindication predicates and the legacy code matched them all with
  # /^biolink:contraindicated/. Compiled at call time, which is cheap relative to a Cosmos read.
  def condition?({:matches, name, pattern}, ctx) do
    case fetch(ctx, name) do
      nil -> false
      value -> Regex.match?(Regex.compile!(pattern), stringify(value))
    end
  end

  # Numeric comparisons, for "positively/negatively associated" driven by a coefficient's sign.
  # A missing or non-numeric value is false rather than an error: half the releases in the wild
  # omit these fields, and a blank sentence beats a crash.
  for {tag, op} <- [lt: :<, gt: :>, lte: :<=, gte: :>=] do
    def condition?({unquote(tag), name, bound}, ctx) when is_number(bound) do
      case fetch(ctx, name) do
        value when is_number(value) -> Kernel.unquote(op)(value, bound)
        _ -> false
      end
    end

    def condition?({unquote(tag), _name, bound}, _ctx) do
      raise ArgumentError,
            "{:#{unquote(tag)}, field, bound} needs a numeric bound, got #{inspect(bound)}"
    end
  end

  # Length of a list field, for pluralization. Counting is not expressible with the numeric
  # comparisons, which read a number out of the document rather than measuring a list.
  def condition?({:count_gt, name, bound}, ctx) when is_integer(bound) do
    case fetch(ctx, name) do
      value when is_list(value) -> length(value) > bound
      nil -> false
      _ -> 1 > bound
    end
  end

  def condition?({:count_gt, _, bound}, _ctx) do
    raise ArgumentError, "{:count_gt, field, integer} expected, got bound #{inspect(bound)}"
  end

  def condition?({:all, conditions}, ctx), do: all?(conditions, ctx)
  def condition?({:any, conditions}, ctx), do: Enum.any?(conditions, &condition?(&1, ctx))

  def condition?(other, _ctx) do
    raise ArgumentError,
          "unsupported condition: #{inspect(other)} (expected :present, :eq, :version, :all or :any)"
  end

  @doc """
  Reads a field, trying the canonical name and then any aliases configured for this version.

  This indirection is why real DAKP releases work: `FDA_regulatory_approvals` became
  `regulatory_approvals`, and not on a clean version boundary — the same version appears in two
  dumps with different spellings. Taking the first candidate that is present means one config
  renders both, and a document carrying the canonical spelling is never shadowed by a stale alias.
  """
  @spec fetch(ctx(), atom() | String.t()) :: term() | nil
  def fetch(ctx, name) do
    key = to_string(name)

    # :self is how {:list, ...} passes a scalar element to its inner spec.
    if key == "self" do
      Map.get(ctx.doc, "__self__")
    else
      ctx
      |> Map.get(:aliases, %{})
      |> Map.get(key, [key])
      |> Enum.find_value(fn candidate ->
        case Map.fetch(ctx.doc, candidate) do
          {:ok, value} -> value
          :error -> nil
        end
      end)
    end
  end

  @doc """
  Resolves a `{name}` reference: a defined slot if there is one, otherwise a document field.

  Slot-first means a config can override how any field renders by defining a slot with that name,
  and field fallback means `{subject_name}` works without ceremony. The fallback reads through
  version aliases, so `{regulatory_approvals}` resolves in both 1.11.2 and 1.16.0 documents.
  """
  @spec lookup_slot(String.t(), ctx()) :: [Segment.t()]
  def lookup_slot(name, ctx) do
    case Map.fetch(ctx.slots, name) do
      {:ok, spec} -> render(spec, ctx)
      :error -> render({:field, name}, ctx)
    end
  end

  defp format_number(value, :int) when is_integer(value), do: Integer.to_string(value)
  defp format_number(value, :int) when is_float(value), do: Integer.to_string(round(value))
  defp format_number(value, :int), do: stringify(value) || ""

  # Two significant digits. Small and large magnitudes use scientific notation with an unpadded
  # exponent ("1.2e-9", not Erlang's "1.2e-09"), which is the form a reader expects for p-values;
  # ordinary magnitudes print plainly so a coefficient of 0.42 does not become "4.2e-1".
  defp format_number(value, :sig2) when is_float(value) do
    magnitude = abs(value)

    if magnitude != 0.0 and (magnitude < 1.0e-3 or magnitude >= 1.0e4) do
      # Erlang writes "1.2e-09"; strip the exponent's padding and any explicit "+".
      Regex.replace(~r/e([+-])0*(\d+)$/, :erlang.float_to_binary(value, scientific: 1), fn
        _, "+", digits -> "e" <> digits
        _, "-", digits -> "e-" <> digits
      end)
    else
      value |> Float.round(significant_decimals(magnitude)) |> trim_trailing_zero()
    end
  end

  # printf-style "%.4g": four significant digits (Erlang rounds exact decimal ties away
  # from zero, unlike Python's half-even rounding),
  # scientific notation when the exponent is below -4 or at least 4, trailing zeros dropped,
  # a signed two-digit exponent ("5.677e-07", "1.235e+05"). Numeric strings are formatted too,
  # since some sources store p-values as text; an inequality keeps its operator ("<1e-05").
  defp format_number(value, :g4) when is_integer(value), do: g4(value * 1.0)
  defp format_number(value, :g4) when is_float(value), do: g4(value)

  defp format_number(value, :g4) when is_binary(value) do
    with [_, op, number] <- Regex.run(~r/^\s*(<=|>=|<|>|=)?\s*(\S+)\s*$/, value),
         {float, ""} <- Float.parse(number),
         false <- underflowed?(float, number) do
      op <> g4(float)
    else
      _ -> value
    end
  end

  defp format_number(value, :g4), do: stringify(value) || ""

  defp format_number(value, :sig2) when is_integer(value), do: Integer.to_string(value)
  # A source that spells a missing number "NA" must not become "0" or crash the render.
  defp format_number(value, :sig2), do: stringify(value) || ""

  defp format_number(_value, other) do
    raise ArgumentError,
          "unsupported {:number, field, #{inspect(other)}} format; expected :sig2, :g4 or :int"
  end

  # The %.4g exponent is the one scientific notation shows after rounding to four digits
  # (9999.5 rounds to 1.000e+04, so it prints as "1e+04", not "9999").
  defp g4(value) do
    scientific = :erlang.float_to_binary(value, scientific: 3)
    [mantissa, exponent] = String.split(scientific, "e")
    exponent = String.to_integer(exponent)

    if exponent < -4 or exponent >= 4 do
      sign = if exponent < 0, do: "-", else: "+"
      digits = exponent |> abs() |> Integer.to_string() |> String.pad_leading(2, "0")
      strip_fraction_zeros(mantissa) <> "e" <> sign <> digits
    else
      value |> :erlang.float_to_binary(decimals: 3 - exponent) |> strip_fraction_zeros()
    end
  end

  defp strip_fraction_zeros(digits) do
    if String.contains?(digits, ".") do
      digits |> String.trim_trailing("0") |> String.trim_trailing(".")
    else
      digits
    end
  end

  # Float.parse("1e-400") is {0.0, ""}: a nonzero p-value below the double range must not
  # print as "0", so it stays as the source wrote it.
  defp underflowed?(float, text), do: float == 0.0 and Regex.match?(~r/^[^eE]*[1-9]/, text)

  # Decimals needed for two significant digits at this magnitude: 0.123 -> 2, 0.0123 -> 3, 12.3 -> 0.
  defp significant_decimals(magnitude) when magnitude == 0.0, do: 0

  defp significant_decimals(magnitude) do
    max(0, 1 - floor(:math.log10(magnitude)))
  end

  defp trim_trailing_zero(rounded) do
    case Float.ratio(rounded) do
      {_, 1} -> rounded |> trunc() |> Integer.to_string()
      _ -> :erlang.float_to_binary(rounded, [:short])
    end
  end

  defp humanize(value) do
    value
    |> String.replace(~r/^biolink:/i, "")
    |> String.replace("_", " ")
  end

  defp interleave([single], _sep), do: [single]
  defp interleave([], _sep), do: []
  defp interleave([head | rest], sep), do: [head, sep | interleave(rest, sep)]

  @doc "The printable form of a document value."
  @spec stringify(term()) :: String.t() | nil
  def stringify(nil), do: nil
  def stringify(value) when is_binary(value), do: value
  def stringify(value) when is_integer(value), do: Integer.to_string(value)
  def stringify(value) when is_float(value), do: trim_float(value)
  def stringify(value) when is_boolean(value), do: Atom.to_string(value)
  def stringify([]), do: nil

  def stringify(value) when is_list(value),
    do: value |> Enum.map(&stringify/1) |> Enum.reject(&is_nil/1) |> Enum.join(", ")

  def stringify(value) when is_map(value), do: value |> JSON.encode!()

  # 12.0 is a count of something, not a measurement; showing "12" matches what the legacy page
  # printed and avoids implying precision the source never had.
  defp trim_float(value) do
    case Float.ratio(value) do
      {_, 1} -> Integer.to_string(trunc(value))
      _ -> :erlang.float_to_binary(value, [:short])
    end
  end
end

defmodule EdgeLinkouts.Display.ValueTest do
  use ExUnit.Case, async: true

  alias EdgeLinkouts.Display.{Config, Segment, Value}

  defp or_query_ctx(doc) do
    %{doc: doc, version: "kg-1.0.0", slots: %{}, aliases: %{}}
  end

  defp number(value, format) do
    ctx = %{doc: %{"n" => value}, version: "kg-1.0.0", slots: %{}, aliases: %{}}
    {:number, "n", format} |> Value.render(ctx) |> Segment.to_text()
  end

  describe "{:names, field}" do
    test "pipe-delimited originals use Oxford commas and omit blanks and duplicates" do
      for {value, expected} <- [
            {"Zyrtec", "Zyrtec"},
            {"A|B", "A and B"},
            {" A | B | C | A || ", "A, B, and C"},
            {["A|B", "C"], "A, B, and C"},
            {nil, ""},
            {" | ", ""}
          ] do
        ctx = %{doc: %{"original" => value}, version: "kg-1.0.0", slots: %{}, aliases: %{}}
        assert Value.render({:names, "original"}, ctx) |> Segment.to_text() == expected

        framed = Value.render({:names, "original", "Original: ", "."}, ctx) |> Segment.to_text()
        assert framed == if(expected == "", do: "", else: "Original: #{expected}.")
      end
    end
  end

  describe "compound mappings" do
    test "empty clauses drop, one stays a sentence, and two become a compound sentence" do
      ctx = %{doc: %{}, version: "kg-1.0.0", slots: %{}, aliases: %{}}

      for {clauses, expected} <- [
            {[], ""},
            {[nil, "The subject mapped to X:1"], "The subject mapped to X:1."},
            {["The subject mapped to X:1", "The object mapped to Y:2"],
             "The subject mapped to X:1; and the object mapped to Y:2."}
          ] do
        assert Value.render({:compound, clauses}, ctx) |> Segment.to_text() == expected
      end
    end

    test "a clause that already ends in sentence punctuation is not given a second mark" do
      # Regression for issue #58: the species clause is configured with a "." suffix and the
      # closer used to append another one unconditionally, printing "Homo sapiens (human)..".
      # The closer owns sentence punctuation, so it must be idempotent for any value that
      # arrives already punctuated, not just for that one config line.
      ctx = %{doc: %{}, version: "kg-1.0.0", slots: %{}, aliases: %{}}

      for {clauses, expected} <- [
            {["This assertion is about Homo sapiens (human)."],
             "This assertion is about Homo sapiens (human)."},
            {["Did the label say so?"], "Did the label say so?"},
            {["Human curation!"], "Human curation!"},
            {["Recorded by FAERS;"], "Recorded by FAERS;"},
            # A stored value can carry trailing whitespace, and the mark it ends with can sit
            # after that space. Neither may survive as "(human) ." or "(human)..".
            {["This assertion is about Homo sapiens (human). "],
             "This assertion is about Homo sapiens (human)."},
            {["This assertion is about Homo sapiens (human) "],
             "This assertion is about Homo sapiens (human)."}
          ] do
        text = Value.render({:compound, clauses}, ctx) |> Segment.to_text()
        assert text == expected
        refute text =~ ".."
        refute text =~ " ."
      end
    end

    test "the closer sees punctuation inside a link or a chip, not only plain text" do
      # The last thing a sentence prints is often a link ("...mapped to CHEBI:5118") or the
      # inside of an emphasis chip. If the mark check and the mark strip only looked at a
      # trailing {:text} segment they would miss a mark living there, and ".." would come back
      # through a path the config never wrote.
      ctx = %{
        doc: %{"label" => "CHEBI:5118.", "curie" => "CHEBI:5118"},
        version: "kg-1.0.0",
        slots: %{"id" => {:link, "label", "curie"}},
        aliases: %{}
      }

      ending_in_link = Value.render({:compound, ["The subject mapped to {id}"]}, ctx)

      assert Segment.to_text(ending_in_link) == "The subject mapped to CHEBI:5118."
      assert link_label(ending_in_link) == "CHEBI:5118."

      chip = Value.render({:compound, [{:strong, "FAERS reports."}]}, ctx)
      assert Segment.to_text(chip) == "FAERS reports."

      # Interior: the mark inside the link label has to yield to the "; and " join as plain
      # text does, and the label itself must not keep it.
      joined =
        Value.render({:compound, ["The subject mapped to {id}", "The object mapped to Y:2"]}, ctx)
        |> Segment.to_text()

      assert joined == "The subject mapped to CHEBI:5118; and the object mapped to Y:2."
      refute joined =~ ".."
      refute joined =~ ".; and"
    end

    test "an interior clause left empty by its own mark does not buy a joiner" do
      # A clause that is nothing but "." loses that mark to the "; and " join and collapses to
      # nothing. Joining it anyway printed "A; and ; and B".
      ctx = %{doc: %{"id" => "x"}, version: "kg-1.0.0", slots: %{}, aliases: %{}}

      text =
        Value.render(
          {:compound, ["The subject mapped to X:1", ".", "The object mapped to Y:2"]},
          ctx
        )
        |> Segment.to_text()

      assert text == "The subject mapped to X:1; and the object mapped to Y:2."
      refute text =~ "; and ;"
      refute text =~ "; and ."
    end

    test "an interior clause yields its terminal mark to the '; and' join" do
      # Two sentences cannot share one connector: "...(human).; and the object mapped to X"
      # reads as a broken sentence. The mark belongs to the assembled sentence, so only the
      # closing clause carries one.
      ctx = %{doc: %{}, version: "kg-1.0.0", slots: %{}, aliases: %{}}

      text =
        Value.render(
          {:compound,
           ["This assertion is about Homo sapiens (human).", "The object mapped to X:1"]},
          ctx
        )
        |> Segment.to_text()

      assert text ==
               "This assertion is about Homo sapiens (human); and the object mapped to X:1."

      refute text =~ ".; and"
    end

    test "a clause that renders to nothing prints no bare period" do
      # An empty evidence entry must drop out entirely; a lone "." on the page is worse than
      # no sentence, and it is what the old unconditional closer produced for blank input.
      ctx = %{doc: %{}, version: "kg-1.0.0", slots: %{}, aliases: %{}}

      for clauses <- [[""], [nil], ["", nil], []] do
        assert Value.render({:compound, clauses}, ctx) |> Segment.to_text() == ""
      end
    end

    test "close_sentence/1 is idempotent, for any sentence-shaped render" do
      # The closer is shared by {:compound} and the provenance list, so a caller that builds a
      # sentence outside a compound gets the same one-mark rule. Without it a bullet ending in
      # a period would double it exactly like the compound closer used to.
      for {input, expected} <- [
            {[], ""},
            {[Segment.text("The product lists fluoxetine")], "The product lists fluoxetine."},
            {[Segment.text("This assertion is about Homo sapiens (human).")],
             "This assertion is about Homo sapiens (human)."},
            # A trailing link label is part of the sentence, so its punctuation counts.
            {[Segment.text("mapped to "), Segment.link("https://x/1", "CHEBI:1.")],
             "mapped to CHEBI:1."}
          ] do
        assert Value.close_sentence(input) |> Segment.to_text() == expected
      end
    end

    test "continuation clauses downcase word openers but never CURIE prefixes" do
      ctx = %{
        doc: %{"x" => "MESH:D001"},
        version: "kg-1.0.0",
        slots: %{"x" => {:link, "x", "x"}},
        aliases: %{}
      }

      text =
        Value.render({:compound, ["The subject mapped to X:1", "Gene family {x} involved"]}, ctx)
        |> Segment.to_text()

      assert text == "The subject mapped to X:1; and gene family MESH:D001 involved."
    end

    test "a blank field falls back to the supporting_text log" do
      for blank <- ["", []] do
        ctx = %{
          doc: %{
            "original_subject" => blank,
            "supporting_text" => ["original_subject: Zyrtec|Cetirizine"]
          },
          version: "kg-1.0.0",
          slots: %{},
          aliases: %{}
        }

        assert Value.render({:names, "original_subject"}, ctx) |> Segment.to_text() ==
                 "Zyrtec and Cetirizine"
      end
    end

    test "qualifier originals also resolve pipe-delimited supporting-text entries" do
      ctx = %{
        doc: %{"supporting_text" => ["original_sex_qualifier: male|men"]},
        version: "kg-1.0.0",
        slots: %{},
        aliases: %{}
      }

      assert Value.render({:names, "original_sex_qualifier"}, ctx) |> Segment.to_text() ==
               "male and men"
    end
  end

  describe "{:number, field, :sig2}" do
    test "p-value magnitudes use unpadded scientific notation" do
      # Erlang's own output is "1.2e-09"; a reader expects "1.2e-9".
      assert number(0.0000000012, :sig2) == "1.2e-9"
      assert number(0.00045, :sig2) == "4.5e-4"
      assert number(123_456.0, :sig2) == "1.2e5"
    end

    test "ordinary magnitudes print plainly at two significant digits" do
      assert number(0.123456, :sig2) == "0.12"
      assert number(-0.42, :sig2) == "-0.42"
      assert number(0.0123, :sig2) == "0.012"
      assert number(12.34, :sig2) == "12"
      assert number(3.0, :sig2) == "3"
    end

    test "zero, integers and non-numbers pass through" do
      assert number(0.0, :sig2) == "0"
      assert number(7, :sig2) == "7"
      # Sources spell a missing number "NA"; it must not become 0 or crash.
      assert number("NA", :sig2) == "NA"
    end
  end

  describe "{:number, field, :g4}" do
    # General-format precision and notation; exact decimal ties use Erlang's rounding.
    test "floats and integers use four significant digits" do
      for {value, expected} <- [
            {5.6767e-07, "5.677e-07"},
            {6.276975374, "6.277"},
            {0.947, "0.947"},
            {-1.11, "-1.11"},
            {1.0, "1"},
            {0.0, "0"},
            {0.0001, "0.0001"},
            {1.234e-05, "1.234e-05"},
            {123_456, "1.235e+05"},
            {9_999.5, "1e+04"},
            {99.995, "100"},
            {1234.5, "1235"},
            {100.25, "100.3"},
            {1.0e100, "1e+100"},
            {-2.5e-300, "-2.5e-300"},
            {1.2345e-320, "1.235e-320"}
          ] do
        assert number(value, :g4) == expected, "#{inspect(value)}"
      end
    end

    test "numeric strings are reformatted, inequalities kept, the rest verbatim" do
      for {value, expected} <- [
            {"7.8600e-06", "7.86e-06"},
            {"1.0700e-04", "0.000107"},
            {"3.2100e+02", "321"},
            {"<1.00e-5", "<1e-05"},
            {"<= 0.05", "<=0.05"},
            {"1e-400", "1e-400"},
            {"1e400", "1e400"},
            {"0.0", "0"},
            {"NA", "NA"},
            {"", ""}
          ] do
        assert number(value, :g4) == expected, "#{inspect(value)}"
      end
    end
  end

  describe "{:number, field, :int}" do
    test "rounds floats the way the legacy sprintf(\"%.0f\") did" do
      assert number(412.0, :int) == "412"
      assert number(411.6, :int) == "412"
      assert number(9, :int) == "9"
    end
  end

  describe "{:local, field}" do
    test "a CURIE renders as its local id" do
      ctx = %{
        doc: %{"p" => "biolink:applied_to_treat"},
        version: "kg-1.0.0",
        slots: %{},
        aliases: %{}
      }

      assert {:local, "p"} |> Value.render(ctx) |> Segment.to_text() == "applied_to_treat"
    end

    test "a value with no prefix passes through; a missing field renders nothing" do
      ctx = %{doc: %{"c" => "CHEBI:1234"}, version: "kg-1.0.0", slots: %{}, aliases: %{}}

      assert {:local, "c"} |> Value.render(ctx) |> Segment.to_text() == "1234"
      assert {:local, "missing"} |> Value.render(ctx) == []
    end

    test "a url template resolves slots before fields, so a local id can build a docs link" do
      ctx = %{
        doc: %{"p" => "biolink:treats"},
        version: "kg-1.0.0",
        slots: %{"local" => {:local, "p"}, "words" => {:humanize, "p"}},
        aliases: %{}
      }

      spec = {:url, "https://biolink.github.io/biolink-model/{local}/", "{words}"}
      assert [{:link, href, "treats"}] = Value.render(spec, ctx)
      assert href == "https://biolink.github.io/biolink-model/treats/"
    end
  end

  describe "{:list, field, inner, separator, max}" do
    defp list_ctx do
      %{
        doc: %{
          "ps" => ["dailymed:a1", "dailymed:b2", "dailymed:c3", "dailymed:d4", "dailymed:e5"]
        },
        version: "kg-1.0.0",
        slots: %{},
        aliases: %{}
      }
    end

    test "the first max items stay inline and the rest fold into one {:more} segment" do
      segments = Value.render({:list, "ps", {:field, "self"}, ", ", 2}, list_ctx())

      assert [{:text, prefix}, {:more, hidden}] = segments
      assert prefix =~ "dailymed:a1, dailymed:b2"
      # The fold carries its leading separator, so a flattened read is the full list.
      assert Segment.to_text(hidden) == ", dailymed:c3, dailymed:d4, dailymed:e5"

      assert Segment.to_text(segments) ==
               "dailymed:a1, dailymed:b2, dailymed:c3, dailymed:d4, dailymed:e5"
    end

    test "a list that fits under the cap renders with no fold at all" do
      segments = Value.render({:list, "ps", {:field, "self"}, ", ", 9}, list_ctx())

      refute Enum.any?(segments, &match?({:more, _}, &1))

      assert Segment.to_text(segments) ==
               "dailymed:a1, dailymed:b2, dailymed:c3, dailymed:d4, dailymed:e5"
    end
  end

  describe "{:preview, field, inner, separator, max}" do
    test "only the first max items are rendered, with no hidden fold payload" do
      ctx = %{
        doc: %{"ps" => ["dailymed:a1", "dailymed:b2", "dailymed:c3", "dailymed:d4"]},
        version: "kg-1.0.0",
        slots: %{},
        aliases: %{}
      }

      segments = Value.render({:preview, "ps", {:field, "self"}, ", ", 2}, ctx)

      assert Segment.to_text(segments) == "dailymed:a1, dailymed:b2"
      assert Segment.flatten(segments) == segments
      refute Enum.any?(segments, &match?({:more, _}, &1))
    end

    test "short, empty, and missing lists render without a placeholder" do
      ctx = %{doc: %{"ps" => ["dailymed:a1"]}, version: "kg-1.0.0", slots: %{}, aliases: %{}}

      assert Value.render({:preview, "ps", {:field, "self"}, ", ", 3}, ctx)
             |> Segment.to_text() == "dailymed:a1"

      assert Value.render({:preview, "ps", {:field, "self"}, ", ", 3}, %{ctx | doc: %{}}) == []

      assert Value.render({:preview, "ps", {:field, "self"}, ", ", 3}, %{ctx | doc: %{"ps" => []}}) ==
               []
    end
  end

  describe "preview validation" do
    test "invalid bounds and fields name the preview form" do
      for max <- [0, -1, 1.5, nil] do
        assert [message] =
                 Config.spec_problems(
                   {:preview, "publications", "{self}", ", ", max},
                   "value"
                 )

        assert message =~ "{:preview, field, inner, separator, max} expected"
      end

      assert [message] =
               Config.spec_problems({:preview, 123, "{self}", ", ", 3}, "value")

      assert message == "value: {:preview, ...} needs a field name"

      assert [message] = Config.spec_problems({:preview, "p", "{self}", nil, 3}, "value")
      assert message == "value: {:preview, ...} separator must be a string"
    end

    test "slot discovery and undefined-slot checks traverse the preview inner spec" do
      {:ok, config} =
        Config.new(%{
          name: "preview-test",
          edge: "{items}",
          slots: %{"items" => {:preview, "publications", "{label}", ", ", 3}}
        })

      assert "label" in Config.undefined_slots(config)
    end
  end

  describe "{:or_query, field, original_field}" do
    test "every name variant is a quoted phrase in one OR group, deduped case-insensitively" do
      ctx =
        or_query_ctx(%{
          "name" => "asthma",
          "original" => "asthma|Asthma (disorder)|ASTHMA"
        })

      # The preferred spelling wins the case-insensitive dedup, so "PAIN"-style dump
      # noise collapses without reordering the surviving variants.
      assert Value.render({:or_query, "name", "original"}, ctx) |> Segment.to_text() ==
               ~s|"asthma" OR "Asthma (disorder)"|
    end

    test "the 3-tuple never emits a bare word-conjunction form" do
      ctx = or_query_ctx(%{"name" => "desflurane", "original" => "DESFLURANE"})

      assert Value.render({:or_query, "name", "original"}, ctx) |> Segment.to_text() ==
               ~s("desflurane")
    end

    test "missing names render no query, matching the other field forms" do
      assert Value.render({:or_query, "name", "original"}, or_query_ctx(%{}))
             |> Segment.to_text() == ""
    end
  end

  describe "{:or_query, field, original_field, %{variants: [:phrase, :terms]}}" do
    test "a multiword variant also emits its bare word-conjunction form, right after its phrase" do
      ctx =
        or_query_ctx(%{"name" => "desflurane", "original" => "DESFLURANE|Desflurane USAN"})

      # Why the bare form exists: DAKP corroborates a treats edge when the label names a
      # substring of the stored phrase, so the quoted phrase can match nothing while the
      # documented DailyMed word conjunction ("NAME:(a b)" = both words) still reaches the
      # label. Single-word variants gain nothing over their quoted phrase.
      assert Value.render(
               {:or_query, "name", "original", %{variants: [:phrase, :terms]}},
               ctx
             )
             |> Segment.to_text() ==
               ~s("desflurane" OR "Desflurane USAN" OR Desflurane USAN)
    end

    test "single-word variants do not duplicate as bare words" do
      ctx = or_query_ctx(%{"name" => "PAIN", "original" => "PAIN|Pain"})

      assert Value.render(
               {:or_query, "name", "original", %{variants: [:phrase, :terms]}},
               ctx
             )
             |> Segment.to_text() == ~s("PAIN")
    end

    test "parenthetical semantic qualifiers are stripped from the bare form" do
      ctx =
        or_query_ctx(%{"name" => "asthma", "original" => "asthma|Asthma (disorder)"})

      # Labels never print the "(disorder)" qualifier, so the bare form of that variant
      # would be the single word "asthma", which its own quoted phrase already covers;
      # only the quoted phrases survive.
      assert Value.render(
               {:or_query, "name", "original", %{variants: [:phrase, :terms]}},
               ctx
             )
             |> Segment.to_text() == ~s|"asthma" OR "Asthma (disorder)"|
    end

    test "unbalanced or nested parentheticals cannot leak into the bare form" do
      # A leaked paren would turn the bare word conjunction into a Lucene parse error and
      # zero all three linkouts for the edge, so residual brackets are stripped outright.
      # Quoted phrases may legitimately contain literal parens, so the check strips quoted
      # segments first and only asserts the bare remainder.
      for original <- ["asthma (disorder", "bronchial asthma (disorder (chronic))"] do
        ctx = or_query_ctx(%{"name" => original, "original" => ""})

        query =
          Value.render(
            {:or_query, "name", "original", %{variants: [:phrase, :terms]}},
            ctx
          )
          |> Segment.to_text()

        bare_remainder = String.replace(query, ~r/"[^"]*"/, "")
        refute bare_remainder =~ "("
        refute bare_remainder =~ ")"
      end

      # The nested case still keeps a useful two-word conjunction.
      ctx =
        or_query_ctx(%{"name" => "bronchial asthma (disorder (chronic))", "original" => ""})

      assert Value.render({:or_query, "name", "original", %{variants: [:phrase, :terms]}}, ctx)
             |> Segment.to_text() ==
               ~s|"bronchial asthma (disorder (chronic))" OR bronchial asthma|
    end

    test ":phrase alone matches the 2-tuple exactly" do
      ctx =
        or_query_ctx(%{"name" => "desflurane", "original" => "DESFLURANE|Desflurane USAN"})

      assert Value.render(
               {:or_query, "name", "original", %{variants: [:phrase]}},
               ctx
             )
             |> Segment.to_text() == ~s("desflurane" OR "Desflurane USAN")
    end
  end

  # Pulls the label out of the first link segment, so a test can say what the link itself
  # prints rather than trusting the flattened sentence.
  defp link_label(segments) do
    segments
    |> Segment.flatten()
    |> Enum.find_value(fn
      {:link, _href, label} -> label
      _ -> nil
    end)
  end
end

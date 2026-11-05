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

      assert "label" in Config.referenced_names(config)
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
end

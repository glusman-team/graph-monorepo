defmodule EdgeLinkouts.DisplayTest do
  @moduledoc """
  Renders real stored documents through the drug approvals config.

  These assertions use the committed contract fixtures rather than invented maps, so they check
  the config against field names that actually occur in DAKP data. A config that validates but
  renders nothing is the failure mode this file exists to catch.
  """
  use ExUnit.Case, async: true

  alias EdgeLinkouts.Codec
  alias EdgeLinkouts.Display
  alias EdgeLinkouts.Display.{Config, Segment, Value}

  @fixtures Path.expand("../../../../dagster/projects/linkouts/contracts/docs.ndjson", __DIR__)
  @drift Path.expand("../../../../dagster/projects/linkouts/contracts/drift.ndjson", __DIR__)
  @v1 "infores:drugapprovals-kp-1.11.2"
  @v2 "infores:drugapprovals-kp-1.16.0"

  setup_all do
    docs =
      @fixtures
      |> File.stream!()
      |> Stream.map(&String.trim/1)
      |> Stream.reject(&(&1 == ""))
      |> Enum.map(&JSON.decode!/1)
      # The fixture file also holds the reserved pool documents behind /random; they are not
      # edge blobs and must not be decoded as ones.
      |> Enum.reject(&String.starts_with?(&1["id"], "__random_pool__"))

    resolved = resolve_all(@fixtures)

    legacy =
      for doc <- docs,
          {:ok, blob} = Codec.decode(doc["b"]),
          version <- Codec.versions(blob),
          {:ok, edge} = Codec.resolve(blob, version) do
        %{id: doc["id"], version: version, doc: edge}
      end

    _ = legacy
    {:ok, resolved: resolved, config: Display.get("infores:drugapprovals-kp")}
  end

  # Every stored document in an NDJSON fixture, resolved at every version it holds.
  defp resolve_all(path) do
    path
    |> File.stream!()
    |> Stream.map(&String.trim/1)
    |> Stream.reject(&(&1 == ""))
    |> Enum.flat_map(fn line ->
      with %{"b" => b64} <- JSON.decode!(line),
           {:ok, blob} <- Codec.decode(b64) do
        for version <- Codec.versions(blob),
            {:ok, doc} = Codec.resolve(blob, version) do
          %{id: b64 && doc["id"], version: version, doc: doc}
        end
      else
        _ -> []
      end
    end)
  end

  # The provenance block renders as one item per claim, so a test that reads it as prose has to
  # say how the items join. A space is what the page's line breaks do visually; asserting on the
  # joined text keeps sentence-level expectations honest without hiding the item boundaries.
  defp items_text(items), do: Enum.map_join(items, " ", &Segment.to_text/1)

  defp evidence_text(config, doc, version),
    do: config |> Display.evidence(doc, version) |> items_text()

  describe "provenance as a list of claims" do
    # Issue #63: the block was one run-on paragraph, so a reader had to re-segment it to find the
    # fact they wanted. These tests hold the item contract the page renders: one entry per
    # distinct claim, every item a closed sentence, and nothing that renders empty gets a bullet.

    test "each item is one closed sentence, and none double its period", %{config: config} do
      doc = %{
        "subject" => "CHEBI:5118",
        "subject_name" => "fluoxetine",
        "original_subject" => "FLUOXETINE",
        "subject_category" => ["biolink:SmallMolecule"],
        "object" => "MONDO:0005258",
        "object_name" => "autism spectrum disorder",
        "original_object" => "Autism spectrum disorder",
        "object_category" => ["biolink:Disease"],
        "predicate" => "biolink:applied_to_treat",
        "clinical_approval_status" => "off_label_use",
        "species_context_qualifier" => "NCBITaxon:9606",
        "supporting_text" => ["species_context_qualifier_name: Homo sapiens (human)"],
        "number_of_cases" => 61,
        "sources" => [%{"resource_id" => "infores:faers"}]
      }

      items = Display.evidence(config, doc, @v1)
      # The fixture is fully specified, so the count is pinned: a claim that stopped rendering
      # has to fail here rather than quietly shorten the list.
      assert length(items) == 3

      for item <- items do
        text = Segment.to_text(item)

        assert text != "", "an empty item would render as a bullet with nothing in it"
        assert String.match?(text, ~r/[.!?]\z/), "item does not close as a sentence: #{text}"
        refute text =~ ~r/\.\./, "item doubles its period: #{text}"
      end

      # One claim per item: the species sentence is not glued onto the case count by a joiner.
      # The refute only means something once both bullets are known to exist, so they are
      # asserted before the glued form is ruled out.
      species =
        Enum.find(items, &(Segment.to_text(&1) |> String.starts_with?("This assertion is about")))

      assert species, "no species bullet rendered: #{items_text(items)}"

      faers = Enum.find(items, &(Segment.to_text(&1) |> String.starts_with?("FAERS and Canada")))

      assert faers, "no case-count bullet rendered: #{items_text(items)}"

      refute species |> Segment.to_text() |> String.contains?("FAERS")

      refute Enum.any?(items, fn item ->
               text = Segment.to_text(item)
               String.contains?(text, ". FAERS")
             end)
    end

    test "an entry whose clause drops leaves no empty item", %{config: config} do
      # The species clause falls back to an empty render when the dump names no species phrase.
      # That must remove the bullet, not print a bare "." one.
      doc = %{
        "subject" => "CHEBI:5118",
        "subject_name" => "fluoxetine",
        "object" => "MONDO:0005258",
        "object_name" => "autism spectrum disorder",
        "predicate" => "biolink:treats",
        "clinical_approval_status" => "approved_for_condition",
        "species_context_qualifier" => "NCBITaxon:9606"
      }

      items = Display.evidence(config, doc, @v1)

      refute Enum.any?(
               items,
               &(&1 |> Segment.to_text() |> String.starts_with?("This assertion is about"))
             )

      refute Enum.any?(items, fn item ->
               text = item |> Segment.to_text() |> String.trim()
               text == "." or text == ""
             end)
    end

    test "the page emits one <li> per claim", %{config: config} do
      # Covered from the renderer side here; the DOM assertion lives in
      # EdgeLinkoutsWeb.EdgeLiveTest, where a real stored document goes through the template.
      doc = %{
        "subject" => "CHEBI:5118",
        "subject_name" => "fluoxetine",
        "original_subject" => "FLUOXETINE",
        "subject_category" => ["biolink:SmallMolecule"],
        "object" => "MONDO:0005258",
        "object_name" => "autism spectrum disorder",
        "original_object" => "Autism spectrum disorder",
        "object_category" => ["biolink:Disease"],
        "predicate" => "biolink:applied_to_treat",
        "clinical_approval_status" => "off_label_use",
        "species_context_qualifier" => "NCBITaxon:9606",
        "supporting_text" => ["species_context_qualifier_name: Homo sapiens (human)"],
        "number_of_cases" => 61,
        "sources" => [%{"resource_id" => "infores:faers"}]
      }

      items = Display.evidence(config, doc, @v1)

      # One claim per item means the template can put each on its own line: an item is a segment
      # list, not a fragment of one, and no item spans two sentences.
      assert Enum.all?(items, &is_list/1)
      refute Enum.any?(items, &(&1 |> Segment.to_text() |> String.contains?(". The original")))
    end
  end

  test "species phrases flow without a comma and link to NCBI Taxonomy", %{config: config} do
    for {taxon, name, label} <- [
          {"9606", "Homo sapiens (human)", "humans"},
          {"9796", "Equus caballus (horse)", "horses"}
        ] do
      doc = %{
        "subject_name" => "Azacitidine",
        "subject" => "CHEBI:2038",
        "object_name" => "refractory malignant neoplasm",
        "object" => "MONDO:0004992",
        "predicate" => "biolink:applied_to_treat",
        "clinical_approval_status" => "off_label_use",
        "species_context_qualifier" => "NCBITaxon:" <> taxon,
        "supporting_text" => ["species_context_qualifier_name: " <> name]
      }

      segments = Display.edge(config, doc, "infores:drugapprovals-kp-1.25.1")
      text = Segment.to_text(segments)
      assert text =~ "refractory malignant neoplasm in #{label}."
      refute text =~ ", in #{label}"

      assert {:link, "https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=" <> taxon,
              label} in segments
    end
  end

  test "the ICD-10-CM supporting source is named, not printed as a CURIE", %{config: config} do
    doc = %{
      "subject_name" => "Mesalamine",
      "subject" => "CHEBI:6775",
      "object_name" => "ulcerative colitis",
      "object" => "MONDO:0005101",
      "predicate" => "biolink:applied_to_treat",
      "sources" => [
        %{
          "resource_id" => "infores:drugapprovals-kp",
          "resource_role" => "primary_knowledge_source"
        },
        %{"resource_id" => "infores:icd10cm", "resource_role" => "supporting_data_source"}
      ]
    }

    segments = Display.methods(config, doc, "infores:drugapprovals-kp-1.25.1")
    text = Segment.to_text(segments)
    assert text =~ "the ICD-10-CM Table of Drugs and Chemicals"
    refute text =~ "infores:icd10cm"

    assert {:link, "https://biolink.github.io/information-resource-registry/resources/icd10cm/",
            "the ICD-10-CM Table of Drugs and Chemicals"} in Segment.flatten(segments)
  end

  test "Multiomics KP node prefixes all resolve to linkouts" do
    for {curie, url} <- [
          {"DOID:13209", "https://disease-ontology.org/term/DOID:13209"},
          {"CL:0000841",
           "https://www.ebi.ac.uk/ols4/ontologies/cl/classes?short_form=CL_0000841"},
          {"WormBase:WBGene00003491",
           "https://wormbase.org/species/c_elegans/gene/WBGene00003491"},
          {"SGD:S000029674", "https://www.yeastgenome.org/locus/S000029674"},
          {"PANTHER.PATHWAY:P00013",
           "http://www.pantherdb.org/pathway/pathwayDiagram.jsp?catAccession=P00013"},
          {"HGNC.FAMILY:679", "https://www.genenames.org/data/genegroup/#!/group/679"},
          {"ComplexPortal:CPX-6016", "https://www.ebi.ac.uk/complexportal/complex/CPX-6016"},
          {"DrugCentral:5160", "https://drugcentral.org/drugcard/5160"},
          {"RXCUI:1153923",
           "https://mor.nlm.nih.gov/RxNav/search?searchBy=RXCUI&searchTerm=1153923"},
          {"REACT:R-HSA-191273", "https://reactome.org/content/detail/R-HSA-191273"}
        ] do
      assert EdgeLinkouts.Display.Prefixes.url(curie) == url
    end
  end

  describe "config loading" do
    test "the drug approvals KG has a config" do
      config = Display.get("infores:drugapprovals-kp")
      assert config
      assert config.display_name == "DrugApprovals KP"
      assert "infores:drugapprovals-kp" in Display.known()
    end

    test "name_of splits a version key at the first numeric segment" do
      # Names contain hyphens, so splitting on the last one would give "infores:drugapprovals-kp-1.16"
      # and on the first would give "infores:drugapprovals". The colon in a canonical name is
      # just another character here: the split is at the hyphen before the numeric segment.
      assert Display.name_of("infores:drugapprovals-kp-1.16.0") == "infores:drugapprovals-kp"
      assert Display.name_of("multiomics-kg-2.13.1") == "multiomics-kg"
      assert Display.name_of("no-version-here") == nil
      assert Display.for_key("infores:drugapprovals-kp-1.16.0").name == "infores:drugapprovals-kp"
    end

    test "a version key stored without the infores prefix gets the same config" do
      # The live store was loaded as "drugapprovals-kp-1.23.4"; an exact-name lookup fell back
      # to the generic default and the edge page lost its sentence, evidence, about section and
      # known-issue notice.
      for key <- ["drugapprovals-kp-1.23.4", "infores:drugapprovals-kp-1.23.4"] do
        config = Display.for_key(key)
        assert config.name == "infores:drugapprovals-kp", key
        assert config.display_name == "DrugApprovals KP"
      end

      assert Display.get("drugapprovals-kp") == Display.get("infores:drugapprovals-kp")

      assert Display.known_error(
               Display.for_key("drugapprovals-kp-1.23.3"),
               "drugapprovals-kp-1.23.3"
             )

      assert Display.get("no-such-kp") == nil
      assert Display.for_key("no-such-kp-1.0.0").name == "default"
    end

    test "a slug is the name without its infores prefix, and expands back to it" do
      # The slug is what a document stores and what a URL carries; the canonical name is what
      # the config table is keyed by. Both directions have to agree or a link on the bar points
      # at a graph the display layer cannot find.
      assert Display.slug("infores:drugapprovals-kp") == "drugapprovals-kp"
      assert Display.slug("drugapprovals-kp") == "drugapprovals-kp"
      assert Display.slug(nil) == nil

      assert Display.kg_from_slug("drugapprovals-kp") == "infores:drugapprovals-kp"
      # Accepting the full form too means a URL that carries it is not a 404.
      assert Display.kg_from_slug("infores:drugapprovals-kp") == "infores:drugapprovals-kp"
      assert Display.kg_from_slug(nil) == nil
    end

    test "a slug with no config expands to the infores form, and keeps a foreign scheme" do
      # A stored graph with no display config still renders (the generic view), so its name has
      # to be derivable from the slug alone.
      assert Display.kg_from_slug("some-future-kp") == "infores:some-future-kp"
      # A name that already carries a scheme is not infores-registered; prefixing it would
      # invent an identifier.
      assert Display.kg_from_slug("other:thing") == "other:thing"
      assert Display.slug("other:thing") == "other:thing"
    end

    test "every referenced name is a slot or a real field" do
      # A typo'd slot renders as a silently shorter sentence, which is indistinguishable from
      # missing data. mix linkouts.check cross-checks against the fixtures; assert it here too so
      # `mix test` alone catches it.
      config = Display.get("infores:drugapprovals-kp")
      assert Display.unresolved_names(config) -- observed_fields() == []
    end
  end

  describe "the edge sentence" do
    test "a contraindication edge reads as a contraindication", %{
      resolved: resolved,
      config: config
    } do
      contraindicated =
        Enum.find(
          resolved,
          &(&1.doc["predicate"] == "biolink:contraindicated_in" and &1.version == @v1)
        )

      assert contraindicated, "no contraindication edge in the fixtures"

      text =
        config |> Display.edge(contraindicated.doc, contraindicated.version) |> Segment.to_text()

      # The legacy Perl matched /^biolink:contraindicated/ rather than listing predicates, so any
      # future contraindication predicate in the family renders correctly too.
      assert text =~ "is contraindicated for patients with"
      assert text =~ contraindicated.doc["subject_name"]
      assert text =~ contraindicated.doc["object_name"]

      # The sentence is the claim alone: the "DrugApprovals KP asserts:" lead-in lives in a
      # separate spec rendered above it, so the source name no longer opens the sentence.
      refute String.starts_with?(text, "DrugApprovals KP")
      assert String.starts_with?(text, contraindicated.doc["subject_name"])
      assert String.ends_with?(text, ".")
    end

    test "the lead-in names the source as a link to its documentation, the sentence is the claim alone",
         %{
           resolved: resolved,
           config: config
         } do
      edge = Enum.find(resolved, &(&1.version == @v1))
      lead = Display.asserts(config, edge.doc, edge.version)

      links = for {:link, href, label} <- lead, do: {label, href}

      assert {"DrugApprovals KP",
              "https://github.com/NCATSTranslator/Translator-All/wiki/Multiomics-Drug-Approvals-KP"} in links

      # The lead-in is the source name and the verb — the page adds the colon and the line
      # break; the sentence itself no longer carries the prefix.
      assert Segment.to_text(lead) == "DrugApprovals KP asserts"

      sentence_text =
        config |> Display.edge(edge.doc, edge.version) |> Segment.to_text()

      assert String.starts_with?(sentence_text, edge.doc["subject_name"])
      refute sentence_text =~ "DrugApprovals KP asserts"
    end

    test "the subject and object become links to their identifiers", %{
      resolved: resolved,
      config: config
    } do
      edge = Enum.find(resolved, &(&1.version == @v1))
      segments = Display.edge(config, edge.doc, edge.version)

      links = for {:link, href, label} <- segments, do: {label, href}

      assert {edge.doc["subject_name"], href_for(edge.doc["subject"])} in links
      assert {edge.doc["object_name"], href_for(edge.doc["object"])} in links
    end

    test "no segment contains markup, so upstream data cannot inject HTML", %{
      resolved: resolved,
      config: config
    } do
      # The legacy code interpolated KG values straight into HTML strings. Segments are data and
      # the template escapes them; this asserts the display layer never produces markup itself.
      hostile = Map.put(hd(resolved).doc, "subject_name", "<script>alert(1)</script>")

      for segment <- Display.edge(config, hostile, @v1) do
        case segment do
          {:text, text} -> refute text =~ "<a href"
          {:link, href, _label} -> assert String.starts_with?(href, "https://")
        end
      end

      assert Segment.to_text(Display.edge(config, hostile, @v1)) =~ "<script>alert(1)</script>"
    end

    test "a missing node name still renders the identifier", %{resolved: resolved, config: config} do
      edge = Enum.find(resolved, &(&1.version == @v1))
      nameless = Map.delete(edge.doc, "subject_name")

      text = config |> Display.edge(nameless, @v1) |> Segment.to_text()

      # Falling back to the CURIE beats the legacy "[[missing name for X]]" and beats dropping the
      # subject entirely, which would make the sentence claim something about nothing.
      assert text =~ edge.doc["subject"]
      refute text =~ "missing name"
    end
  end

  describe "active ingredient composition" do
    # Fields and the first four labels from DAKP 1.27.0 edge
    # 66d8e992-d902-374f-92fc-ebc2250cfa84, joined to its released nodes.
    defp composition_doc do
      %{
        "id" => "66d8e992-d902-374f-92fc-ebc2250cfa84",
        "subject_name" => "Normal saline",
        "subject" => "UMLS:C0445115",
        "original_subject" =>
          "0.9% SODIUM CHLORIDE|ISOTONIC SODIUM CHLORIDE|SODIUM CHLORIDE 0.9%",
        "object_name" => "Chloride ion",
        "object" => "CHEBI:17996",
        "original_object" => "CHLORIDE ION",
        "predicate" => "biolink:has_active_ingredient",
        "category" => ["biolink:ChemicalEntityToChemicalEntityAssociation"],
        "knowledge_level" => "knowledge_assertion",
        "agent_type" => "manual_validation_of_automated_agent",
        "publications" => [
          "dailymed:2be5b3e2-c4a4-00ea-e063-6394a90a6163",
          "dailymed:33648718-2fbc-6840-e063-6294a90a5065",
          "dailymed:7860dfb7-a67c-7b5f-e053-2a91aa0ad59f",
          "dailymed:78825011-268c-784b-e053-2991aa0ae0d4"
        ],
        "sources" => [
          %{"resource_id" => "infores:dailymed", "resource_role" => "supporting_data_source"},
          %{
            "resource_id" => "infores:drugapprovals-kp",
            "resource_role" => "primary_knowledge_source"
          },
          %{
            "resource_id" => "infores:drugs-at-fda",
            "resource_role" => "supporting_data_source"
          }
        ],
        "supporting_text" => ["object_nlp_level: 1", "subject_nlp_level: 1"]
      }
    end

    test "the claim, title, methods, and verification action stay specific to composition", %{
      config: config
    } do
      doc = composition_doc()
      version = "infores:drugapprovals-kp-1.27.0"

      assert Display.title(config, doc, version) ==
               "Normal saline active ingredient: Chloride ion"

      assert config |> Display.edge(doc, version) |> Segment.to_text() ==
               "Normal saline lists Chloride ion as an active ingredient."

      assert config |> Display.methods(doc, version) |> Segment.to_text() ==
               "This composition was machine-generated by DrugApprovals KP from DailyMed and DrugsFDA, with dataset-level manual quality control."

      generated_by =
        Value.render(config.slots["generated_by"], Display.context(config, doc, version))

      assert Segment.to_text(generated_by) ==
               "This edge was generated by DrugApprovals KP, which integrated DailyMed and DrugsFDA in this assertion."

      footnote = Display.footnote(config, doc, version)

      assert Segment.to_text(footnote) ==
               "Verify this composition in DailyMed labels mentioning both Normal saline and Chloride ion."

      search_links =
        footnote
        |> Segment.flatten()
        |> Enum.filter(&(match?({:link, _, _}, &1) and elem(&1, 1) =~ "search.cfm"))

      assert length(search_links) == 1
    end

    test "a long label list renders only three links and a total, not thousands of hidden links",
         %{config: config} do
      # DAKP 1.27.0's Hand Sanitizers -> alcohol edge carries 3,231 labels. A fold still
      # renders every hidden link; a preview keeps the initial document bounded.
      doc = %{
        composition_doc()
        | "publications" => Enum.map(1..3231, &"dailymed:set-#{&1}")
      }

      evidence = Display.evidence(config, doc, "infores:drugapprovals-kp-1.27.0")
      text = items_text(evidence)

      assert text =~ "3231 product labels"
      assert text =~ "set-1, set-2, set-3"
      assert text =~ "KGX download preserves the complete list"
      refute text =~ "set-4"
      refute text =~ "original subject"
      refute Enum.any?(Segment.flatten(evidence), &match?({:more, _}, &1))

      links = for {:link, _, _} = link <- Segment.flatten(evidence), do: link
      assert length(links) == 3
    end

    test "a short label list renders completely without a truncation notice", %{config: config} do
      doc = %{
        composition_doc()
        | "publications" => Enum.take(composition_doc()["publications"], 2)
      }

      evidence = Display.evidence(config, doc, "infores:drugapprovals-kp-1.27.0")
      text = items_text(evidence)

      assert text =~ "DailyMed documents this composition in"
      assert text =~ "2be5b3e2-c4a4-00ea-e063-6394a90a6163"
      assert text =~ "33648718-2fbc-6840-e063-6294a90a5065"
      refute text =~ "product labels"
      refute text =~ "KGX download"
      assert length(for {:link, _, _} <- Segment.flatten(evidence), do: true) == 2
    end

    test "exactly three labels stays on the complete-list branch", %{config: config} do
      doc = %{
        composition_doc()
        | "publications" => Enum.take(composition_doc()["publications"], 3)
      }

      evidence = Display.evidence(config, doc, "infores:drugapprovals-kp-1.27.0")
      text = items_text(evidence)

      assert text =~ "7860dfb7-a67c-7b5f-e053-2a91aa0ad59f"
      refute text =~ "product labels"
      refute text =~ "KGX download"
      assert length(for {:link, _, _} <- Segment.flatten(evidence), do: true) == 3
    end

    test "no labels omits Provenance but retains the verification action", %{config: config} do
      doc = Map.delete(composition_doc(), "publications")
      version = "infores:drugapprovals-kp-1.27.0"

      assert Display.evidence(config, doc, version) == []
      assert Display.footnote(config, doc, version) != []
    end
  end

  describe "version-scoped aliases" do
    test "the approvals row renders under either spelling of the field" do
      # The rename is real but is not visible inside one blob: docs.ndjson holds 1.11.2 and 1.16.0
      # both spelled FDA_regulatory_approvals, while drift.ndjson holds 1.16.0 spelled
      # regulatory_approvals. So the alias is proven across two fixture files rather than two
      # versions of one edge — which is also how the drift actually reaches production: a new dump
      # arrives with the new spelling while stored documents keep the old one.
      config = Display.get("infores:drugapprovals-kp")

      for {path, expected_key} <- [
            {@fixtures, "FDA_regulatory_approvals"},
            {@drift, "regulatory_approvals"}
          ] do
        seen =
          for %{version: version, doc: doc} <- resolve_all(path),
              Map.has_key?(doc, expected_key) do
            evidence = config |> Display.evidence(doc, version) |> Segment.flatten()
            hrefs = for {:link, href, _label} <- evidence, do: href

            assert Segment.to_text(evidence) =~ "documented under",
                   "#{Path.basename(path)} #{version} has #{expected_key} but rendered no approvals sentence"

            assert Enum.any?(hrefs, &String.starts_with?(&1, "https://fda.report/applications/")),
                   "#{Path.basename(path)} #{version} rendered no FDA application link"

            version
          end

        assert seen != [], "no document in #{Path.basename(path)} carries #{expected_key}"
      end
    end

    test "a document with neither spelling renders no approvals sentence", %{
      resolved: resolved,
      config: config
    } do
      # The corollary: the alias must not make the sentence appear out of nothing, because
      # a approvals sentence with nothing behind it reads as a claim that there is a label.
      edge =
        Enum.find(resolved, fn %{doc: doc} ->
          not Map.has_key?(doc, "FDA_regulatory_approvals") and
            not Map.has_key?(doc, "regulatory_approvals")
        end)

      assert edge, "expected a fixture edge with no approvals field at all"

      refute evidence_text(config, edge.doc, edge.version) =~
               "documented under"
    end

    test "an unscoped alias applies at every version, canonical name first", %{config: config} do
      # The rename is not version-gated in the real data, so the alias must not be either. Order
      # matters: a document carrying both spellings resolves to the canonical one.
      for version <- [@v1, @v2] do
        assert Display.Config.aliases_for(config, version) == %{
                 "regulatory_approvals" => ["regulatory_approvals", "FDA_regulatory_approvals"]
               }
      end

      both = %{
        "regulatory_approvals" => ["new"],
        "FDA_regulatory_approvals" => ["old"]
      }

      assert config |> Display.context(both, @v1) |> Value.fetch("regulatory_approvals") == [
               "new"
             ]

      legacy = %{"FDA_regulatory_approvals" => ["old"]}

      assert config |> Display.context(legacy, @v1) |> Value.fetch("regulatory_approvals") == [
               "old"
             ]
    end

    test "a version-scoped alias only applies inside its range" do
      # Scoped aliases still work for KGs whose renames really are version boundaries, and are
      # tried before unscoped ones because they say more about that release.
      {:ok, config} =
        Config.new(%{
          name: "scoped-kg",
          display_name: "Scoped",
          edge: "{thing}",
          aliases: [
            %{field: "thing", as: "old_thing", versions: "<2.0.0"},
            %{field: "thing", as: "other_thing"}
          ]
        })

      assert Config.aliases_for(config, "scoped-kg-1.5.0") == %{
               "thing" => ["thing", "old_thing", "other_thing"]
             }

      assert Config.aliases_for(config, "scoped-kg-2.1.0") == %{
               "thing" => ["thing", "other_thing"]
             }
    end
  end

  describe "evidence" do
    test "sentences with no data are omitted rather than rendered empty", %{
      resolved: resolved,
      config: config
    } do
      edge =
        Enum.find(resolved, &(&1.version == @v1 and not Map.has_key?(&1.doc, "number_of_cases")))

      assert edge, "expected a fixture edge without a FAERS case count"

      text = evidence_text(config, edge.doc, edge.version)

      # "holds  cases reporting this usage" with a hole in it would misstate the source.
      refute text =~ "FAERS"
      # The search sentence moved to the footnote paragraph, so the evidence paragraph no
      # longer carries it.
      refute text =~ "Try searching DailyMed"

      footnote = config |> Display.footnote(edge.doc, edge.version) |> Segment.to_text()
      assert footnote =~ "Try searching DailyMed"
    end

    test "the paragraph is prose: no storage key with underscores leaks into it", %{
      resolved: resolved,
      config: config
    } do
      # Values like agent_type are humanized into words before they reach the paragraph; a
      # reader should never meet "manual_validation_of_automated_agent" in a sentence.
      for %{version: version, doc: doc} <- resolved, version == @v1 do
        refute evidence_text(config, doc, version) =~ "_"
      end
    end

    test "DailyMed publications become product-label links", %{resolved: resolved, config: config} do
      edge = Enum.find(resolved, &(&1.version == @v1 and is_list(&1.doc["publications"])))
      assert edge

      evidence = Display.evidence(config, edge.doc, edge.version) |> Segment.flatten()
      assert Segment.to_text(evidence) =~ "documented on the product labels"

      links = for {:link, href, label} <- evidence, do: {label, href}

      for publication <- edge.doc["publications"] do
        "dailymed:" <> setid = publication

        # The exact set id, shown and linked, not a labelled "1" the reader must count.
        assert {setid, "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=#{setid}"} in links
      end
    end

    test "an edge with no approvals or product labels omits both sentences", %{
      resolved: resolved,
      config: config
    } do
      # The legacy page printed its product-labels line even when empty ("None."); here a
      # missing fact is simply absent, never announced.
      edge =
        Enum.find(resolved, fn %{doc: doc} ->
          not Map.has_key?(doc, "FDA_regulatory_approvals") and
            not Map.has_key?(doc, "regulatory_approvals") and
            not Map.has_key?(doc, "publications")
        end)

      assert edge, "expected a fixture edge with neither approvals nor product labels"

      text = evidence_text(config, edge.doc, edge.version)
      refute text =~ "No FDA application numbers are recorded"
      refute text =~ "No structured-product-label set identifiers are recorded"
    end

    test "FDA application numbers link to the label browser", %{
      resolved: resolved,
      config: config
    } do
      edge =
        Enum.find(
          resolved,
          &(&1.version == @v1 and Map.has_key?(&1.doc, "FDA_regulatory_approvals"))
        )

      assert edge

      evidence = Display.evidence(config, edge.doc, edge.version) |> Segment.flatten()
      assert Segment.to_text(evidence) =~ "documented under"

      hrefs =
        for {:link, href, _label} <- evidence,
            String.starts_with?(href, "https://fda.report/applications/") do
          href
        end

      assert hrefs != []
    end

    test "provenance maps both originals with Oxford commas and omits missing facts", %{
      config: config
    } do
      doc = %{
        "original_subject" => "Zyrtec|Cetirizine|Allergy relief",
        "subject" => "CHEBI:3561",
        "subject_category" => ["biolink:ChemicalEntity"],
        "original_object" => "Itching|Pruritus",
        "object" => "HP:0000989",
        "object_category" => ["biolink:PhenotypicFeature"]
      }

      text = evidence_text(config, doc, @v1)

      assert text =~
               "The original subject Zyrtec, Cetirizine, and Allergy relief mapped to CHEBI:3561, which is a ChemicalEntity; and the original object Itching and Pruritus mapped to HP:0000989, which is a PhenotypicFeature."

      assert text =~
               "the original object Itching and Pruritus mapped to HP:0000989, which is a PhenotypicFeature."

      for missing <- [nil, "", " | ", []] do
        partial = Map.put(doc, "original_subject", missing) |> Map.delete("object_category")
        text = evidence_text(config, partial, @v1)
        refute text =~ "original subject"
        assert text =~ "The original object Itching and Pruritus mapped to HP:0000989."
        refute text =~ "which is a"
      end
    end

    test "qualifier originals form a separate compound sentence", %{config: config} do
      doc = %{
        "population_context_qualifier" => "NCIT:C27954",
        "sex_qualifier" => "PATO:0000384",
        "supporting_text" => [
          "original_population_context_qualifier: adults|patients|people",
          "original_sex_qualifier: male|men"
        ]
      }

      text = evidence_text(config, doc, @v1)

      assert text =~
               "The original population context qualifier adults, patients, and people mapped to NCIT:C27954; and the original sex qualifier male and men mapped to PATO:0000384."

      refute text =~ "original subject"
      refute text =~ "original object"
    end

    test "the species sentence closes with exactly one period", %{config: config} do
      # Issue #58, on the page a reviewer actually read: the species clause carries its own "."
      # suffix and the compound closer appended a second one unconditionally, so Provenance
      # printed "This assertion is about Homo sapiens (human).. FAERS and Canada Vigilance
      # record 61 cases of this usage." Sentence punctuation belongs to the closer, so the
      # period has to be idempotent for a value that already ends in one.
      doc = %{
        "subject" => "CHEBI:5118",
        "subject_name" => "fluoxetine",
        "original_subject" => "FLUOXETINE",
        "subject_category" => ["biolink:SmallMolecule"],
        "object" => "MONDO:0005258",
        "object_name" => "autism spectrum disorder",
        "original_object" => "Autism spectrum disorder",
        "object_category" => ["biolink:Disease"],
        "predicate" => "biolink:applied_to_treat",
        "clinical_approval_status" => "off_label_use",
        "species_context_qualifier" => "NCBITaxon:9606",
        "supporting_text" => ["species_context_qualifier_name: Homo sapiens (human)"],
        "number_of_cases" => 61
      }

      text = evidence_text(config, doc, @v1)

      assert text =~ "This assertion is about Homo sapiens (human). FAERS"
      refute text =~ "(human).."
      refute text =~ ~r/\.\./

      # The same clause inside a multi-clause compound must hand its mark to the "; and" join
      # rather than gluing a period onto the connector.
      two_clauses =
        doc
        |> Map.put("sex_qualifier", "PATO:0000384")
        |> Map.update!("supporting_text", &["original_sex_qualifier: male" | &1])

      joined = evidence_text(config, two_clauses, @v1)

      assert joined =~ "This assertion is about Homo sapiens (human); and"
      refute joined =~ ".; and"
      refute joined =~ ~r/\.\./
    end

    test "methods name the integrator, then its supporting sources", %{
      resolved: resolved,
      config: config
    } do
      edge = Enum.find(resolved, &(&1.version == @v1))

      text = config |> Display.methods(edge.doc, edge.version) |> Segment.to_text()

      # The primary source is the generator, so the closing sentence names it with its old
      # emphasis and registry link, then lists only the supporting sources (the primary
      # renders empty and the list drops it). Each source is named in words from the RIG's
      # supporting_data_source_info; a single supporting source takes no conjunction.
      assert text =~
               "This edge was generated by DrugApprovals KP, which integrated DailyMed in this assertion."

      refute text =~ "(primary)"
      refute text =~ "(supporting)"

      # The generator carries its old emphasis and linkout: bold, linking to its registry page.
      assert [{:strong, strong_inner}] =
               Enum.filter(
                 Display.methods(config, edge.doc, edge.version),
                 &match?({:strong, _}, &1)
               )

      assert [{:link, href, "DrugApprovals KP"}] = strong_inner

      assert href ==
               "https://biolink.github.io/information-resource-registry/resources/drugapprovals-kp/"
    end

    test "search links percent-encode the drug names they embed", %{
      resolved: resolved,
      config: config
    } do
      edge = Enum.find(resolved, &(&1.version == @v1))
      footnote = Display.footnote(config, edge.doc, edge.version) |> Segment.flatten()

      links =
        for {:link, href, _} <- footnote,
            href =~ "dailymed.nlm.nih.gov/dailymed/search.cfm" do
          href
        end

      assert length(links) == 3

      # A name with a space or an ampersand must not break the query or add a parameter.
      for href <- links do
        refute href =~ " "
      end
    end

    test "search queries OR the preferred name with each pipe-delimited original", %{
      resolved: resolved,
      config: config
    } do
      edge = Enum.find(resolved, &(&1.version == @v1))

      doc =
        Map.merge(edge.doc, %{
          "subject_name" => "desflurane",
          "original_subject" => "DESFLURANE|Desflurane USAN",
          "object_name" => "asthma",
          "original_object" => "asthma|Asthma (disorder)|ASTHMA"
        })

      footnote = Display.footnote(config, doc, edge.version) |> Segment.flatten()

      links =
        for {:link, href, _} <- footnote,
            href =~ "dailymed.nlm.nih.gov/dailymed/search.cfm" do
          href
        end

      assert [subject_href, object_href, both_href] = links

      # Every name the source used is a quoted phrase in one OR group; case-insensitive
      # duplicates collapse to the first spelling, and each multiword variant also emits its
      # bare word-conjunction form (documented DailyMed AND semantics), because the stored
      # phrase can be longer than any label wording. The link text keeps the preferred name,
      # so only the underlying query changes. adv=1 is what switches DailyMed from
      # drug-name search to full label-text search; without it a disease name matches nothing.
      assert subject_href =~
               "query=%22desflurane%22+OR+%22Desflurane+USAN%22+OR+Desflurane+USAN"

      assert object_href =~
               "query=%22asthma%22+OR+%22Asthma+%28disorder%29%22"

      assert both_href =~
               "query=(%22desflurane%22+OR+%22Desflurane+USAN%22+OR+Desflurane+USAN)+AND+(%22asthma%22+OR+%22Asthma+%28disorder%29%22)"

      # All three searches run in advanced (full-text) mode; the linkouts must never
      # regress to the default mode that searches drug names only.
      for href <- links do
        assert href =~ "search.cfm?adv=1&labeltype=all&query="
      end
    end

    test "the both-names search is an unrestricted full-text AND", %{
      resolved: resolved,
      config: config
    } do
      # Why unrestricted: DAKP mines contraindication evidence from six SPL sections, not
      # only Indications, and a section-scoped object clause returned 0 results for the
      # DESFLURANE/asthma edge (mention lives in Warnings) despite its supporting label.
      # Live evidence 2026-10-19: the old "34067-9:"-scoped query returned 0 for that edge;
      # the unrestricted AND returns 6. The query must stay free of section field codes.
      edge = Enum.find(resolved, &(&1.version == @v1))

      doc =
        Map.merge(edge.doc, %{
          "subject_name" => "desflurane",
          "original_subject" => "DESFLURANE",
          "object_name" => "asthma",
          "original_object" => "asthma"
        })

      footnote = Display.footnote(config, doc, edge.version) |> Segment.flatten()

      assert [{:link, both_href, _}] =
               footnote
               |> Enum.filter(fn
                 {:link, href, _} ->
                   href =~ "dailymed.nlm.nih.gov/dailymed/search.cfm" and href =~ "+AND+"

                 _ ->
                   false
               end)
               |> Enum.map(fn {:link, href, _} -> {:link, href, nil} end)

      refute both_href =~ "34067-9:"

      assert both_href =~
               "query=(%22desflurane%22)+AND+(%22asthma%22)"
    end
  end

  describe "version gates" do
    test "known_error matches by release and by full key, and misses untouched releases" do
      # The shipped config declares one issue (1.23.3's missing db.systemsbiology.net per-edge
      # linkouts); the matching logic is also exercised on a hand-built multi-version issue so
      # range requirements stay covered.
      # A pattern match both asserts the config exists and refines the type, so the struct
      # update below cannot warn about updating a possibly-nil value.
      assert %Display.Config{} = config = Display.get("infores:drugapprovals-kp")

      assert %{} = error = Display.known_error(config, "1.23.3")
      assert error.description =~ "db.systemsbiology.net"

      assert %{} = Display.known_error(config, "infores:drugapprovals-kp-1.23.3")
      assert Display.known_error(config, "1.16.0") == nil
      assert Display.known_error(config, "1.11.2") == nil
      assert Display.known_error(nil, "1.23.3") == nil

      flagged = %Display.Config{
        config
        | known_errors: [
            %{
              description:
                "Some applied-to-treat edges in this release incorrectly display regulatory approvals.",
              versions: ["1.11.2", "1.16.0"]
            }
          ]
      }

      assert %{} = error = Display.known_error(flagged, "1.16.0")
      assert error.description =~ "applied-to-treat edges"

      assert %{} = Display.known_error(flagged, "infores:drugapprovals-kp-1.11.2")
      assert Display.known_error(flagged, "1.17.0") == nil
      assert Display.known_error(flagged, "2.0.0") == nil
    end

    test "known_errors lists every matching issue, so an affected release shows all of them" do
      assert %Display.Config{} = config = Display.get("infores:drugapprovals-kp")

      # 1.23.3 carries both shipped defects; 1.23.4 and 1.23.5 carry only the qualifier
      # scoping problem.
      assert [%{description: first}, %{description: second}] =
               Display.known_errors(config, "1.23.3")

      assert first =~ "db.systemsbiology.net"
      assert second =~ "improperly scoped"

      assert [%{description: only}] = Display.known_errors(config, "1.23.5")
      assert only =~ "improperly scoped"

      assert Display.known_errors(nil, "1.23.3") == []
    end

    test "the disease-context clause renders whenever a displayable name exists" do
      # The gate is on the name field, not the version: a CURIE without a name would print
      # " in the context of " with nothing after it, so it is suppressed instead; a name at
      # any release renders.
      config = Display.get("infores:drugapprovals-kp")

      base = %{
        "subject" => "CHEBI:1",
        "subject_name" => "aspirin",
        "object" => "MONDO:1",
        "object_name" => "fever",
        "predicate" => "biolink:treats",
        "disease_context_qualifier" => "MONDO:2"
      }

      with_name =
        config
        |> Display.edge(
          Map.put(base, "disease_context_qualifier_name", "adult fever"),
          "infores:drugapprovals-kp-1.0.0"
        )
        |> Segment.to_text()

      without_name = config |> Display.edge(base, @v1) |> Segment.to_text()

      assert with_name =~ "in the context of adult fever"
      refute without_name =~ "in the context of"
    end

    test "for_key falls back to the default config, and named configs still win" do
      assert Display.for_key("infores:some-other-kp-1.0.0").name == "default"
      assert Display.for_key("infores:drugapprovals-kp-1.23.3").name == "infores:drugapprovals-kp"
      assert Display.default().name == "default"
      # The default is infrastructure: it is not a KG name a curator could configure.
      refute "default" in Display.known()
    end

    test "the default config renders the sources sentence for a standard KGX provenance shape" do
      config = Display.default()

      doc = %{
        "subject" => "X:1",
        "predicate" => "biolink:treats",
        "object" => "Y:2",
        "sources" => [
          %{"resource_id" => "infores:some-kp", "resource_role" => "primary_knowledge_source"},
          %{"resource_id" => "infores:pubmed", "resource_role" => "supporting_data_source"}
        ]
      }

      text = evidence_text(config, doc, "infores:some-kp-1.0.0")

      assert text =~ "This association is derived from"
    end

    test "the qualifier stack reads the original supporting-text phrases" do
      # DAKP logs each qualifier's readable source phrase into supporting_text as
      # "original_{qualifier}: value"; the claim prefers those over the CURIEs, at every
      # release — the gates are on the qualifiers' presence, not the version.
      config = Display.get("infores:drugapprovals-kp")

      doc = %{
        "subject" => "CHEBI:1",
        "subject_name" => "aspirin",
        "object" => "MONDO:1",
        "object_name" => "fever",
        "predicate" => "biolink:treats",
        "anatomical_context_qualifier" => "UBERON:1",
        "frequency_qualifier" => "UMLS:2",
        "population_context_qualifier" => "UMLS:3",
        "sex_qualifier" => "UMLS:C16573",
        "temporal_context_qualifier" => "UMLS:4",
        "supporting_text" => [
          "original_anatomical_context_qualifier: breast",
          "original_frequency_qualifier: dosage",
          "original_population_context_qualifier: pediatric patients",
          "original_sex_qualifier: adult|females",
          "original_temporal_context_qualifier: extended period of time"
        ]
      }

      rendered = config |> Display.edge(doc, @v1) |> Segment.to_text()
      old = config |> Display.edge(doc, "infores:drugapprovals-kp-1.0.0") |> Segment.to_text()

      # The frames read as one flowing clause, with rewordings repairing source fragments
      # (a lone "dosage", a singular "adult", a missing article).
      assert rendered =~
               ", in the breast, dosed at the labeled dosage, in pediatric patients, in adults, over an extended period of time."

      assert old == rendered

      # Pipe-separated alternates keep the first, canonical read.
      refute rendered =~ "females"
      refute rendered =~ "C16573"
    end

    test "the qualifier stack prefers concept names over the raw log lines" do
      # The RIG resolves context qualifiers to UMLS concepts and stores the reader's name in
      # {qualifier}_name; the claim shows that name, not the raw log line. A source error that
      # repeats one concept across two qualifiers still renders both — the display reports the
      # stored facts, it does not clean them up.
      config = Display.get("infores:drugapprovals-kp")

      doc = %{
        "subject" => "CHEBI:1",
        "subject_name" => "aspirin",
        "object" => "MONDO:1",
        "object_name" => "fever",
        "predicate" => "biolink:treats",
        "population_context_qualifier" => "UMLS:C1706450",
        "population_context_qualifier_name" => "Legal Adult",
        "sex_qualifier" => "UMLS:C1706450",
        "sex_qualifier_name" => "Legal Adult",
        "supporting_text" => [
          "original_population_context_qualifier: Adults",
          "original_sex_qualifier: adults"
        ]
      }

      rendered = config |> Display.edge(doc, @v1) |> Segment.to_text()

      assert rendered =~ ", in Legal Adult, in Legal Adult."
      refute rendered =~ ": Adults"
      refute rendered =~ "original_sex_qualifier"
    end

    test "a qualifier without a concept name still reads from the source log line" do
      config = Display.get("infores:drugapprovals-kp")

      doc = %{
        "subject" => "CHEBI:1",
        "subject_name" => "aspirin",
        "object" => "MONDO:1",
        "object_name" => "fever",
        "predicate" => "biolink:treats",
        "population_context_qualifier" => "UMLS:C1706450",
        "supporting_text" => ["original_population_context_qualifier: pediatric patients"]
      }

      rendered = config |> Display.edge(doc, @v1) |> Segment.to_text()

      assert rendered =~ ", in pediatric patients."
    end

    test "a qualifier without a supporting-text phrase falls back to the labelled CURIE" do
      config = Display.get("infores:drugapprovals-kp")

      doc = %{
        "subject" => "CHEBI:1",
        "subject_name" => "aspirin",
        "object" => "MONDO:1",
        "object_name" => "fever",
        "predicate" => "biolink:treats",
        "sex_qualifier" => "UMLS:C16573"
      }

      text = config |> Display.edge(doc, @v1) |> Segment.to_text()

      assert text =~ ", sex UMLS:C16573"
    end

    test "object_modifier switches treating to preventing" do
      config = Display.get("infores:drugapprovals-kp")

      base = %{
        "subject" => "CHEBI:1",
        "subject_name" => "aspirin",
        "object" => "MONDO:1",
        "object_name" => "fever",
        "predicate" => "biolink:treats"
      }

      plain = config |> Display.edge(base, @v1) |> Segment.to_text()

      prevention =
        config
        |> Display.edge(Map.put(base, "object_modifier", "prevention"), @v1)
        |> Segment.to_text()

      assert plain =~ "has been approved for treating fever"
      assert prevention =~ "has been approved for preventing fever"
    end
  end

  describe "segments" do
    test "adjacent text runs merge and nils drop" do
      assert Segment.join([Segment.text("a"), nil, Segment.text("b")]) == [{:text, "ab"}]
      assert Segment.join([]) == []
    end

    test "a non-http href degrades to text instead of becoming a link" do
      assert Segment.link("javascript:alert(1)", "click") == {:text, "click"}
      assert Segment.link("data:text/html,x", "click") == {:text, "click"}
      assert Segment.link(nil, "plain") == {:text, "plain"}
      assert Segment.link("https://x.test", "") == nil
    end

    test "to_text drops hrefs and keeps labels" do
      assert Segment.to_text([{:text, "see "}, {:link, "https://x.test", "this"}]) == "see this"
    end
  end

  # Field names present in the fixtures, used to prove a config references real data. Nested names
  # count too, because {:list, "sources", "{resource_id}", ", "} legitimately reaches into a KGX
  # sources entry.
  defp observed_fields do
    @fixtures
    |> stored_blobs()
    |> Enum.flat_map(&resolved_docs/1)
    |> Enum.flat_map(&field_names/1)
  end

  defp stored_blobs(path) do
    path
    |> File.stream!()
    |> Stream.map(&String.trim/1)
    |> Stream.reject(&(&1 == ""))
    |> Enum.map(&JSON.decode!/1)
    |> Enum.flat_map(fn
      %{"b" => b64} ->
        case Codec.decode(b64) do
          {:ok, blob} -> [blob]
          _ -> []
        end

      _ ->
        []
    end)
  end

  defp resolved_docs(blob) do
    for version <- Codec.versions(blob),
        {:ok, doc} = Codec.resolve(blob, version),
        do: doc
  end

  defp field_names(doc) do
    nested =
      doc
      |> Map.values()
      |> Enum.filter(&is_list/1)
      |> List.flatten()
      |> Enum.filter(&is_map/1)
      |> Enum.flat_map(&Map.keys/1)

    Map.keys(doc) ++ nested
  end

  defp href_for("CHEBI:" <> _ = curie), do: "https://www.ebi.ac.uk/chebi/beta/#{curie}"
  defp href_for("MONDO:" <> id), do: "https://monarchinitiative.org/MONDO:#{id}"
end

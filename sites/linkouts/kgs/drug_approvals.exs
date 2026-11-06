# Display config for the DrugApprovals KP.
#
# Ported from the legacy KGinfo/drug_approvals.pl, field by field, against real DAKP documents
# rather than from memory: the field names below were confirmed by decoding the committed contract
# corpus (dagster/projects/linkouts/contracts/docs.ndjson). Two of the legacy behaviours are deliberately not
# reproduced - see the notes on `relation` and on the missing-name fallback.
%{
  # Everything this file does not declare is inherited from kgs/_default.exs: the generic KGX
  # rendering (names, kinds, provenance, the qualifier stack, sources). Only DAKP's specifics
  # live here.
  extends: "default",

  # The canonical identifier: the infores the graph is registered under. Its slug — the same
  # name without the `infores:` prefix — is what gets stored on documents and put in URLs.
  name: "infores:drugapprovals-kp",
  display_name: "DrugApprovals KP",
  url: "https://github.com/NCATSTranslator/Translator-All/wiki/Multiomics-Drug-Approvals-KP",
  # One flowing string: the page wraps it to the article measure. Hard line breaks in a
  # heredoc would survive white-space handling and print as a ragged narrow column.
  description:
    "The DrugApprovals KP curates assertions of FDA- and EMA-approved treatment relationships, usages observed in FAERS and Health Canada's Canada Vigilance database and applied to treat, contraindications text-mined from DailyMed labeling, and product active-ingredient composition from DailyMed and Drugs@FDA; these assertions are derived from DailyMed, Drugs@FDA, FAERS, Canada Vigilance, and the EMA medicines registry.",
  feedback_repo: "https://github.com/glusman-team/graph-monorepo",

  # Known issues: each entry states one defect, then lists every release where it occurs
  # (:versions, requirements in the {:version, ...} syntax — a bare version matches exactly).
  # The description is the curator's own words about what is wrong, shown verbatim in a
  # notice when any of those releases is displayed.
  known_errors: [
    %{
      description:
        "The per-edge linkouts on db.systemsbiology.net do not exist for this version.",
      versions: ["1.23.3"]
    },
    %{
      description:
        "The qualifiers on these edges are improperly scoped and include some disallowed values.",
      versions: ["1.23.3", "1.23.4", "1.23.5"]
    }
  ],

  # Measured drift, and messier than a rename: the 1.11.2 dump spells this
  # FDA_regulatory_approvals, and so does one 1.16.0 dump, while another 1.16.0 dump spells it
  # regulatory_approvals. A version gate would be wrong in both directions, so this is an unscoped
  # fallback: the canonical name wins when present, the old spelling is used otherwise.
  aliases: [
    %{field: "regulatory_approvals", as: "FDA_regulatory_approvals"}
  ],
  slots: %{
    "subject" => {:link, "subject_name", "subject"},
    "object" => {:link, "object_name", "object"},

    # The legacy code picked "preventing" vs "treating" off object_modifier, then the verb phrase
    # off the predicate. Same two decisions, expressed as data.
    "verb" =>
      {:pick, "object_modifier",
       %{
         "prevention" => "preventing",
         :default => "treating"
       }},
    "relation" =>
      {:pick, "predicate",
       %{
         "biolink:treats" => "has been approved for {verb}",
         # Composition is not a treatment claim. DAKP 1.27.0 introduced product-to-ingredient
         # edges, so they need explicit wording rather than the generic treatment fallback.
         "biolink:has_active_ingredient" => "lists",
         # applied_to_treat is an observed usage rather than an approval, so the claim
         # takes the predicate's own wording ("has been applied to treat") instead of the
         # generic "used for" fallback. When the source stored the usage as off-label, the
         # headline says so and the evidence paragraph drops its status sentence — one
         # statement of the fact, in the loudest place.
         "biolink:applied_to_treat" =>
           {:if, [{:eq, "clinical_approval_status", "off_label_use"}],
            "was applied off label to treat", "has been applied to treat"},
         # Contraindication is a family of predicates, not one value, so it is matched rather than
         # listed: biolink:contraindicated_in, biolink:contraindicated_for, and any later addition.
         :default =>
           {:if, [{:matches, "predicate", "^biolink:contraindicated"}],
            "is contraindicated for patients with", "has been used for {verb}"}
       }},

    # The text relationship line names each node with its linkout; the CURIE itself is an
    # identifier, not part of the claim, so it sits behind a "curie" chip that reveals it in
    # place — one click to see or copy, no parentheses breaking the sentence.
    "subject_curie" => {:fold, "curie", {:link, "subject", "subject"}},
    "object_curie" => {:fold, "curie", {:link, "object", "object"}},

    # Inside a {:list, "sources", ...} element, slots resolve against the element itself, so
    # {infores} links this source's own resource_id into the Translator information resource
    # registry catalog, and {role} is this source's role, not the edge's.
    # The RIG's supporting_data_source_info names each source in words a reader knows
    # (DailyMed, FAERS, the EMA medicines registry); the name links to the source's page in
    # the information resource registry where one is registered. The legacy slug
    # multiomics-drugapprovals moved to drugapprovals-kp, but stored edges from before the
    # rename still carry the legacy id, so both branches name the same page;
    # the RIG's infores:ema, infores:epar and infores:canada-vigilance are not in the registry
    # at all, so those link to the agencies' own pages instead of a 404. Unknown sources fall
    # back to the linked CURIE.
    "infores" =>
      {:pick, "resource_id",
       %{
         # Stored edges from before the rename carry the legacy id; both name it the same.
         "infores:drugapprovals-kp" =>
           {:url,
            "https://biolink.github.io/information-resource-registry/resources/drugapprovals-kp/",
            "DrugApprovals KP"},
         "infores:multiomics-drugapprovals" =>
           {:url,
            "https://biolink.github.io/information-resource-registry/resources/drugapprovals-kp/",
            "DrugApprovals KP"},
         "infores:dailymed" =>
           {:url, "https://biolink.github.io/information-resource-registry/resources/dailymed/",
            "DailyMed"},
         "infores:drugs-at-fda" =>
           {:url, "https://www.accessdata.fda.gov/scripts/cder/daf/", "DrugsFDA"},
         "infores:faers" =>
           {:url, "https://biolink.github.io/information-resource-registry/resources/faers/",
            "the FDA Adverse Event Reporting System (FAERS)"},
         "infores:ema" =>
           {:url, "https://www.ema.europa.eu/en/medicines", "the EMA medicines registry"},
         "infores:epar" =>
           {:url, "https://www.ema.europa.eu/en/medicines/download-medicine-data", "the EMA EPAR"},
         "infores:canada-vigilance" =>
           {:url,
            "https://www.canada.ca/en/health-canada/services/drugs-health-products/medeffect-canada/adverse-reaction-database.html",
            "Canada Vigilance Adverse Reaction Database"},
         # From 1.25.0 the ICD-10-CM Table of Drugs and Chemicals screens observed-use
         # subjects (the RIG's filtered_content), so applied_to_treat edges cite it.
         "infores:icd10cm" =>
           {:url, "https://biolink.github.io/information-resource-registry/resources/icd10cm/",
            "the ICD-10-CM Table of Drugs and Chemicals"},
         :default => {:link, "resource_id", "resource_id"}
       }},
    # The legacy page printed approval statuses verbatim ("off_label_use"). Naming them in plain
    # language is the same fact in the reader's vocabulary; an unlisted status still renders,
    # humanized, rather than disappearing.
    "status" =>
      {:pick, "clinical_approval_status",
       %{
         "approved_for_condition" => "approved for the indicated condition",
         "off_label_use" => "observed off-label use",
         "contraindicated" => "contraindicated",
         "not_provided" => "unspecified",
         :default => {:humanize, "clinical_approval_status"}
       }},
    # Three read inline; a longer run folds behind a "show N more" toggle right where the list
    # sits, the way the legacy page kept its labelled lines to a row each.
    "approvals" => {:list, "regulatory_approvals", {:link, "self", "self"}, ", ", 3},

    # Species context (from release 1.25.0): DAKP resolves the taxon itself, so the original_
    # log line repeats the CURIE and the readable phrase is the resolved name logged as
    # species_context_qualifier_name — a binomial with the common name in parentheses
    # ("Equus caballus (horse)", the organismtaxon.txt 2026jul22 wording, verified over all
    # eleven taxa in 1.25.1). The binomial is a label, not a phrase a reader says, so each
    # name rewords to the common plural the sentence needs: "... has been approved for
    # treating Insomnia in horses." Cattle and sheep plural as themselves. A name the map
    # does not list renders verbatim rather than disappearing, and a release without the
    # name line falls back to the linked CURIE chip.
    "species_qualifier_link" => {:link, "species_context_qualifier", "species_context_qualifier"},
    "species_local" => {:local, "species_context_qualifier"},
    "species_link" =>
      {:url, "https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id={species_local}",
       "{species_name}"},
    "qualifier_species" => {:if, [{:present, "species_context_qualifier"}], " in {species_link}"},
    "species_name" =>
      {:supporting, "species_context_qualifier_name", "", "",
       %{
         "homo sapiens (human)" => "humans",
         "canis lupus familiaris (dog)" => "dogs",
         "felis catus (domestic cat)" => "cats",
         "equus caballus (horse)" => "horses",
         "sus scrofa (pig)" => "pigs",
         "bos taurus (domestic cattle)" => "cattle",
         "capra hircus (goat)" => "goats",
         "ovis aries (sheep)" => "sheep",
         "oryctolagus cuniculus (rabbit)" => "rabbits",
         "gallus gallus (chicken)" => "chickens",
         "meleagris gallopavo (turkey)" => "turkeys"
       }, "{species_context_qualifier}"},

    # Inside a {:list, ...} element "self" is the element itself, so {:local, "self"} is the
    # element's id past its prefix — the exact SPL set id, shown and linked, where the legacy
    # page printed numbered links into DailyMed.
    "self_local" => {:local, "self"},
    "labels" =>
      {:list, "publications",
       {:url, "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={self_local}",
        "{self_local}"}, ", ", 3},
    # One 1.27.0 composition edge cites 3,231 labels. A fold would still put every link in
    # the initial HTML, so composition uses a bounded preview plus the total instead.
    "label_count" => {:count, "publications"},
    "composition_labels" =>
      {:preview, "publications",
       {:url, "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={self_local}",
        "{self_local}"}, ", ", 3},
    # The searches run over every name the source used for each concept - the preferred name
    # plus each pipe-delimited original - as an OR group, so a label that only spells the name
    # the way the source text did is still found. The link text stays the preferred name, so
    # what the reader reads is unaffected.
    #
    # Query syntax, live-verified against dailymed.nlm.nih.gov (2026-10-19): the query box is
    # Lucene; WITHOUT adv=1 it searches drug names only, so a disease name matches nothing
    # ("neural tube defects" -> 0), while adv=1 searches full label text (same query -> 1000).
    # Every search linkout therefore carries adv=1. The :terms variants add each multiword
    # name's bare word conjunction (documented semantics: NAME:(a b) = both words), because
    # DAKP corroborates a treats edge when the label names a substring of the stored phrase:
    # the object can be longer than anything the label says (FAERS "hormone receptor positive
    # breast cancer" corroborated by a label saying only "breast cancer"), and a quoted
    # phrase that long matches nothing while the word conjunction still reaches the label.
    "subject_query" =>
      {:or_query, "subject_name", "original_subject", %{variants: [:phrase, :terms]}},
    "object_query" =>
      {:or_query, "object_name", "original_object", %{variants: [:phrase, :terms]}},
    "search_subject" =>
      {:url,
       "https://dailymed.nlm.nih.gov/dailymed/search.cfm?adv=1&labeltype=all&query={subject_query}",
       "labels mentioning {subject_name}"},
    "search_object" =>
      {:url,
       "https://dailymed.nlm.nih.gov/dailymed/search.cfm?adv=1&labeltype=all&query={object_query}",
       "labels mentioning {object_name}"},
    # The both-names search used to constrain the object to the Indications section
    # ("34067-9:"), the section approved-treats are corroborated in. That zeroed out real
    # edges: contraindications are NER-mined from six SPL sections (Contraindications 34070-3,
    # Indications 34067-9, Boxed Warning 34066-1, Warnings 43685-7 and the legacy 34071-1 /
    # 42232-9), and the DESFLURANE-contraindicated-for-asthma edge's mention lives in the
    # Warnings section, where the indications-only query returned 0 results for an edge with
    # a supporting label in hand. Unrestricted full-text AND is a strict superset of any
    # section-scoped form (verified live), so the object stays unrestricted.
    "search_both" =>
      {:url,
       "https://dailymed.nlm.nih.gov/dailymed/search.cfm?adv=1&labeltype=all&query=({subject_query})+AND+({object_query})",
       "labels mentioning both {subject_name} and {object_name}"},
    "search_composition" =>
      {:url,
       "https://dailymed.nlm.nih.gov/dailymed/search.cfm?adv=1&labeltype=all&query=({subject_query})+AND+({object_query})",
       "DailyMed labels mentioning both {subject_name} and {object_name}"},

    # The sentence opens by naming its source, and the name links out to the source's
    # documentation: the same target as the header's external-link icon (the top-level
    # :url), so a reader who lands mid-sentence reaches it without scrolling back up.
    # Slots cannot reference the config's own top-level keys, so the URL is written out
    # here too and must stay in sync with :url.
    "kp" =>
      {:url,
       "https://github.com/NCATSTranslator/Translator-All/wiki/Multiomics-Drug-Approvals-KP",
       "DrugApprovals KP"},

    # Provenance names the integrator once and lists what it integrated: the primary source
    # is DAKP itself, so it renders as nothing inside the list (empty elements are dropped)
    # and the sentence names the supporting sources with their linkouts.
    "supporting_source" =>
      {:pick, "resource_role",
       %{
         "primary_knowledge_source" => "",
         :default => "{infores}"
       }},
    "supporting_sources" => {:list, "sources", "{supporting_source}", ", ", 5, "and"},
    "dakp" =>
      {:strong,
       {:url,
        "https://biolink.github.io/information-resource-registry/resources/drugapprovals-kp/",
        "DrugApprovals KP"}},
    "generated_by" =>
      {:if, [{:present, "sources"}],
       "This edge was generated by {dakp}, which integrated {supporting_sources} in this assertion."},
    "composition_methods" =>
      {:pick, "agent_type",
       %{
         "manual_validation_of_automated_agent" =>
           "This composition was machine-generated by {dakp} from {supporting_sources}, with dataset-level manual quality control.",
         "automated_agent" =>
           "This composition was machine-generated by {dakp} from {supporting_sources}.",
         "manual_agent" =>
           "This composition was human-curated by {dakp} from {supporting_sources}.",
         :default => "{provenance} {generated_by}"
       }}
  },
  # The lead-in line above the sentence: who is making the claim. Rendered as its own
  # paragraph ending in a colon, so the main sentence reads as the claim alone
  # ("DrugApprovals KP asserts:" / "Metformin has been approved for treating ..."),
  # the way the legacy page headed each relationship.
  asserts: "{kp} asserts",
  title:
    {:pick, "predicate",
     %{
       "biolink:has_active_ingredient" => "{subject_name} active ingredient: {object_name}",
       :default => "{subject_name} and {object_name}"
     }},
  edge:
    {:pick, "predicate",
     %{
       "biolink:has_active_ingredient" =>
         "{subject} {relation} {object} as an active ingredient{context}{qualifier_species}{qualifier_anatomical}{qualifier_frequency}{qualifier_population}{qualifier_sex}{qualifier_temporal}.",
       :default =>
         "{subject} {relation} {object}{context}{qualifier_species}{qualifier_anatomical}{qualifier_frequency}{qualifier_population}{qualifier_sex}{qualifier_temporal}."
     }},

  # Methods retain how the statement was produced, without repeating the headline.
  relationship: nil,
  methods:
    {:pick, "predicate",
     %{
       "biolink:has_active_ingredient" => "{composition_methods}",
       :default => "{provenance} {generated_by}"
     }},

  # The evidence block, as prose. The legacy page ran these same facts as optional lines —
  # "Number of FAERS cases reporting this usage: 12", "Relevant approvals: NDA202155" — and the
  # instinct stands: a sentence carries its own meaning, where a table row labelled
  # "Assertion method (agent_type)" asked the reader to decode a storage key. Each entry is one
  # sentence; the :if conditions drop the ones the document cannot support; the renderer joins
  # what survives into one paragraph.
  evidence: [
    %{
      value:
        {:pick, "clinical_approval_status",
         %{
           "approved_for_condition" =>
             "DrugApprovals KP claims this association is approved for the indicated condition.",
           # Stated in the headline ("was applied off label to treat"); an empty render
           # drops the sentence here rather than repeating it in quieter type.
           "off_label_use" => "",
           "contraindicated" => "DrugApprovals KP claims this association is contraindicated.",
           "not_provided" =>
             "DrugApprovals KP does not specify the approval status of this association.",
           :default => "DrugApprovals KP characterizes this association as {status}."
         }},
      if: [{:present, "clinical_approval_status"}]
    },
    %{
      value:
        {:pick, "predicate",
         %{
           # The product and ingredient are already named in the claim. Their source aliases
           # remain in KGX without burying composition evidence under another long list.
           "biolink:has_active_ingredient" => "",
           :default => "{node_mappings}"
         }}
    },
    %{value: "{qualifier_mappings}"},
    # The legacy page always printed the approvals line, falling back to an explicit
    # "none" sentence. Here the line simply drops when the source has no numbers: a
    # shorter paragraph beats one that spends a sentence saying nothing is there.
    %{
      value:
        {:if, [{:present, "regulatory_approvals"}],
         "Regulatory approval for this association is documented under {approvals}."}
    },
    # Legacy: "Number of FAERS cases reporting this usage: 12".
    %{
      # The count is computed in the DAKP pipeline as the union of distinct case ids across
      # FAERS and Canada Vigilance spontaneous reports (both aggregation paths land in one
      # table and Tablassert's uuid merge recomputes number_of_cases as the union), so the
      # sentence names both databases. EMA contributes no cases: it feeds the approval and
      # contraindication tables. DailyMed corroborates labels, not cases. The full names are
      # established by the first paragraph's sources sentence, so acronyms suffice here.
      value: "FAERS and Canada Vigilance record {number_of_cases} cases of this usage.",
      if: [{:present, "number_of_cases"}]
    },
    # Legacy: "Relevant product labels: 1, 2", each SPL set id linked into DailyMed. Like
    # the approvals line above: no set ids, no sentence.
    %{
      value:
        {:pick, "predicate",
         %{
           "biolink:has_active_ingredient" =>
             {:if, [{:present, "publications"}],
              {:if, [{:count_gt, "publications", 3}],
               "DailyMed documents this composition in {label_count} product labels, including {composition_labels}. The KGX download preserves the complete list.",
               "DailyMed documents this composition in {composition_labels}."}},
           :default =>
             {:if, [{:present, "publications"}],
              "This association is documented on the product labels {labels}."}
         }}
    }
  ],

  # Helper text the reader acts on, not a fact about the edge: the DailyMed searches the
  # legacy page ended with ("Search labels for $subject, labels for $subject and $object").
  # It renders as its own paragraph beneath the evidence paragraph, so an action item does
  # not dilute the evidence argument.
  footnote:
    {:pick, "predicate",
     %{
       "biolink:has_active_ingredient" => "Verify this composition in {search_composition}.",
       :default =>
         "Try searching DailyMed for {search_subject}, {search_object}, or {search_both}."
     }}
}

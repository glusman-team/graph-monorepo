# Display config for the Multiomics KP (MULTIOMICS_KP).
#
# Written against the actual 3.1.3 edges file, not from the dataset card alone: every value
# domain below (predicates, effect types, significance tokens, qualifier values, source
# records, publication prefixes) is the exact result of scanning all 18,206,965 edges, and
# every field referenced was observed in that file. The starter came from
# the inspect_kgx Dagster job and was then finished per the add-a-KG guide.
%{
  # Everything this file does not declare is inherited from kgs/_default.exs: the generic KGX
  # rendering (names, kinds, the qualifier stack, sources). Only Multiomics KP's specifics
  # live here.
  extends: "default",

  # The canonical identifier: the infores the graph is registered under. Its slug
  # ("multiomics-kp") is what documents store and URLs carry.
  name: "infores:multiomics-kp",
  display_name: "Multiomics KP",
  url: "https://github.com/NCATSTranslator/Translator-All/wiki/Multiomics-KP",
  # One flowing string: the page wraps it to the article measure. Hard line breaks in a
  # heredoc would survive white-space handling and print as a ragged narrow column.
  description:
    "The Multiomics KP mines assertions from the supplementary tables of open-access PubMed Central articles in the rare-disease cohort, mapping configured table records to Biolink subject-predicate-object statements and resolving entities against BABEL; each edge carries the statistical evidence, direction and aspect qualifiers, and publication provenance the source table reports.",
  feedback_repo: "https://github.com/SkyeAv/Tablassert",

  # Known limitations of the build, stated in the dataset card's own words. Shown as a
  # notice on every 3.1.3 edge page, because the card frames this build as not
  # scientifically finalized.
  known_errors: [
    %{
      description:
        "This is a working build, not a scientifically finalized release: no fresh domain-and-range audit was re-run for 3.1.3, statistical significance qualifiers are bare enum tokens, and treats-style assertions are modeled as applied_to_treat without approval evidence.",
      versions: ["3.1.3"]
    }
  ],
  slots: %{
    "subject" => {:link, "subject_name", "subject"},
    "object" => {:link, "object_name", "object"},

    # The predicate in the reader's vocabulary: one branch per predicate observed in the
    # 3.1.3 file (all 29, confirmed by full scan), most mirroring the humanized biolink
    # term so the sentence reads as a claim rather than a storage key. The exceptions are
    # the ones the generic wording would misstate:
    #
    #   * applied_to_treat keeps the predicate's own wording on purpose: the build models
    #     treats-style assertions as applied_to_treat without approval evidence (stated on
    #     the dataset card), so the sentence must not read as an approval.
    #   * response_associated_with's subject IS the response, so the branch says so.
    #   * the likelihood family gets its articles so the headline parses as English.
    "relation" =>
      {:pick, "predicate",
       %{
         "biolink:associated_with" => "is associated with",
         "biolink:affects" => "affects",
         "biolink:affected_by" => "is affected by",
         "biolink:affects_likelihood_of" => "affects the likelihood of",
         "biolink:associated_with_likelihood_of" => "is associated with the likelihood of",
         "biolink:associated_with_increased_likelihood_of" =>
           "is associated with an increased likelihood of",
         "biolink:associated_with_response_to" => "is associated with response to",
         "biolink:response_associated_with" => "is a response associated with",
         "biolink:applied_to_treat" => "has been applied to treating",
         "biolink:binds" => "binds",
         "biolink:causes" => "causes",
         "biolink:contributes_to" => "contributes to",
         "biolink:correlated_with" => "is correlated with",
         "biolink:coexpressed_with" => "is coexpressed with",
         "biolink:directly_physically_interacts_with" => "directly physically interacts with",
         "biolink:disease_has_location" => "has location",
         "biolink:expressed_in" => "is expressed in",
         "biolink:expresses" => "expresses",
         "biolink:gene_associated_with_condition" => "has a genetic association with",
         "biolink:gene_fusion_with" => "forms a gene fusion with",
         "biolink:genetically_associated_with" => "is genetically associated with",
         "biolink:genetically_interacts_with" => "genetically interacts with",
         "biolink:has_gene_product" => "has gene product",
         "biolink:has_phenotype" => "has phenotype",
         "biolink:actively_involved_in" => "is actively involved in",
         "biolink:interacts_with" => "interacts with",
         "biolink:participates_in" => "participates in",
         "biolink:physically_interacts_with" => "physically interacts with",
         "biolink:regulates" => "regulates",
         :default => {:humanize, "predicate"}
       }},

    # Effect types as a reader knows them: the 14 values observed in the file are
    # statistical-estimator names (log2_fold_change, odds_ratio, the Mendelian-randomization
    # estimators), so they are named with their proper spellings rather than humanized
    # (which would print "pearsons r"). Anything new falls back to the humanized value.
    "effect_type_prose" =>
      {:pick, "effect_type",
       %{
         "log2_fold_change" => "log2 fold change",
         "regression_coefficient" => "regression coefficient",
         "odds_ratio" => "odds ratio",
         "hazard_ratio" => "hazard ratio",
         "correlation_coefficient" => "correlation coefficient",
         "pearsons_r" => "Pearson's r",
         "spearmans_rho" => "Spearman's rho",
         "relative_risk" => "relative risk",
         "standardized_mean_difference" => "standardized mean difference",
         "cohens_d" => "Cohen's d",
         "inverse_variance_weighted" => "inverse-variance-weighted estimate",
         "wald_ratio" => "Wald ratio",
         "weighted_median" => "weighted-median estimate",
         "mr_egger" => "MR-Egger estimate",
         :default => {:humanize, "effect_type"}
       }},

    # Significance qualifiers are bare enum tokens in this build (very_strongly_significant
    # down to suggestive, the complete observed set), listed so the sentence reads as plain
    # English; an unlisted token still renders, humanized, rather than disappearing.
    "significance_prose" =>
      {:pick, "statistical_significance_qualifier",
       %{
         "very_strongly_significant" => "very strongly significant",
         "strongly_significant" => "strongly significant",
         "significant" => "significant",
         "suggestive" => "suggestive",
         :default => {:humanize, "statistical_significance_qualifier"}
       }},

    # Statistics in printf "%.4g": four significant digits, scientific notation outside
    # 1e-4..1e4 ("5.677e-07"). Effect sizes are JSON doubles carrying up to 17 digits;
    # p values are strings in this build (the dataset card: inequality notation is
    # preserved), and {:number, _, :g4} formats numeric strings and keeps a leading
    # inequality operator, so all three read alike. A value that does not parse as a
    # number, or would underflow a double (1e-400), renders as the source wrote it.
    "effect_size_g4" => {:number, "effect_size", :g4},
    "p_value_g4" => {:number, "p_value", :g4},
    "adjusted_p_value_g4" => {:number, "adjusted_p_value", :g4},

    # Direction/aspect qualifiers: the observed domains are expression/activity/abundance
    # (subject aspect) and increased/decreased (both directions), so humanizing is the
    # prose already.
    "subject_aspect_prose" => {:humanize, "subject_aspect_qualifier"},
    "subject_direction_prose" => {:humanize, "subject_direction_qualifier"},
    "object_direction_prose" => {:humanize, "object_direction_qualifier"},

    # Multiomics carries its anatomical context as a LIST of CURIEs, so the default's
    # single-CURIE qualifier branch cannot render it; this override lists them, linked.
    "anatomical_context_list" =>
      {:list, "anatomical_context_qualifier", {:link, "self", "self"}, " and "},
    "qualifier_anatomical" =>
      {:if, [{:present, "anatomical_context_qualifier"}], ", in {anatomical_context_list}"},

    # The population context is a single CURIE with no resolved name in this build (only
    # UMLS:C0085756 is observed), so it links as a chip instead of taking the default's
    # name-first branch.
    "population_context_link" =>
      {:link, "population_context_qualifier", "population_context_qualifier"},
    "qualifier_population" =>
      {:if, [{:present, "population_context_qualifier"}],
       ", in population {population_context_link}"},

    # Inside a {:list, "sources", ...} element, slots resolve against the element itself.
    # Every edge in this build carries the same two source records (confirmed over all
    # 18,206,965 edges): the KP itself as primary and PubMed Central as the supporting
    # data source, so both are named with their linkouts. Unknown sources fall back to the
    # linked CURIE.
    "infores" =>
      {:pick, "resource_id",
       %{
         "infores:multiomics-kp" =>
           {:url,
            "https://biolink.github.io/information-resource-registry/resources/multiomics-kp/",
            "Multiomics KP"},
         "infores:pubmed-central" => {:url, "https://pmc.ncbi.nlm.nih.gov/", "PubMed Central"},
         :default => {:link, "resource_id", "resource_id"}
       }},

    # Provenance names the integrator once and lists what it integrated: the primary source
    # is the KP itself, so it renders as nothing inside the list (empty elements are
    # dropped) and the sentence names the supporting source.
    "supporting_source" =>
      {:pick, "resource_role",
       %{
         "primary_knowledge_source" => "",
         :default => "{infores}"
       }},
    "supporting_sources" => {:list, "sources", "{supporting_source}", ", ", 5, "and"},
    "mokp" =>
      {:strong,
       {:url, "https://biolink.github.io/information-resource-registry/resources/multiomics-kp/",
        "Multiomics KP"}},
    "generated_by" =>
      {:if, [{:present, "sources"}],
       "This edge was generated by {mokp}, which integrated {supporting_sources} in this assertion."},

    # The sentence opens by naming its source, and the name links out to the source's
    # documentation: the same target as the header's external-link icon (the top-level
    # :url), so a reader who lands mid-sentence reaches it without scrolling back up.
    # Slots cannot reference the config's own top-level keys, so the URL is written out
    # here too and must stay in sync with :url.
    "kp" =>
      {:url, "https://github.com/NCATSTranslator/Translator-All/wiki/Multiomics-KP",
       "Multiomics KP"},

    # Every observed edge is a statistical association produced by a data analysis pipeline
    # (knowledge_level=statistical_association, agent_type=data_analysis_pipeline on all
    # 18.2M edges), so the generic "statement" provenance of the default config is replaced
    # with wording that says what this KG's edges actually are. The fallback keeps the
    # default's generic phrasing for any future release that changes the mix.
    "agent_type_prose" => {:humanize, "agent_type"},
    "provenance" =>
      {:pick, "knowledge_level",
       %{
         "statistical_association" =>
           "This is a statistical association computed from a published supplementary table by an automated data analysis pipeline.",
         :default =>
           {:if, [{:present, "agent_type"}],
            "The assertion method of this statement is {agent_type_prose}.",
            "The provenance of this statement is not recorded."}
       }},

    # Publications are PMCID CURIEs on every edge (verified), linked into PMC by
    # _prefixes.exs. Three read inline; a longer run folds behind a "show N more" toggle.
    "publication_list" => {:list, "publications", {:link, "self", "self"}, ", ", 3},

    # has_evidence carries the source table's own evidence phrases ("Promoter (1-2kb)"),
    # rendered as a plain list.
    "has_evidence_list" => {:list, "has_evidence", {:field, "self"}, ", "},

    # The footnote's searches run over every name the source used for each concept - the
    # preferred name plus each pipe-delimited original - as an OR group, so an article that
    # only spells the name the way the source table did is still found. The link text stays
    # the preferred name, so what the reader reads is unaffected. The :terms variants add
    # each multiword name's bare word conjunction, because a phrase that long often matches
    # nothing in PMC's index while the word conjunction still reaches the article.
    "subject_query" =>
      {:or_query, "subject_name", "original_subject", %{variants: [:phrase, :terms]}},
    "object_query" =>
      {:or_query, "object_name", "original_object", %{variants: [:phrase, :terms]}},
    "search_subject" =>
      {:url, "https://www.ncbi.nlm.nih.gov/pmc/?term={subject_query}",
       "articles mentioning {subject_name}"},
    "search_object" =>
      {:url, "https://www.ncbi.nlm.nih.gov/pmc/?term={object_query}",
       "articles mentioning {object_name}"},
    "search_both" =>
      {:url, "https://www.ncbi.nlm.nih.gov/pmc/?term=({subject_query})+AND+({object_query})",
       "articles mentioning both {subject_name} and {object_name}"}
  },

  # The lead-in line above the sentence: who is making the claim. Rendered as its own
  # paragraph ending in a colon, so the main sentence reads as the claim alone
  # ("Multiomics KP asserts:" / "SOD1 is associated with amyotrophic lateral sclerosis").
  asserts: "{kp} asserts",
  title: "{subject_name} and {object_name}",
  edge: "{subject} {relation} {object}{qualifier_anatomical}{qualifier_population}.",

  # Methods retain how the statement was produced, without repeating the headline.
  methods: "{provenance} {generated_by}",

  # The provenance list, one bullet per claim: the statistical claim first, then publication and
  # mapping provenance. The :if conditions drop the claims the document cannot support, and an
  # entry that renders no text drops instead of printing an empty bullet.
  evidence: [
    %{
      # Every statistic reads in printf %.4g ({effect_size_g4} and friends): effect sizes
      # arrive as raw doubles ("6.276975374"), p values as strings in mixed precision.
      value:
        {:if, [{:present, "effect_type"}],
         "The reported effect size is {effect_size_g4} ({effect_type_prose}).",
         "The reported effect size is {effect_size_g4}."},
      if: [{:present, "effect_size"}]
    },
    %{
      value: "The reported p value is {p_value_g4}.",
      if: [{:present, "p_value"}]
    },
    %{
      value: "The adjusted p value is {adjusted_p_value_g4}.",
      if: [{:present, "adjusted_p_value"}]
    },
    %{
      value: "The study classifies this result as {significance_prose}.",
      if: [{:present, "statistical_significance_qualifier"}]
    },
    %{
      value:
        {:if, [{:present, "subject_aspect_qualifier"}],
         "The measured {subject_aspect_prose} of the subject was {subject_direction_prose}.",
         "The subject measurement was {subject_direction_prose}."},
      if: [{:present, "subject_direction_qualifier"}]
    },
    %{
      value: "The object measurement was {object_direction_prose}.",
      if: [{:present, "object_direction_qualifier"}]
    },
    %{
      value: "This association is reported in {publication_list}.",
      if: [{:present, "publications"}]
    },
    %{
      value: "The source table describes the evidence as {has_evidence_list}.",
      if: [{:present, "has_evidence"}]
    },
    # Deliberately omitted:
    #   * supporting_text, because every entry is an internal entity-resolution log line
    #     ("object_nlp_level: 1"), not a reader fact;
    #   * has_supporting_studies, because it is a publication-keyed map and the slot ops
    #     iterate lists, not maps - its study_context sentence is exactly what a reader
    #     would want, but there is no spec form that reaches it today.
    %{value: "{subject_mapping}"},
    %{value: "{object_mapping}"},
    %{value: "{mapping_species_context_qualifier}"},
    %{value: "{qualifier_mappings}"},
    %{
      value: "This association is derived from {sources}.",
      if: [{:present, "sources"}]
    }
  ],

  # Helper text the reader acts on, not a fact about the edge: the PMC searches, mirroring
  # the DailyMed searches the drug approvals page ends with. It renders as its own
  # paragraph beneath the provenance list, so an action item does not dilute the
  # evidence argument.
  footnote:
    "Try searching PubMed Central for {search_subject}, {search_object}, or {search_both}."
}

# CURIE prefix → linkout URL templates, ported from the legacy KGinfo.pl addLinkout().
#
# Extracted mechanically from the Perl rather than retyped, so the set matches what has been
# in production. `$curie` is the whole CURIE ("MONDO:0004979") and `$value` is the part after
# the first colon ("0004979"); templates choose whichever the target site expects.
#
# Prefixes are matched case-insensitively. A CURIE whose prefix is not listed renders as plain
# text, never as a broken link: an unresolvable identifier is still worth showing.
%{
  exact: %{
    "CHEBI" => "https://www.ebi.ac.uk/chebi/beta/$curie",
    "CHEMBL.COMPOUND" => "https://www.ebi.ac.uk/chembl/compound_report_card/$value",
    "EC" => "https://www.genome.jp/dbget-bin/www_bget?$value",
    "EFO" => "https://www.ebi.ac.uk/ols4/ontologies/efo/classes?short_form=EFO_$value",
    "FB" => "https://flybase.org/reports/$value",
    "GO" => "https://www.ebi.ac.uk/QuickGO/term/GO:$value",
    "HMDB" => "https://hmdb.ca/metabolites/$value",
    "HP" => "https://hpo.jax.org/app/browse/term/$curie",
    "LOINC" => "https://loinc.org/$value",
    "MESH" => "https://id.nlm.nih.gov/mesh/$value",
    "MONDO" => "https://monarchinitiative.org/$curie",
    "UMLS" => "https://identifiers.org/$curie",
    # Qualifier values on DAKP edges arrive as these CURIEs too (the RIG's sparse qualifier
    # stack: anatomical, frequency, population, sex, temporal context).
    "NCIT" =>
      "https://ncit.nci.nih.gov/ncitbrowser/ConceptReport.jsp?dictionary=NCI_Thesaurus&code=$value",
    "UBERON" => "https://monarchinitiative.org/$curie",
    "NCBITAXON" => "https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=$value",
    # FDA application numbers reach the label browser by number; the legacy page linked
    # NDA/ANDA/BLA spellings to the same browser, and ANDA- or BLA-only edges keep that.
    "NDA" => "https://fda.report/applications/$value",
    "ANDA" => "https://fda.report/applications/$value",
    "BLA" => "https://fda.report/applications/$value",
    "PR" => "https://proconsortium.org/cgi-bin/entry_pro?id=PR_$value",
    "PUBCHEM.COMPOUND" => "https://pubchem.ncbi.nlm.nih.gov/compound/$value",
    "RHEA" => "https://www.rhea-db.org/rhea/$value",
    "RO" => "http://purl.obolibrary.org/obo/RO_$value",
    "UNII" => "https://gsrs.ncats.nih.gov/ginas/app/beta/substances/$value",
    "UNIPROTKB" => "https://www.uniprot.org/uniprotkb/$value",
    # Site-specific linkouts that are not ontology CURIEs but appear in KGX slots.
    "PMID" => "https://pubmed.ncbi.nlm.nih.gov/$value/",
    "PMC" => "https://www.ncbi.nlm.nih.gov/pmc/articles/$value/",
    "DOI" => "https://doi.org/$value",
    # Model-organism and gene resources the Multiomics KP nodes resolve to. All verified to
    # resolve for a real id (2026-10-22); PANTHER and OMIM 403 for non-browser agents but
    # load fine for a human, which is what a linkout is for.
    "NCBIGene" => "https://www.ncbi.nlm.nih.gov/gene/$value",
    "MGI" => "http://www.informatics.jax.org/marker/MGI:$value",
    "RGD" => "https://rgd.mcw.edu/rgdweb/report/gene/main.html?id=$value",
    "ZFIN" => "https://zfin.org/$value",
    "HGNC" => "https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/$curie",
    "GTOPDB" => "https://www.guidetopharmacology.org/GRAC/ObjectDisplayForward?objectId=$value",
    "PANTHER.FAMILY" => "http://www.pantherdb.org/panther/family.do?clsAccession=$value",
    "OMIM" => "https://omim.org/entry/$value",
    # The rest of the Multiomics KP 3.1.3 node prefixes (full scan of the nodes file,
    # 2026-10-22). Verified to resolve for a real id; WormBase and PANTHER 403 for
    # non-browser agents only.
    "DOID" => "https://disease-ontology.org/term/$curie",
    "CL" => "https://www.ebi.ac.uk/ols4/ontologies/cl/classes?short_form=CL_$value",
    "WORMBASE" => "https://wormbase.org/species/c_elegans/gene/$value",
    "SGD" => "https://www.yeastgenome.org/locus/$value",
    "PANTHER.PATHWAY" =>
      "http://www.pantherdb.org/pathway/pathwayDiagram.jsp?catAccession=$value",
    "HGNC.FAMILY" => "https://www.genenames.org/data/genegroup/#!/group/$value",
    "COMPLEXPORTAL" => "https://www.ebi.ac.uk/complexportal/complex/$value",
    "DRUGCENTRAL" => "https://drugcentral.org/drugcard/$value",
    "RXCUI" => "https://mor.nlm.nih.gov/RxNav/search?searchBy=RXCUI&searchTerm=$value",
    "REACT" => "https://reactome.org/content/detail/$value",
    # DailyMed SPL ids arrive as "dailymed:<uuid>" in the drug approvals KG.
    "DAILYMED" => "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=$value",
    # DrugBank drug ids arrive on DAKP edges whose subject the node join named from DrugBank.
    "DRUGBANK" => "https://go.drugbank.com/drugs/$value",
    # Information resources arrive as "infores:<slug>" and resolve to their page in the
    # Translator information resource registry's catalog (infores:dailymed, infores:faers, ...).
    "INFORES" => "https://biolink.github.io/information-resource-registry/resources/$value",
    # The FDA's own daf browser, for CURIEs that name it explicitly.
    "FDA.APPLICATION" =>
      "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=$value"
  },

  # Value-shape rewrites applied before the URL template. The legacy code special-cased these:
  # a bare "NDA12345" string has no colon, and PMC labels carry the id in the text.
  normalize: [
    # "NDA020346" -> prefix NDA, value "020346"
    %{match: ~r/^(NDA|ANDA|BLA)\s*0*(\d+)$/i, prefix: "$1", value: "$2"},
    # "PMCID:PMC1234" -> value "PMC1234"
    %{match: ~r/^PMCID:(.+)$/i, prefix: "PMC", value: "$1"}
  ],

  # A CURIE that is already a URL links to itself.
  passthrough: ~r{^https?://}i
}

---
title: "The default display config"
description: "Inheritance, fallback rendering, slots, provenance, qualifiers, and sentence templates."
sidebar:
  order: 2
---

`sites/linkouts/kgs/_default.exs` renders a standard KGX document: names, CURIEs, biolink predicates and
categories, the standard qualifier fields, and KGX's `sources` shape. It contains nothing
specific to any one knowledge graph.

It is used two ways, and both are worth understanding before writing an override.

## Two roles

**The base an override builds on.** A KG config declares `extends: "default"` and then states
only what is specific to it. The merge is per section:

| key             | merge                                                                   |
| --------------- | ----------------------------------------------------------------------- |
| `slots`         | per slot: an override replaces the slots it names and inherits the rest |
| `aliases`       | appended                                                                |
| every other key | replaced when the override declares it, inherited when it does not      |

`sites/linkouts/kgs/drug_approvals.exs` is the worked example. It overrides `subject`, `object`, `relation`
and `infores` (linked names with CURIE chips, per-predicate prose, agency URL branches) and
adds its own slots (`status`, `approvals`, `labels`, the searches). It inherits everything
generic: the qualifier stack, the disease-context clause, the provenance pick, the category
clauses, and the `sources` sentence.

**The fallback for an unconfigured KG.** A stored KG with no config of its own renders through
the default, so ingesting a random KGX dump produces a readable page before anyone writes a
single line of config. The default is never listed as a KG (`Display.known/0` does not include
it); it is infrastructure.

## What it renders

From this plain KGX edge:

```json
{
  "subject": "HGNC:11998",
  "subject_name": "TP53",
  "subject_category": ["biolink:Gene"],
  "predicate": "biolink:correlated_with",
  "object": "MONDO:0005070",
  "object_name": "sarcoma",
  "object_category": ["biolink:Disease"],
  "knowledge_level": "knowledge_assertion",
  "agent_type": "manual_validation_of_automated_agent",
  "sources": [{ "resource_id": "infores:any-kp", "resource_role": "primary_knowledge_source" }]
}
```

the headline states the relationship once. Methods describes its production:

> This observation is machine generated. The entire dataset underwent manual QC with some
> human validation.

Provenance lists supporting evidence:

> This association is derived from infores:any-kp.

Strip the document down to the three required KGX fields and the sentences shorten instead of
gaining holes:

> This relationship states that X:1 treats Y:2.

That shortening is the config's central rule: **a missing field drops its clause, it never
prints an empty frame.** No `", a ."`, no `"in the context of "` with nothing after it.

## Slot reference

### Identity

| slot                                | renders as                                                            |
| ----------------------------------- | --------------------------------------------------------------------- |
| `subject_display`, `object_display` | the node's name when the dump carries one, the CURIE itself otherwise |
| `relation`                          | the predicate as words: `biolink:treats` becomes "treats"             |

### Categories

`subject_kind` and `object_kind` render a biolink category as words and link it to that term's
own page in the [biolink model docs](https://biolink.github.io/biolink-model/), which name every
term's page by its local id (`biolink:SmallMolecule` links to `.../SmallMolecule/`). The clause
slots wrap them with the indefinite article and commas, and drop entirely when the category is
absent:

```elixir
"subject_kind_clause" => {:if, [{:present, "subject_category"}], ", a {subject_kind},"},
"object_kind_clause"  => {:if, [{:present, "object_category"}],  ", a {object_kind}"}
```

The subject clause carries its trailing comma inside the `{:if}` so "X, a Gene, treats Y" and
"X treats Y" are both punctuated correctly.

### Provenance

`provenance` picks a sentence from the standard KGX `knowledge_level` and `agent_type` enums,
in the reader's vocabulary:

| knowledge_level       | agent_type                             | sentence                                                                                                                                                            |
| --------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `knowledge_assertion` | `manual_validation_of_automated_agent` | "This observation is machine generated. The entire dataset underwent manual QC with some human validation." (the QC sentence is identical at every knowledge level) |
| `knowledge_assertion` | `automated_agent`                      | "This assertion is machine-generated."                                                                                                                              |
| `knowledge_assertion` | `manual_agent`                         | "This assertion is human-curated."                                                                                                                                  |
| `observation`         | ...                                    | the same four, saying "observation"                                                                                                                                 |
| any other level       | ...                                    | the same four, saying "statement"                                                                                                                                   |

Unlisted values fall back rather than disappear: an unknown `agent_type` renders "The assertion
method of this statement is text mining." (humanized); a missing `agent_type` renders "The
provenance of this statement is not recorded."

### Qualifiers

The six qualifier slots (`qualifier_species`, `qualifier_anatomical`, `qualifier_frequency`,
`qualifier_population`, `qualifier_sex`, `qualifier_temporal`) each prefer the readable phrase
the source logged in `supporting_text` (`original_anatomical_context_qualifier: knee joint`),
framed so the sentence flows ("...leg, in the knee joint, ..."). A rewording map repairs
fragments that would read badly in prose ("dosage" becomes "at the labeled dosage", "adult"
becomes "adults"). Where the log line is missing the slot falls back to a labelled CURIE chip
(", frequency UMLS:2"), because a chip identifies without pretending to read. The gates are on
the qualifier's presence, not the version, so any KG that populates these fields renders them.

`qualifier_species` reads the resolved organism name the source logged under
`species_context_qualifier_name` ("Homo sapiens (human)") rather than the `original_` line,
which for species repeats the CURIE; the evidence panel adds the mapping sentence
"This assertion is about Bos taurus (domestic cattle)." when the name is present.

The disease-context clause (`context`) renders " in the context of {name}" through a link, and
is gated on the _name_ field: a present CURIE with no name would otherwise print an empty
frame.

### Sources

`sources` renders the KGX `sources` list as a sentence: the primary knowledge source bold, the
rest plain, comma-separated, capped at five then an "and". Each `resource_id` links to its page
in the Translator information resource registry (`sites/linkouts/kgs/_prefixes.exs` maps every `infores:` id
to `.../resources/<slug>`). Override `infores` when a KG knows better URLs than that catalog
gives (DrugApprovals does, for the agencies it cites).

## Sentence templates

| template   | reads as                                                                                                    |
| ---------- | ----------------------------------------------------------------------------------------------------------- |
| `title`    | "{subject_display} and {object_display}"                                                                    |
| `edge`     | "This relationship states that X treats Y" + context + qualifiers                                           |
| `methods`  | the assertion-method prose from `{provenance}`                                                              |
| `evidence` | original-name mappings when supplied, plus "This association is derived from {sources}." when sources exist |

The page no longer repeats the headline as a relationship paragraph or diagram. Under
Provenance each distinct claim gets its own line: the original subject and object names each map
(`subject_mapping`, `object_mapping`) in a sentence that calls them by the role the knowledge
graph uses (`subject_role`, `object_role`: "subject" and "object" in the default, "treatment"
and "condition" for a DrugApprovals treatment edge). Roles can follow the predicate, so the
same sentence stays true on a different edge shape: a DrugApprovals composition edge calls
them "product" and "active ingredient". When the resolved concept's preferred name says
something the source's string does not ("0.9% SODIUM CHLORIDE" vs "Normal saline"), the mapping
sentence names it: "... mapped to UMLS:C0445115, which is the ChemicalEntity \"Normal saline\"." A
case-only difference ("FLUOXETINE" vs "fluoxetine") adds nothing the original has not already
printed, so the sentence stays as it was. The species sentence stands
alone (`mapping_species_context_qualifier`), and the remaining qualifier originals share one
compound sentence (`qualifier_mappings`). Every original string prints in double quotes, so a
reader can tell the source's words from the sentence around them. Pipe-delimited names use an
Oxford-comma list; absent originals omit the mapping, and absent categories omit only the class
clause. Configured helper links appear under Tips. All section headings use the same style as
About.

## Writing an override

Start from what the default already does right, and replace only the rest:

1. `extends: "default"`.
2. Override `relation` with per-predicate prose when the generic verb is not the claim a reader
   should see ("has been approved for treating", not "treats").
3. Override `subject`/`object` with linked names when the page should link nodes, and say so in
   the templates (the default's `{subject_display}` shows a plain name-or-CURIE).
4. Append KG-specific evidence sentences; the sources sentence stays.
5. Run `cd sites/linkouts && MIX_ENV=test mix linkouts.check`, then `mix test`. Compile-time validation fails the build
   on a malformed config, so a typo can never reach a page as a silently shorter sentence.

# Plan: `biolink:has_active_ingredient` edges (DailyMed + Drugs@FDA)

Status: IMPLEMENTED (all steps done; nx affected -t test green, dg check defs green)

## Context

DAKP emits three chemical-to-disease families today (`treats`, `applied_to_treat`,
`contraindicated_in`). Both FDA sources already in the DAG carry drug composition, but
the shapers discard it:

- **DailyMed SPL**: `spl_ingredients.parquet` records active/inactive ingredients
  per SPL *set* (name, UNII, role). The product name (`manufacturedProduct/name`,
  e.g. "Lipitor") and the substance-vs-moiety distinction are lost.
- **Drugs@FDA**: `products.parquet` already pairs `drug_name` ("VYTORIN") with
  `active_ingredient` ("EZETIMIBE; SIMVASTATIN") and the application number
  (51,633 rows, about 8.4k distinct drug names).

Goal: a fourth edge family, `<product> biolink:has_active_ingredient <ingredient>`.

Decisions (from review):

| # | Decision |
|---|---|
| Subject | Product name text (DailyMed `manufacturedProduct/name`, Drugs@FDA `drug_name`), resolved by Tablassert/fullmap |
| Object | Both the ingredient substance ("ATORVASTATIN CALCIUM") and its active moiety ("ATORVASTATIN") |
| Self edges | Drop rows whose product name equals the ingredient text, and drop any edge whose resolved subject CURIE equals the object CURIE |
| Scope | All DailyMed labels (Rx, OTC, animal, homeopathic, everything acquired today) |
| Provenance | Match the other families: `infores:drugapprovals-kp` primary, `knowledge_assertion`, `manual_validation_of_automated_agent` |
| Inactive | Ignored for now (no `has_excipient`) |
| Qualifiers | None. No `species_context_qualifier` or any other qualifier on these edges |

Biolink check: `has active ingredient` (is_a `has part`, RO:0002248) has domain `drug`
and range `molecular entity`. For every Drug/ChemicalEntity/SmallMolecule/MolecularMixture
pair Tablassert derives `ChemicalEntityToChemicalEntityAssociation`, and that class accepts
the predicate (checked with `tablassert.biolink.resolve_association_class`). The class holds
`publications`. It does **not** hold `regulatory_approvals` (see Decision D1).
These edges carry no qualifiers.

## Approach

### 1. Extraction: a product-level DailyMed table

Real SPL (2022+ shape, checked against `dm_spl_release_human_rx_part1.zip`):

```
manufacturedProduct/manufacturedProduct
  code   code="63187-050"  (NDC product code)
  name   "Lisinopril and hydrochlorothiazide"
  asEntityWithGeneric/genericMedicine/name
  ingredient classCode="ACTIB"            (ACTIB | ACTIM | ACTIR = active)
    ingredientSubstance  code=E7199S1YWR  name "LISINOPRIL"            -> kind=substance
      activeMoiety/activeMoiety  code=7Q3P4BS2FD name "LISINOPRIL ANHYDROUS" -> kind=moiety
  ingredient classCode="IACT"  (inactive, ignored)
```

The legacy 2006 shape (`manufacturedMedicine` + `activeIngredient/activeIngredientSubstance`)
is handled the same way. Kits (`partProduct`) attach each part's actives to the kit's
product name.

Add a new interim table `spl_products.parquet` (`spl_products.tsv` from Go) with these columns:
`source_record_id, spl_set_id, product_code, product_name, generic_name,
ingredient_name, ingredient_unii, ingredient_kind (substance|moiety), release_file, xml_path`.
It is emitted by the Go production parser (`go:dailymed`) and its Python mirror, and it
leaves the existing tables untouched, so no current shaper changes behavior.
`source_record_id` uses the existing `SourceRecordID(sourceID, "product", setID, code, name, unii, kind)`.

### 2. New edge family

- Table `has_active_ingredient_assertions`. It is text-only: no `subject_curie` /
  `object_curie` / `*_name` / `*_category` columns. Every subject and object identifier
  comes from Tablassert fullmap resolution of `subject_text` / `object_text`, and a row
  whose subject or object does not resolve is dropped by Tablassert (both encodings are
  non-nullable). Every emitted edge therefore carries fullmap CURIEs on both ends.
  Columns:
  `subject_text, predicate, object_text, ingredient_kind, FDA_regulatory_approvals,
  supporting_spl_sets, edge_evidence, knowledge_level, agent_type,
  primary_knowledge_source, upstream_resource_ids`.
  Column-to-KGX mapping (Tablassert config):
  `subject_text` -> `subject` (+ `original_subject`), `object_text` -> `object`
  (+ `original_object`), `edge_evidence` -> `publications` (split `|`).
  `knowledge_level` / `agent_type`
  / sources come from the table's provenance override. `ingredient_kind`,
  `FDA_regulatory_approvals`, and `supporting_spl_sets` stay TSV-only audit columns.
- Shaper `HasActiveIngredientShaper` (new `assertions/active_ingredients.py`), with no NER:
  - **DailyMed rows**: one row per (product_name, ingredient text), across all label
    types (human Rx/OTC, animal, homeopathic). The set's approvals come from
    `DailyMedEvidence.approval_ids_by_set`. No species attribution: animal labels emit
    the same unqualified edge, and no rows are dropped for lacking a species.
  - **Drugs@FDA rows**: `drug_name` paired with each component of `active_ingredient`, split
    on top-level `;` only. This keeps "TRIPLE SULFA (SULFABENZAMIDE;SULFACETAMIDE;...)"
    intact. Approvals come from `FDAApprovalIndex` (`NDA021687`).
  - **Self-edge text guard**: drop a row when `casefold(collapse_ws(product)) ==
    casefold(collapse_ws(ingredient))`, and count it (`dropped_self_named`).
  - **Aggregation**: key `(casefold(product), casefold(ingredient))`. Pipe-join
    provenance (sets, approvals, evidence CURIEs `dailymed:<set_id>`) with `sorted_pipe`, and
    keep the lexicographically first casing as `subject_text`/`object_text`. Sort
    deterministically.
- `EdgeFamily("has_active_ingredient", table, "biolink:has_active_ingredient",
  "knowledge_assertion", "manual_validation_of_automated_agent", ner_profile=None, ...)`.

### 3. Tablassert config: make the disease-specific globals per-table

`table_config()` hardcodes the disease shape: `SUBJECT_PRIORITIZE`, `OBJECT_PRIORITIZE`,
`OBJECT_CATEGORY_OVERRIDE`, `_TABLE_OBJECT_DENYLIST`, `_OBJECT_EXCLUDE_REGEX`. These become
per-table lookups. The three existing tables keep identical values, which the existing
config tests prove byte-for-byte. The new table gets:

- Subject allow-list: `Drug, ChemicalEntity, SmallMolecule, MolecularMixture`. Brand names
  resolve to UMLS `ChemicalEntity`, e.g. Lipitor = `UMLS:C0593906`. Subject
  `exclude_regex` = the shared `_SUBJECT_EXCLUDE_REGEX`.
- Object allow-list: `SmallMolecule, ChemicalEntity, MolecularEntity, MolecularMixture, Drug`.
  `Protein` is excluded because it derives `ChemicalGeneInteractionAssociation`;
  biologics like adalimumab already resolve to `ChemicalEntity` (`UNII:FYS6T7F842`).
- `category_override`: every allowed object category is pinned to
  `ChemicalEntityToChemicalEntityAssociation`.
- No disease object denylist / disease exclude regex.
- Qualifiers: none. `statement.qualifiers` is omitted for this table (`_TABLE_QUALIFIERS`
  entry is `()`), so `table_config()` must emit no `qualifiers` key when the tuple is empty.
  The graph-level `UUID_FIELDS` still lists the qualifier slots; on these edges they are
  always absent, so identity reduces to (subject, predicate, object). The existing
  test asserting "every table carries the species slot" gets an explicit exemption for
  this table.
- Annotations: `edge_evidence -> publications` (split `|`).
- Subject and object encodings are non-nullable (the Tablassert default), so a row
  with no fullmap match produces no edge. The shaper never writes a CURIE itself.
- Sources: DAKP primary (upstream `infores:dailymed`, `infores:drugs-at-fda`), plus
  supporting `infores:dailymed` and `infores:drugs-at-fda`. `infores:drugs-at-fda` is in
  the upstream Biolink information-resource-registry but missing from the local
  `InforesCatalog` copy.
- RIG: add a Drugs@FDA entry to `RIG_SUPPORTING_DATA_SOURCES`, since it now backs edges,
  and update the "NO Drugs@FDA entry" comment. Add a `DailyMed SPL product composition`
  entry to `RIG_INCLUDED_CONTENT`. Add `_TABLE_SOURCE_URLS` entries for the DailyMed
  index and `DRUGSFDA_DATA_FILES_URL`.
- Add the table to `_TABLE_SPECS` / `_TABLE_ORDER` / `_TABLE_SOURCES`, and update `GRAPH_DESCRIPTION`.

### 4. CURIE-level self-loop drop (DAKP post-build filter; no Tablassert change)

Two different wordings can resolve to one CURIE. For example, "Atorvastatin Calcium" and
"ATORVASTATIN CALCIUM" could both land on `UMLS:C0286650`. Tablassert PR #229 (released in
22.2.0) makes `build-kg --qc` *fail* on `self-loop-edges`; nothing in Tablassert drops them,
and no reindex filter can compare two resolved columns. Per review, no new Tablassert PR:
DAKP removes them itself, in `pipeline/src/dakp_pipeline/selfloops.py`.

`TablassertRunner.run` therefore builds the graph WITHOUT `--qc` whenever the composition
table is present, then, in order: `drop_self_loop_edges` (composition predicate only, plus
nodes orphaned by the removal) -> `reconcile_approval_status` -> `validate_kgx_contract` ->
Tablassert's own stage-7 study (`study_final_ndjson`, the same assertions `--qc` would have
applied) as a second subprocess over the filtered pair. A non-zero study exits the run as
failed. The shaper's text guard removes most loops before resolution; this catches the rest.
Handoff report fields: `self_loop_edges_dropped`, `orphan_nodes_dropped`, `study`.

### 5. DAG and contracts

- `assets.py`: `shape_has_active_ingredient` asset (deps: dailymed + drugsfda extracts),
  added to `SHAPE_STAGES` and the `tablassert_configs` / `kg_build` deps. The narrow job
  `dakp_build_shape_has_active_ingredient` is generated automatically.
- `translator.py`: add a `biolink:has_active_ingredient` entry to `EDGE_FAMILIES` /
  `FAMILY_INVARIANTS` (upstream `infores:dailymed` or the `infores:drugs-at-fda`
  alternative, `knowledge_assertion`, no clinical-approval-status constraint). Make the
  pinned-category and subject/object category compatibility checks per family:
  `ChemicalEntityToChemicalEntityAssociation` here, the disease classes elsewhere.

### Decision D1: regulatory approvals on the edge

`ChemicalEntityToChemicalEntityAssociation` refuses `regulatory_approvals`. Annotating it
would make `prune_to_class` fold the numbers into `supporting_text`. Plan: keep
`FDA_regulatory_approvals` in the TSV for audit and leave it **unannotated** in v1. If
you want application numbers on these edges, the alternative is a Tablassert
`CLASS_FIELD_OVERRIDES` grant (same precedent as the 18.x/19.x grants), done in the same
Tablassert PR as step 4.

## Example edges

Every edge below has the same KGX shape the 1.26.0 build produces
(`/tmp/dakp-1.26.0/DRUG_APPROVALS_KP_1.26.0.edges.ndjson`), with keys in the same order.
`subject` and `object` are always fullmap CURIEs, and `original_*` keeps the TSV text.
The CURIEs shown are the identifiers the 1.26.0 fullmap build already assigns to these
names (`/tmp/dakp-1.26.0/*.nodes.ndjson`). Edge `id`s are illustrative, since they derive
from `UUID_FIELDS` at build time, and so is the Lipitor set id.

### Example 1: one TSV row and the KGX edge it becomes (brand product to moiety)

Assertion TSV row (`tabular/has_active_ingredient_assertions.tsv`, shown as JSON). It has
no CURIE columns; Tablassert resolves `subject_text` / `object_text` through the fullmap.

```json
{
  "subject_text": "VYTORIN",
  "predicate": "biolink:has_active_ingredient",
  "object_text": "EZETIMIBE",
  "ingredient_kind": "moiety|substance",
  "FDA_regulatory_approvals": "NDA021687",
  "supporting_spl_sets": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=89b860d2-de83-468e-b7e8-76aeec2edca2",
  "edge_evidence": "dailymed:89b860d2-de83-468e-b7e8-76aeec2edca2",
  "knowledge_level": "knowledge_assertion",
  "agent_type": "manual_validation_of_automated_agent",
  "primary_knowledge_source": "infores:drugapprovals-kp",
  "upstream_resource_ids": "infores:dailymed|infores:drugs-at-fda"
}
```

The KGX edge built from that row:

```json
{
  "publications": ["dailymed:89b860d2-de83-468e-b7e8-76aeec2edca2"],
  "subject": "UMLS:C1527845",
  "original_subject": "VYTORIN",
  "object": "CHEBI:49040",
  "original_object": "EZETIMIBE",
  "predicate": "biolink:has_active_ingredient",
  "category": ["biolink:ChemicalEntityToChemicalEntityAssociation"],
  "knowledge_level": "knowledge_assertion",
  "agent_type": "manual_validation_of_automated_agent",
  "sources": [
    {
      "resource_id": "infores:drugapprovals-kp",
      "resource_role": "primary_knowledge_source",
      "upstream_resource_ids": ["infores:dailymed", "infores:drugs-at-fda"],
      "source_record_urls": ["https://linkouts.skyelanegoetz.com/edges/7d1f0c2e-3b9a-3c51-9e0f-2a6b8d4e1f00"]
    },
    {"resource_id": "infores:dailymed", "resource_role": "supporting_data_source"},
    {"resource_id": "infores:drugs-at-fda", "resource_role": "supporting_data_source"}
  ],
  "supporting_text": [
    "object_nlp_level: 1",
    "subject_nlp_level: 1"
  ],
  "id": "7d1f0c2e-3b9a-3c51-9e0f-2a6b8d4e1f00"
}
```

`VYTORIN` has a second row, `object_text: "SIMVASTATIN"`, which becomes the same edge
shape with `"object": "CHEBI:9150"`.

### Example 2: one product, both substance and moiety kept (two edges)

TSV rows: `Lipitor` / `ATORVASTATIN CALCIUM TRIHYDRATE` (`ingredient_kind: substance`)
and `Lipitor` / `ATORVASTATIN` (`ingredient_kind: moiety`).

```json
[
  {
    "publications": ["dailymed:c6e131fe-e7df-4876-83f7-9156fc4e8228"],
    "subject": "UMLS:C0593906",
    "original_subject": "Lipitor",
    "object": "UMLS:C0286650",
    "original_object": "ATORVASTATIN CALCIUM TRIHYDRATE",
    "predicate": "biolink:has_active_ingredient",
    "category": ["biolink:ChemicalEntityToChemicalEntityAssociation"],
    "knowledge_level": "knowledge_assertion",
    "agent_type": "manual_validation_of_automated_agent",
    "sources": [
      {
        "resource_id": "infores:drugapprovals-kp",
        "resource_role": "primary_knowledge_source",
        "upstream_resource_ids": ["infores:dailymed", "infores:drugs-at-fda"],
        "source_record_urls": ["https://linkouts.skyelanegoetz.com/edges/0b6c9a7e-51d2-3f0a-8c44-7e2d9b1a6c10"]
      },
      {"resource_id": "infores:dailymed", "resource_role": "supporting_data_source"},
      {"resource_id": "infores:drugs-at-fda", "resource_role": "supporting_data_source"}
    ],
    "supporting_text": [
      "object_nlp_level: 1",
      "subject_nlp_level: 1"
    ],
    "id": "0b6c9a7e-51d2-3f0a-8c44-7e2d9b1a6c10"
  },
  {
    "publications": ["dailymed:c6e131fe-e7df-4876-83f7-9156fc4e8228"],
    "subject": "UMLS:C0593906",
    "original_subject": "Lipitor",
    "object": "CHEBI:39548",
    "original_object": "ATORVASTATIN",
    "predicate": "biolink:has_active_ingredient",
    "category": ["biolink:ChemicalEntityToChemicalEntityAssociation"],
    "knowledge_level": "knowledge_assertion",
    "agent_type": "manual_validation_of_automated_agent",
    "sources": [
      {
        "resource_id": "infores:drugapprovals-kp",
        "resource_role": "primary_knowledge_source",
        "upstream_resource_ids": ["infores:dailymed", "infores:drugs-at-fda"],
        "source_record_urls": ["https://linkouts.skyelanegoetz.com/edges/5e8f3d21-a7c4-3b6e-9d02-1c4f7a8b2e33"]
      },
      {"resource_id": "infores:dailymed", "resource_role": "supporting_data_source"},
      {"resource_id": "infores:drugs-at-fda", "resource_role": "supporting_data_source"}
    ],
    "supporting_text": [
      "object_nlp_level: 1",
      "subject_nlp_level: 1"
    ],
    "id": "5e8f3d21-a7c4-3b6e-9d02-1c4f7a8b2e33"
  }
]
```

### Example 3: animal label (same shape, no species qualifier)

TSV row: `Clavamox` / `AMOXICILLIN TRIHYDRATE`, from a NADA-approved animal SPL set.
The edge is identical in shape to a human-label edge.

```json
{
  "publications": ["dailymed:3b1f6a2c-9d4e-4c7a-8b2f-0e5d6c7a8b9c"],
  "subject": "UMLS:C2732158",
  "original_subject": "Clavamox",
  "object": "UMLS:C0521900",
  "original_object": "AMOXICILLIN TRIHYDRATE",
  "predicate": "biolink:has_active_ingredient",
  "category": ["biolink:ChemicalEntityToChemicalEntityAssociation"],
  "knowledge_level": "knowledge_assertion",
  "agent_type": "manual_validation_of_automated_agent",
  "sources": [
    {
      "resource_id": "infores:drugapprovals-kp",
      "resource_role": "primary_knowledge_source",
      "upstream_resource_ids": ["infores:dailymed", "infores:drugs-at-fda"],
      "source_record_urls": ["https://linkouts.skyelanegoetz.com/edges/9a2b4c6d-8e0f-3a1b-8c3d-5e7f9a1b3c5d"]
    },
    {"resource_id": "infores:dailymed", "resource_role": "supporting_data_source"},
    {"resource_id": "infores:drugs-at-fda", "resource_role": "supporting_data_source"}
  ],
  "supporting_text": [
    "object_nlp_level: 1",
    "subject_nlp_level: 1"
  ],
  "id": "9a2b4c6d-8e0f-3a1b-8c3d-5e7f9a1b3c5d"
}
```

### Dropped (never emitted)

| Product | Ingredient | Why |
|---|---|---|
| `Acyclovir` | `ACYCLOVIR` (substance and moiety) | text self-name guard (shaper) |
| `Lisinopril` | `LISINOPRIL` (substance) | text self-name guard (shaper) |
| `Atorvastatin Calcium` | `atorvastatin calcium`, both resolving to `UMLS:C0286650` | CURIE self-loop drop (Tablassert) |
| any product | an ingredient with no fullmap match | unresolved object; no edge without a CURIE |
| an unresolvable product name | any | unresolved subject; no edge without a CURIE |

Kept: `Lisinopril` to `LISINOPRIL ANHYDROUS` (the moiety). The text differs, and the edge
ships only if the fullmap assigns it a different CURIE from the subject.

## Files to modify

- `dagster/projects/dakp/workers/go/internal/dailymed/dailymed.go` (+ `dailymed_test.go`, `testdata/hl7v3_spl.xml.gz`, `testdata/golden/spl_products.tsv`)
- `dagster/projects/dakp/workers/go/internal/worker/extract_dailymed.go` (`dailymedInterimOrder`)
- `dagster/projects/dakp/workers/go/internal/pipesutil/*.go` (`WorkerVersion` bump)
- `dagster/projects/dakp/pipeline/src/dakp_pipeline/extract/spl_xml.py` (parity mirror)
- `dagster/projects/dakp/pipeline/src/dakp_pipeline/io/schemas.py` (new table contract)
- `dagster/projects/dakp/pipeline/src/dakp_pipeline/assertions/active_ingredients.py` (new)
- `dagster/projects/dakp/pipeline/src/dakp_pipeline/assertions/evidence.py` (add `spl_products` loader, bump `EVIDENCE_BUILDER_VERSION` only if `DailyMedEvidence` changes)
- `dagster/projects/dakp/pipeline/src/dakp_pipeline/assertions/__init__.py` (`INFORES_DRUGS_AT_FDA`)
- `dagster/projects/dakp/pipeline/src/dakp_pipeline/families.py`
- `dagster/projects/dakp/pipeline/src/dakp_pipeline/tablassert.py`
- `dagster/projects/dakp/pipeline/src/dakp_pipeline/translator.py`
- `dagster/projects/dakp/pipeline/pyproject.toml` (tablassert floor)
- `dagster/projects/dakp/src/dakp_dag/assets.py`
- `../Tablassert` (separate PR): `Statement.drop_self_loops` (+ optional D1 grant)
- `AGENTS.md` / README: mention the fourth family

## Reuse

- `DailyMedEvidence` / `load_or_build_dailymed_evidence`: `approval_ids_by_set` (`assertions/evidence.py`)
- `build_fda_approval_index` / `FDAApprovalIndex.expand`, `normalize_nda` (`assertions/evidence.py`)
- `find_table`, `write_assertion_table`, `cached_shape_outputs`, `shape_operation_inputs`, `sorted_pipe`, `merge_unique`, `dailymed_set_url`, `dailymed_set_curie`, `edge_evidence_pipe` (`assertions/evidence.py`)
- `row_for`, `join_pipe`, `INFORES_*`, `KL_ASSERTION`, `AT_MANUAL` (`assertions/__init__.py`)
- `textnorm` collapse/casefold helpers for the self-name guard
- `_run_shape` + generated narrow jobs (`src/dakp_dag/assets.py`)
- `pipeline.SourceRecordID` (Go) / `spl_xml._source_record_id` (Python)

## Steps

- [x] ~~Tablassert PR~~ not needed (review): DAKP `selfloops.py` + deferred study stage; Tablassert floor unchanged at 22.2.0
- [x] Go: parse `manufacturedProduct` products + actives (ACTIB/ACTIM/ACTIR + legacy `activeIngredient`, substance + moiety, kits); emit `spl_products.tsv`; extend fixture + golden; bump `WorkerVersion`
- [x] Python `spl_xml.py` mirror + parity test against the Go golden
- [x] `schemas.py`: `HAS_ACTIVE_INGREDIENT_COLUMNS` + `ASSERTION_TABLES` entry
- [x] `assertions/active_ingredients.py`: shaper (DailyMed + Drugs@FDA, split, self-name guard, aggregation; no qualifier columns) + unit tests
- [x] `families.py`: register family; update `test_families.py`
- [x] `tablassert.py`: per-table category guards/override/denylists, specs, sources, URLs, RIG entries; extend `TABLES` in `test_tablassert_configs.py` and assert the other three configs are unchanged
- [x] `translator.py`: family + invariants, per-family pinned categories; tests
- [x] `assets.py`: asset, `SHAPE_STAGES`, deps; `uv run dg check defs`
- [x] Docs: AGENTS.md "Migration status" / README family list

## Verification

1. `npx nx affected -t test` (python + go, cached) — PASS.
2. `cd dagster/projects/dakp/workers/go && go test ./internal/dailymed/...` (golden + parity) — PASS;
   Go/Python `spl_products` output also diffed byte-for-byte on 29 real SPLs from
   `dm_spl_release_human_rx_part1.zip` (legacy, modern, kit) — identical.
3. `cd dagster/projects/dakp/pipeline && uv run --with 'tablassert[cli,log,qc]==22.2.0' pytest` — PASS.
4. `cd dagster/deployments/local && uv run dg check defs` — PASS (22 assets).
5. Mock workdir: `uv run dagster job execute -j dakp_build_shape_has_active_ingredient -m dakp_dag.definitions`,
   then inspect `tabular/has_active_ingredient_assertions.tsv` (no self-named rows, both kinds present,
   animal fixture rows present and shaped like human rows).
6. NOT RUN HERE (no fullmap on this host). Full build with `DAKP_FULLMAP` set and `DAKP_TABLASSERT_QC=1`: `kg_build` + `kg_qc` pass (no
   `self-loop-edges`, no bare `biolink:Association`). Spot-check:
   `jq -c 'select(.predicate=="biolink:has_active_ingredient")' kg.edges.ndjson | head`,
   then confirm Vytorin to ezetimibe/simvastatin, Lipitor to atorvastatin calcium/atorvastatin, and
   `jq 'select(.subject==.object)'` is empty. Also confirm every family edge has prefixed
   CURIEs on both ends: `jq 'select(.predicate=="biolink:has_active_ingredient" and ((.subject|test("^[A-Za-z.]+:")|not) or (.object|test("^[A-Za-z.]+:")|not)))'` is empty,
   and both endpoints exist in `kg.nodes.ndjson` (the `kg_qc` dangling check). No
   qualifier on any family edge: `jq 'select(.predicate=="biolink:has_active_ingredient") | keys[] | select(endswith("_qualifier"))'` is empty. Confirm the three existing families' edge counts are unchanged.

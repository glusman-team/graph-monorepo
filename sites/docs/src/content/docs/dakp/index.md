---
title: DAKP dataset and sources
description: Approved treatments, observed uses, contraindications, provenance, releases, and KGX downloads.
sidebar:
  order: 0
---

DAKP, the Drug Approvals Knowledge Provider, is a KGX knowledge graph of FDA and EMA approved drug-disease treatment relationships, observed applied-to-treat uses from FAERS and Health Canada's Canada Vigilance database, and contraindications text-mined from DailyMed Structured Product Labels. It is built with Tablassert and validated against the Biolink Model.

**Live dataset:** [SkyeAv/drug-approvals-kp on Hugging Face](https://huggingface.co/datasets/SkyeAv/drug-approvals-kp). The live dataset page is authoritative for the newest release.

## Documented release: 1.25.1

The release documented at this migration has 16,427 nodes, 136,317 edges, zero validation failures, and Biolink Model 4.4.6. Every release publishes an NDJSON node/edge pair and a Resource Ingest Guide (RIG). Older releases remain downloadable through `v<version>` dataset tags even though `main` carries only the latest.

| Version |  Nodes |   Edges | Biolink | Tag       |
| ------- | -----: | ------: | ------- | --------- |
| 1.25.1  | 16,427 | 136,317 | 4.4.6   | `v1.25.1` |
| 1.25.0  | 16,453 | 136,504 | 4.4.6   | `v1.25.0` |

Version 1.25.1 adds the ICD-10-CM screen, so its counts are lower than 1.25.0. The 1.25.x line is built by the Dagster port in this repository; earlier releases used the original Airflow project.

## Sources

| Source                                                                                                                                                                                                               | Contribution                                                                                                                         |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| [DailyMed](https://dailymed.nlm.nih.gov/dailymed/spl-resources-all-drug-labels.cfm) (`infores:dailymed`)                                                                                                             | SPL indications (LOINC 34067-9), contraindications (34070-3), boxed warnings, warnings and precautions, and FDA application numbers. |
| [FAERS](https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html) (`infores:faers`)                                                                                                                           | Quarterly drug and indication case pairs and counts for `biolink:applied_to_treat`.                                                  |
| [Drugs@FDA](https://www.fda.gov/media/89850/download)                                                                                                                                                                | Approval backing for indications.                                                                                                    |
| [EMA medicines report](https://www.ema.europa.eu/en/documents/report/medicines-output-medicines-report_en.xlsx) (`infores:ema`)                                                                                      | Centrally authorised human medicines, MeSH therapeutic areas, EPAR indication text, and product numbers.                             |
| [Canada Vigilance](https://www.canada.ca/en/health-canada/services/drugs-health-products/medeffect-canada/adverse-reaction-database/canada-vigilance-online-database-data-extract.html) (`infores:canada-vigilance`) | Monthly observed-use evidence, not Canadian approval evidence.                                                                       |
| [ICD-10-CM Table of Drugs and Chemicals](https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/2027/icd10cm-table-and-index-2027.zip) (`infores:icd10cm`)                                              | FY2027 non-therapeutic subject screen for observed-use filtering. It contributes no assertion text.                                  |

## ICD-10-CM poison screen

The ICD-10-CM table marks substances with poisoning and adverse-effect codes. A substance whose Adverse-effect column is empty has no use under proper administration under Official Guideline I.C.19.e, so it must not carry an applied-to-treat claim. Every `biolink:applied_to_treat` edge cites `infores:icd10cm` as a supporting data source.

The pipeline derives two screens on every build through `derive_icd10cm_screen` and `derive_icd10cm_curies`:

- **Wording:** observed-use rows whose subject text names a screened substance in the T36-T65 ranges are dropped at load by one Tablassert anti-join filter.
- **Identifier:** subject resolution excludes identifiers whose own name is a screened substance. A reporter spelling that resolves to a poison, such as FAERS `LEVOXINE` resolving to hydrazine, or a label ingredient such as turpentine cannot surface as a node on any predicate.

Substances that regulator label text names as medicines are exempt, including lithium, zinc, arsenic trioxide, and permethrin. Water-as-substance identifiers and wordings are excluded separately. GHB and GBL (sodium oxybate, an approved narcolepsy medicine) are deliberately not screened. Each release RIG records the full scope and rationale.

## Download KGX

```sh
BASE=https://huggingface.co/datasets/SkyeAv/drug-approvals-kp/resolve/main/1.25.1
curl -LO "$BASE/DRUG_APPROVALS_KP_1.25.1.edges.ndjson"
curl -LO "$BASE/DRUG_APPROVALS_KP_1.25.1.nodes.ndjson"
```

With the Python `datasets` library:

```python
from datasets import load_dataset

edges = load_dataset("SkyeAv/drug-approvals-kp", "1.25.1_edges")
```

Every edge carries provenance through `sources`, rooted at `infores:drugapprovals-kp`, plus evidence fields where applicable: `number_of_cases`, `regulatory_approvals`, `publications`, and context qualifiers. `species_context_qualifier` identifies the organism, using `NCBITaxon:9606` for human edges and veterinary taxa for animal-drug approvals.

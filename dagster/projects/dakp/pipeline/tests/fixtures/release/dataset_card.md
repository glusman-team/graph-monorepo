---
license: apache-2.0
pretty_name: Drug Approvals Knowledge Provider (DAKP)
---
# Drug Approvals Knowledge Provider (DAKP)

DAKP is a knowledge graph of drug–indication relationships built from drug labels,
approval data, post-marketing adverse-event reports, and the ICD-10-CM Table of
Drugs and Chemicals. It is distributed as KGX NDJSON plus the emitted Release
Information Graph (RIG).

## Release 1.27.1

**3 nodes · 2 edges · 0.0 MB · built 2026-11-07 · validation PASS**

- Chemical subjects (CHEBI): **2**
- Disease objects (MONDO): **2**
- Human-qualified contraindication edges: **1**
- Edges carrying supporting-study evidence: **1**
- Tablassert: **22.2.0-test** · Biolink: **4.4.6**
- Previous published version in local release state: **1.27.0**

### What changed in 1.27.1

New data for 1 source on 2026-11-04: DailyMed (2026-11-04).

- **DailyMed** — 2026-11-04; 2 acquired artifacts.

**Graph delta:** +1 nodes / +1 edges vs v1.27.0.

Only releases with at least one changed upstream source artifact are built and
published. Re-running the weekly check against identical BLAKE3 source
fingerprints does not build, increment the version, or upload anything.

## Release contents

| File | Bytes | Human size | MD5 |
|---|---:|---:|---|
| `DRUG_APPROVALS_KP_1.27.1.nodes.ndjson` | 240 | 0.0 MB | `e361ae6ad583435e67d8ffcfa2a56ecf` |
| `DRUG_APPROVALS_KP_1.27.1.edges.ndjson` | 355 | 0.0 MB | `75313c70ad18e91b1cac3f3196196ec4` |
| `DRUG_APPROVALS_KP_1.27.1.RIG.yaml` | 68 | 0.0 MB | `f4c9eac6c9f1c03c2f358573efe34a3a` |

Load exactly this release without depending on `main`:

```python
from huggingface_hub import hf_hub_download

repo = "SkyeAv/drug-approvals-kp"
nodes = hf_hub_download(repo, "1.27.1/DRUG_APPROVALS_KP_1.27.1.nodes.ndjson", repo_type="dataset", revision="v1.27.1")
edges = hf_hub_download(repo, "1.27.1/DRUG_APPROVALS_KP_1.27.1.edges.ndjson", repo_type="dataset", revision="v1.27.1")
rig = hf_hub_download(repo, "1.27.1/DRUG_APPROVALS_KP_1.27.1.RIG.yaml", repo_type="dataset", revision="v1.27.1")
```

The `v1.27.1` dataset tag is immutable. The version directory on `main` is the
same content; older version directories are pruned from `main` only after their
tag has been confirmed.

## Actual graph composition

These tables are computed by streaming the NDJSON files in this release, not from
a previous card or a hand-maintained count.

### Nodes by Biolink category

| Category | Nodes |
|---|---:|
| biolink:ChemicalEntity | 1 |
| biolink:Disease | 1 |
| biolink:OrganismTaxon | 1 |

### Edges by predicate

| Predicate | Edges |
|---|---:|
| biolink:approved_for_treatment | 1 |
| biolink:contraindicated_for | 1 |

### Nodes by identifier namespace

| Namespace | Nodes |
|---|---:|
| CHEBI | 1 |
| MONDO | 1 |
| NCBITaxon | 1 |

## Source artifacts in this build

Dates come from upstream `Last-Modified` metadata when available, otherwise from
the acquisition retrieval timestamp. Artifact counts describe the content-addressed
refs acquired for this release.

| Source | Artifacts | Upstream date(s) | Retrieval date(s) |
|---|---:|---|---|
| DailyMed | 2 | 2026-11-04 | 2026-11-06 |

## Build process

DAKP releases are produced by the Dagster code location in
`glusman-team/graph-monorepo`, on `main`:

1. **Friday 17:00 America/Los_Angeles:** `dakp_release_check` validates the
   fullmap, extraction worker, fixture data, tokens, disk headroom, unbounded
   release settings, NER-cache health, and that `HEAD == origin/main` before any
   acquisition. Source acquisition uses freshness gates (7 days for fast-moving
   sources and discovery indexes; 30 days for monthly Canada Vigilance and the
   EMA product-information crawl). Manual warm builds can use cached-only mode.
2. **Change detection:** each source's acquired refs are reduced to a sorted
   BLAKE3 fingerprint and compared with the last successfully published release.
   No changed fingerprints means no build, version increment, or upload.
3. **GPU gate:** an hourly sensor waits up to 24 hours for every GPU to report at
   most 1024 MiB used. The build launches once, with the check run as its
   idempotence key. A timeout files a GitHub issue instead of building.
4. **Versioning:** the next version is the highest `vX.Y.Z` dataset tag with the
   patch component incremented. It travels as an immutable Dagster run tag; the
   Python package version is independent.
5. **Build:** the same acquisition/extraction/assertion/Tablassert graph used by
   manual builds produces the KGX pair and RIG. NER mining is lazy, cached in the
   managed Pebble service, device-pinned, and shut down after each mining stage.
6. **Verification:** `tablassert validate-kgx`, QC counts, dangling-edge checks,
   screened-CURIE checks, and RIG release-marker checks run against the actual
   outputs. Validation for this release is **PASS**:

```text
KGX output is Biolink-compliant
```

7. **Card and publication:** this card is generated from the staged NDJSON/RIG
   outputs. Publication uploads the version directory, commits this card, prunes
   superseded directories only after their tags exist, creates `v1.27.1`, and
   verifies uploaded checksums. Release state updates only after all steps pass.
8. **Failure reporting:** failed release jobs open or update a
   `DAKP build failed` GitHub issue with the run, failed steps, timings, and
   truncated logs.

## Releases

Latest first. Every listed version remains loadable through its dataset tag.

| Version | Nodes | Edges | File size | Files |
|---|---:|---:|---:|---|
| v1.27.1 | 3 | 2 | 0.0 MB | [nodes](1.27.1/DRUG_APPROVALS_KP_1.27.1.nodes.ndjson), [edges](1.27.1/DRUG_APPROVALS_KP_1.27.1.edges.ndjson), [RIG](1.27.1/DRUG_APPROVALS_KP_1.27.1.RIG.yaml) |
| v1.27.0 | 2 | 1 | 1.0 MB | [nodes](old) |

## KGX record shape

Nodes carry `id`, `name`, `category`, and `description`. Edges carry `subject`,
`predicate`, `object`, evidence and source fields, optional qualifiers, and
stable identifiers generated by Tablassert. Qualifiers such as
`biolink:species_context_qualifier` are represented in the Biolink qualifier
list. RIG metadata records source provenance, versioning, contributors, license,
and the Biolink model.

## Reproduce

Use the source tree at the release's `main` commit and the exact tagged output
above. A warm on-demand build can set `DAKP_SOURCE_REFRESH=cached`; scheduled
publication always uses `DAKP_SOURCE_REFRESH=auto`, rejects source limits and
offline NER, and requires a clean checkout equal to `origin/main`.

## License and upstream terms

The dataset card and generated release metadata use Apache-2.0. DailyMed,
Drugs@FDA, and FAERS are US-government public-domain data. EMA content is reused
under the European Medicines Agency public-reuse policy with acknowledgement.
Canada Vigilance and CDC/NCHS ICD-10-CM remain subject to their respective
upstream terms. This graph is a research knowledge artifact, not medical advice.

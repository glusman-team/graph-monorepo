$front_matter
# Drug Approvals Knowledge Provider (DAKP)

DAKP is a knowledge graph of drug–indication relationships built from drug labels,
approval data, post-marketing adverse-event reports, and the ICD-10-CM Table of
Drugs and Chemicals. It is distributed as KGX NDJSON plus the emitted Release
Information Graph (RIG).

## Release $version

**$nodes nodes · $edges edges · $human_bytes · built $date · validation $validation**

- Chemical subjects (CHEBI): **$chemicals**
- Disease objects (MONDO): **$diseases**
- Human-qualified contraindication edges: **$species**
- Edges carrying supporting-study evidence: **$study_backed**
- Tablassert: **$tablassert_version** · Biolink: **$biolink_version**
- Previous published version in local release state: **$previous_version**

### What changed in $version

$delta_message

$change_bullets

**Graph delta:** $graph_deltas.

Only releases with at least one changed upstream source artifact are built and
published. Re-running the weekly check against identical BLAKE3 source
fingerprints does not build, increment the version, or upload anything.

## Release contents

| File | Bytes | Human size | MD5 |
|---|---:|---:|---|
$file_rows

Load exactly this release without depending on `main`:

```python
from huggingface_hub import hf_hub_download

repo = "SkyeAv/drug-approvals-kp"
nodes = hf_hub_download(repo, "$version/$nodes_file", repo_type="dataset", revision="v$version")
edges = hf_hub_download(repo, "$version/$edges_file", repo_type="dataset", revision="v$version")
rig = hf_hub_download(repo, "$version/$rig_file", repo_type="dataset", revision="v$version")
```

The `v$version` dataset tag is immutable. The version directory on `main` is the
same content; older version directories are pruned from `main` only after their
tag has been confirmed.

## Actual graph composition

These tables are computed by streaming the NDJSON files in this release, not from
a previous card or a hand-maintained count.

### Nodes by Biolink category

| Category | Nodes |
|---|---:|
$category_rows

### Edges by predicate

| Predicate | Edges |
|---|---:|
$predicate_rows

### Nodes by identifier namespace

| Namespace | Nodes |
|---|---:|
$namespace_rows

## Source artifacts in this build

Dates come from upstream `Last-Modified` metadata when available, otherwise from
the acquisition retrieval timestamp. Artifact counts describe the content-addressed
refs acquired for this release.

| Source | Artifacts | Upstream date(s) | Retrieval date(s) |
|---|---:|---|---|
$source_rows

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
   outputs. Validation for this release is **$validation**:

```text
$validator_output
```

7. **Card and publication:** this card is generated from the staged NDJSON/RIG
   outputs. Publication uploads the version directory, commits this card, prunes
   superseded directories only after their tags exist, creates `v$version`, and
   verifies uploaded checksums. Release state updates only after all steps pass.
8. **Failure reporting:** failed release jobs open or update a
   `DAKP build failed` GitHub issue with the run, failed steps, timings, and
   truncated logs.

## Releases

Latest first. Every listed version remains loadable through its dataset tag.

| Version | Nodes | Edges | File size | Files |
|---|---:|---:|---:|---|
$release_rows

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

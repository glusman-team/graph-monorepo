---
title: Add a KG display
description: Inspect real KGX, create a compiled display config, and handle field drift across releases.
sidebar:
  order: 0
---

A KG gets readable pages through a display config in `sites/linkouts/kgs/`. A config is an Elixir file containing plain data, not functions, templates, or HTML. An unconfigured KG still renders through the default config.

## 1. Inspect real data

Run `linkouts_inspect_kgx` first with `emit_config: false` to report predicates, fields, and frequencies:

```yaml
ops:
  inspect_kgx:
    config:
      edges_path: /data/my-kp/edges.ndjson
      limit: 20000
      emit_config: false
```

Then run the same narrow job with `emit_config: true`. Its `display-starter.exs` artifact lists observed predicates and evidence sentences gated on field presence. Lists of CURIEs are linked. Copy the reviewed artifact to `sites/linkouts/kgs/my_kg.exs`; do not deploy an unreviewed starter.

## 2. Configure identity and inheritance

Set `name` to the canonical infores name used by release keys. For `infores:my-kp-1.0.0`, use:

```elixir
%{
  name: "infores:my-kp",
  extends: "default",
  display_name: "My Knowledge Provider",
  # ...
}
```

The name without the prefix is the slug `my-kp`, used in stored documents, the root page, and `/my-kp/random`.

`extends: "default"` inherits generic names, categories, provenance, qualifiers, and sources. Slots merge by name, aliases append, and other declared keys replace their defaults. See the [default-config guide](../default-config/).

Use the monorepo issue form for corrections:

```elixir
feedback_repo: "https://github.com/glusman-team/graph-monorepo"
```

Phoenix adds the edge ID and permalink to a `linkouts_bug.yml` issue URL.

## 3. Write the relationship in the KG's voice

The starter says "X predicate Y". Use `{:pick, ...}` when wording depends on the predicate:

```elixir
slots: %{
  "subject" => {:link, "subject_name", "subject"},
  "object" => {:link, "object_name", "object"},
  "relation" =>
    {:pick, "predicate",
     %{
       "biolink:treats" => "has been approved for treating",
       :default => "has been used for treating"
     }}
},
edge: "This relationship states that {subject} {relation} {object}."
```

Delete evidence sentences reviewers do not need and rewrite the rest as prose. A missing field must drop its clause rather than leave an empty frame.

## 4. Validate and test

```sh
cd sites/linkouts
MIX_ENV=test mix linkouts.check
mix test --warnings-as-errors
```

`mix linkouts.check` reports all schema problems with file locations and compares referenced field names against the canonical contract documents. Compilation runs the same validation, so a malformed config fails the build.

Add focused fixtures only to the canonical Go-generated corpus. Do not create a second Phoenix-specific copy.

## Handle a renamed source field

Keep old releases readable by adding an alias instead of replacing the old spelling:

```elixir
aliases: [
  %{field: "regulatory_approvals", as: "FDA_regulatory_approvals"}
]
```

Use `regulatory_approvals` throughout the config. Documents are read with the canonical name first and alias second.

Add `versions: "<2.0.0"` only when a rename occurred at a clean version boundary. Drug Approvals has different spellings within the same release, so its alias deliberately has no version range.

## Worked examples

Read `sites/linkouts/kgs/drug_approvals.exs` beside `kgs/_default.exs`, the [default-config guide](../default-config/), and the [configuration reference](../config-reference/). The configs and tests cover real stored documents, legacy field drift, missing values, and safe link rendering.

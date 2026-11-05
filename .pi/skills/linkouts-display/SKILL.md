---
name: linkouts-display
description: Add or adjust a Linkouts knowledge-graph display config in sites/linkouts/kgs/, with real KGX evidence, compiled validation, aliases for field drift, and Phoenix page review. Use when the user says add a KG, show fields differently, rename a KG label, or fix a wrong sentence on an edge page.
---

# Linkouts display configs

## Principles

- A display is DATA, not code: one declarative `sites/linkouts/kgs/<kg>.exs`
  file, no functions, templates, or HTML. `_default.exs` is the generic KGX
  fallback; a KG file only declares its specifics and `extends: "default"`.
- Evidence accuracy outranks presentation. Never guess a field name; inspect
  real decoded documents before writing pickers.
- Phoenix derives everything server-side (names, ordering, counts); the config
  says how an assertion READS, not how the page is laid out. Layout changes
  belong in the LiveView/design system (`DESIGN.md`), not in `kgs/`.

## Workflow

1. Gather real input: the KGX `edges.ndjson`/`nodes.ndjson` release, or decoded
   stored documents. Useful offline corpus:
   `dagster/projects/linkouts/contracts/docs.ndjson` (canonical bytes).
   To decode: the worker `get` operation with `raw: true` (file store), or the
   Elixir `EdgeLinkouts.Codec` functions in a `mix run` - never a hand-rolled
   reimplementation.
2. Start from the machine draft: run the narrow `rig` operation (inspect KGX and
   emit a starter config). Keep only what is genuinely specific to the KG.
3. Write `kgs/<kg>.exs`:
   - `extends: "default"` unless there is a reason not to;
   - slug/name/labels and release naming if they differ from defaults;
   - `relation` pickers per predicate (`{:pick, "predicate", ...}`) so each
     predicate reads as a sentence; use `subject`/`object` role sentences only
     when the KG truly requires them;
   - evidence sentences for the fields that matter; omit - do not blank -
     absent fields;
   - aliases (`{:"field-name", "known_alt"}`) where a source renamed a field.
4. Validate offline:

   ```sh
   cd sites/linkouts
   mix compile --warnings-as-errors
   mix test edge_linkouts/display_test.exs
   mix test test/contract_test.exs     # canonical corpus still byte-agreed
   make linkouts-web-test              # from repo root
   ```

   Display-config compilation is strict: unknown keys, bad pickers, and
   non-HTTP(S) links fail here, not in production.
5. Eyeball real pages: point Phoenix at the file store or contract corpus
   (`COSMOS_BACKEND=file COSMOS_DOCS=... mix phx.server`), then visit `/`,
   `/<kg>/random`, `/random`, and one known edge. Check both light and dark
   themes and the per-release diff.
6. Update the docs guide (`sites/docs/src/content/docs/linkouts/display/`) only
   if you added a capability; run `make docs-verify` from the root.

## Rules

- No raw HTML, no `raw` assignments, no JavaScript in configs. Escaped text and
  safe links only; the compiled config enforces HTTP(S).
- Every new alias needs a comment saying which release introduced the drift,
   and a fixture exercising it.
- Field drift is handled by ALIASES, not by changing `EdgeLinkouts.Codec`'s
  canonical wire format (that would invalidate every stored blob).
- A KG display that cannot express the assertion accurately is a bug report
   against the pipeline, not a creative-writing exercise.
- Terminology is fixed: `infores:` canonical names, KG slugs, version keys.
   URLs use the slug; stored documents use the slug; display labels may differ.

---
title: Type checking
description: Dialyzer enforcement for the Phoenix reader - what is checked, how to run it, and how warnings are handled.
sidebar:
  order: 4
---

The Phoenix reader (`sites/linkouts`) is statically type checked in CI the way pyright checks Python: dialyxir (Dialyzer) validates `@spec` contracts across modules, and the Elixir 1.19 compiler's built-in type checking validates calls at compile time. Both are gates, not suggestions: CI fails on any warning.

## What is enforced

- `mix dialyzer` with `flags: [:unmatched_returns, :error_handling]` (see the `dialyzer` key in `sites/linkouts/mix.exs`). dialyxir enables nothing by default, so the flags are explicit; any warning fails the run with a nonzero exit.
- `mix compile --warnings-as-errors` in the `lint` target covers the compiler's own type checking of spec'd calls.
- Every public function in `lib/edge_linkouts/` (the domain layer) carries an `@spec`. Behaviour-callback implementations (`@impl true`) are exempt: the behaviour's callback already carries the contract.
- `:underspecs` is deliberately off. It flags the intentional encode-any-term specs (`EdgeLinkouts.Codec.canonical/1`, `Display.Value.stringify/1`) whose uncovered types are meant to raise `FunctionClauseError` loudly. The rationale lives in the `mix.exs` dialyzer config comment.

## Run it

```sh
make typecheck                    # nx-cached dialyzer for linkouts-web (part of make check)
cd sites/linkouts && mix dialyzer # directly; first run builds the PLT into priv/plts/
```

CI runs the same analysis in the `linkouts-web` job: `npx nx run-many -t test lint dialyzer --projects=linkouts-web --parallel=1`. The PLT is cached against the `flake.lock` and `mix.lock` hashes, so only toolchain or dependency changes pay the rebuild.

## When dialyzer reports a warning

1. Fix the code. Warnings are almost always a real ignored error return, an unhandled error path, or a spec that lies about the success typing.
2. Add or tighten `@spec`s so the contract matches what the function actually does.
3. Silence a warning only when it is a verified false positive, via `.dialyzer_ignore.exs` (set `ignore_warnings` in `mix.exs`). Every entry needs a comment justifying it, and `--list-unused-filters` must stay empty in CI. The repo currently has no ignore file; keep it that way unless a false positive truly cannot be fixed.

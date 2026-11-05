# Linkouts wire-contract corpus

This is the single canonical stored-document corpus shared by the Go writer, Elixir reader, display tests, and credential-free preview deployment. Do not copy it into language-specific test directories.

- `docs.ndjson`: two releases, full entries and deltas, pool documents, and pool index.
- `dictdocs.ndjson`: the first release encoded with `fixture.dict`.
- `drift.ndjson`: real older KGX slot shapes.
- `unresolvable.ndjson`: malformed/unresolvable references used by reader tests.
- `fixture.dict`: deterministic dictionary trained from the small KGX corpus.

Inputs live in `../fixtures/kgx/`. The Go golden test regenerates every output into a temporary directory and compares bytes:

```sh
cd dagster/projects/linkouts/workers/go
go test ./internal/contract
```

After an intentional wire change, regenerate and review both writer and reader behavior:

```sh
LINKOUTS_UPDATE_CONTRACTS=1 go test ./internal/contract
cd ../../../../../..
cd sites/linkouts
mix test test/contract_test.exs --warnings-as-errors
```

Fixtures contain public/synthetic data and need no Cosmos or Fly credentials. Keep every test on the fake/file backends.

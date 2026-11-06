# Linkouts (Phoenix reader)

Dynamic Phoenix LiveView app rendering permanent, human-readable pages for
knowledge-graph edges: `/edges/:uuid`, `/random`, `/<kg>/random`, and the KG
index at `/`.

This is the reader only. Ingestion is the manually launched Dagster pipeline in
`dagster/projects/linkouts/` plus its hidden Go Pipes worker. Docs live on the
monorepo documentation site:

- Product/design context: `PRODUCT.md`, `DESIGN.md`
- Guides and runbooks: https://glusman-team.github.io/graph-monorepo/
- Display configs: `kgs/*.exs` (see the display guide on the docs site)

## Local preview (no credentials)

```sh
mix setup
COSMOS_BACKEND=file \
COSMOS_DOCS=../../dagster/projects/linkouts/contracts/docs.ndjson \
  mix phx.server
```

Then open http://localhost:4000.

Prefer the repository-root targets (`make linkouts-web-setup`,
`make linkouts-preview`, `make linkouts-web-test`, `make linkouts-web-build`);
they set the same paths consistently. Tests always use the fake/file backends -
never a real Cosmos account.

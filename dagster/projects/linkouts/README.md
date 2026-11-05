# Linkouts Dagster project

`linkouts_dag` is the manually triggered Linkouts ingestion code location. It consumes KGX and publishes the compressed serving index read by `sites/linkouts`; it does not publish a new knowledge graph.

`pipeline/` owns Dagster-free operation and artifact contracts. `workers/go/` owns the real ingestion implementation and its hidden Dagster Pipes worker.

## Local setup

```sh
go build -o bin/linkouts-worker ./cmd/linkouts-worker  # from workers/go/
uv sync --group dev                                   # from this directory
uv run dg dev --project .
```

Launch `linkouts_upload` for staging plus publication, or a narrow job for one asset. Run-specific inputs use Dagster's typed launch configuration. Credentials are read only from the worker environment and are never accepted in run configuration.

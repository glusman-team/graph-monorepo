# linkouts-pipeline

Dagster-free contracts for the Linkouts Go worker. This package owns typed operation payloads, content-addressed staging manifests, and artifact-reference JSON. It never loads Dagster or handles credentials.

The `linkouts_dag` code location translates Dagster run configuration into these models. The hidden Go worker consumes the same JSON through Dagster Pipes.

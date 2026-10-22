# dakp-pipeline

DAKP pipeline core, ported from ISB/DAKP v1.25.0 for the graph-monorepo Dagster
deployment. Orchestrator-free: stages communicate through BLAKE3 content-addressed
`ArtifactRef`s and the operation index (`manifests/_index.json`). The Dagster code
location (`../src/dakp_dag`) wraps these stages as assets.

- Layout, conventions, commands: see the monorepo root AGENTS.md.
- Source of truth for behavior: DAKP v1.25.0 (tests are ported alongside the code).

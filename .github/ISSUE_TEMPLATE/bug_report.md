---
name: Bug report
about: Report a reproducible problem in the graph-monorepo pipelines
title: "[Bug]: "
labels: bug
assignees: ""
---

## Description
<!-- Briefly describe the problem and its impact. -->

## Steps to Reproduce
1. <!-- First step, including any setup or fixture details. -->
2. <!-- Next step leading to the failure. -->
3. <!-- First observable failure or incorrect result. -->

## Command or Run Configuration
<!-- Include the exact command, such as `make dev`, `npx nx test dakp`, or the materialize selection. -->

## Code Location and Stage
<!-- Check where the problem appears, if known. -->
- [ ] `dagster/projects/dakp` (Dagster port of DAKP)
- [ ] Acquisition
- [ ] Go extraction worker (`workers/go`)
- [ ] Disease NER
- [ ] Aggregation / assertions
- [ ] KGX KG build
- [ ] Dagster orchestration (`dg dev` / process-compose / daemon)
- [ ] Other (describe below):

## Worker Language (if worker-related)
- [ ] Python (libs or project defs)
- [ ] Go (`workers/go`)
- [ ] Scala / JVM (`workers/scala`, future)
- [ ] Not worker-related

## Data Source
<!-- Which input dataset, paper set, or fixture, if relevant. -->

## Logs or Output
<!-- Paste relevant output. Keep it trimmed to what matters. -->

## Environment
- OS / nix version:
- How you enter the env (`nix develop`, direnv, bare shell):

## Additional Context
<!-- Anything else that helps reproduce or scope the bug. -->

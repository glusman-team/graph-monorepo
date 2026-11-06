## Summary

<!-- What changed, why, and which user task it serves. -->

## Components

- [ ] DAKP (`dagster/projects/dakp`)
- [ ] Linkouts Python/Dagster (`dagster/projects/linkouts`)
- [ ] Linkouts Go worker/contracts
- [ ] Linkouts Phoenix/display/deployment (`sites/linkouts`)
- [ ] Shared Python libraries
- [ ] Documentation (`sites/docs`)
- [ ] Tooling, hooks, Nix, or CI

## Verification

<!-- Exact commands, results, host, and CPU limit. -->

- Commands/results:
- Regression and failure-path tests:
- Contract/fixture byte changes:
- Generated-link/content review:
- Removed code/dependencies and reason:

## Safety

- [ ] No secrets, production data, or credentials entered source, run config, Pipes metadata, or logs.
- [ ] No real Cosmos/Fly/public-dataset mutation was part of verification.
- [ ] Normal uploads remain manual; destructive jobs remain separately guarded.
- [ ] Relevant narrow-job and shared-contract fan-out tests pass.
- [ ] `prek run --all-files --hook-stage pre-commit` passes.
- [ ] `prek run --all-files --hook-stage pre-push` passes.
- [ ] Documentation and agent context match the implementation.

## Related issues

<!-- Link issues or state none. -->

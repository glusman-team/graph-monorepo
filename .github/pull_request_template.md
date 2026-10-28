# Pull Request Template

## Summary
<!-- One-line summary of the change -->

## Changes

- **What was changed**
- **Why this change is needed**
- **How it affects the graph or pipeline**

## Testing
<!-- List exact validation commands and their results, e.g. `make test`: passed -->
- **Commands run and results**
- **Unit tests added/updated**
- **Integration tests added/updated**
- **Manual testing performed** (e.g. `dg check defs`, a `dakp_build_<stage>` run)

## Code Location and Stage
<!-- Which area does this touch? -->
- [ ] `dagster/projects/dakp` (src / pipeline / workers)
- [ ] `dagster/libs` (mo-core / mo-kg / mo-dagster)
- [ ] Nix / devshell / CI
- [ ] Docs only

## Related Issues

- [ ] None

## Checklist

- [ ] `npx nx affected -t test` passes
- [ ] `prek run --all-files` clean
- [ ] Every code location still loads (`dg check defs`)
- [ ] Documentation updated
- [ ] No breaking changes to published artifacts

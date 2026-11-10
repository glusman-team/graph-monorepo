---
name: CI or deployment problem
about: A failure or gap in GitHub Actions, Docker packaging, Fly deployment, Pages, or the Nix flake
title: "[CI]: "
labels: ci
assignees: ""
---

## Description
<!-- What is failing or missing, and its impact. -->

## Workflow or Command
<!-- Link the failing workflow run, or give the exact local command that failed
     (for example `make docs-verify`, `npx nx affected -t test`, `docker build`, `nix flake check`). -->

## Scope
<!-- Check the affected area, if known. Scheduled DAKP release failures are filed automatically
     through the "DAKP build failed" template; do not refile those here. -->
- [ ] GitHub Actions (`.github/workflows`)
- [ ] Docker image / deployment packaging
- [ ] Fly deployment (`sites/linkouts`)
- [ ] GitHub Pages (`sites/docs`)
- [ ] Nix flake / devshells (`nix/modules/`)
- [ ] Dependabot / lock files

## Logs or Output
<!-- Paste the relevant, trimmed output. Redact any secret or token. -->

## Additional Context
<!-- Anything that helps reproduce or scope: first failure, recurrence, recent related changes. -->

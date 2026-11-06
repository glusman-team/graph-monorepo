#!/usr/bin/env bash
# Run affected checks against the best available main ref; isolated test clones run all.
set -euo pipefail
cd "$(dirname "$0")/.."

if git rev-parse --verify --quiet refs/remotes/origin/main >/dev/null; then
  base=origin/main
elif git rev-parse --verify --quiet refs/heads/main >/dev/null; then
  base=main
else
  echo "No main ref is available; running all Nx test and lint targets."
  exec npx nx run-many -t test lint --parallel=4
fi

exec npx nx affected --base="$base" -t test lint --parallel=4

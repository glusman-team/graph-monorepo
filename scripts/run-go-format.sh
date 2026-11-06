#!/usr/bin/env bash
# Format only Go modules owning changed files; never create a root go.work.
set -euo pipefail
cd "$(dirname "$0")/.."

declare -A modules=()
for file in "$@"; do
  if [[ "$file" == ".golangci.yml" ]]; then
    while IFS= read -r manifest; do
      modules[${manifest%/go.mod}]=1
    done < <(find dagster -type f -name go.mod | sort)
    continue
  fi

  directory=$(dirname "$file")
  while true; do
    if [[ -f "$directory/go.mod" ]]; then
      modules[$directory]=1
      break
    fi
    [[ "$directory" != "." && "$directory" != "/" ]] || break
    directory=$(dirname "$directory")
  done
done

for module in "${!modules[@]}"; do
  echo "golangci-lint fmt: $module"
  (cd "$module" && golangci-lint fmt ./...)
done

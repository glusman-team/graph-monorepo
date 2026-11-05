#!/usr/bin/env bash
# Run the race suite in each Go module owning a changed file.
set -euo pipefail
cd "$(dirname "$0")/.."

declare -A modules=()
if [[ ${1:-} == "--all" ]]; then
  shift
  while IFS= read -r manifest; do
    modules[${manifest%/go.mod}]=1
  done < <(find dagster -type f -name go.mod | sort)
fi

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

export GOTOOLCHAIN=local
export GOMAXPROCS=${GOMAXPROCS:-4}
export GOFLAGS=${GOFLAGS:--p=4}
for module in "${!modules[@]}"; do
  echo "go test -race: $module"
  (cd "$module" && go test -race -count=1 ./...)
done

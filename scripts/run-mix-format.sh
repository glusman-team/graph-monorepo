#!/usr/bin/env bash
# Run mix format in each BEAM project owning a changed source file.
set -euo pipefail
cd "$(dirname "$0")/.."

declare -A projects=()
for file in "$@"; do
  directory=$(dirname "$file")
  while true; do
    if [[ -f "$directory/mix.exs" ]]; then
      projects[$directory]=1
      break
    fi
    [[ "$directory" != "." && "$directory" != "/" ]] || break
    directory=$(dirname "$directory")
  done
done

for project in "${!projects[@]}"; do
  echo "mix format: $project"
  (cd "$project" && mix format)
done

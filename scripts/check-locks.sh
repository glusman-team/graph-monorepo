#!/usr/bin/env bash
# Check every language lockfile without changing it. Used by prek and CI.
set -euo pipefail
cd "$(dirname "$0")/.."

status=0
run_check() {
  local directory=$1
  shift
  echo "lock check: $directory: $*"
  if ! (cd "$directory" && "$@"); then
    status=1
  fi
}

while IFS= read -r manifest; do
  directory=${manifest%/pyproject.toml}
  if [[ ! -f "$directory/uv.lock" ]]; then
    echo "missing uv.lock beside $manifest" >&2
    status=1
    continue
  fi
  run_check "$directory" uv lock --check
done < <(find dagster -type f -name pyproject.toml -not -path '*/.venv/*' | sort)

while IFS= read -r manifest; do
  directory=${manifest%/package-lock.json}
  [[ -n "$directory" ]] || directory=.
  run_check "$directory" npm ci --dry-run --ignore-scripts --no-audit --no-fund
done < <(find . -type f -name package-lock.json \
  -not -path './.git/*' -not -path '*/node_modules/*' -not -path '*/.venv/*' | sort)

while IFS= read -r manifest; do
  directory=${manifest%/go.mod}
  run_check "$directory" go mod tidy -diff
done < <(find . -type f -name go.mod \
  -not -path './.git/*' -not -path '*/node_modules/*' -not -path '*/.venv/*' | sort)

while IFS= read -r manifest; do
  directory=${manifest%/mix.exs}
  if [[ ! -f "$directory/mix.lock" ]]; then
    echo "missing mix.lock beside $manifest" >&2
    status=1
    continue
  fi
  run_check "$directory" env MIX_ENV=test mix deps.get --check-locked
  run_check "$directory" env MIX_ENV=test mix deps.unlock --check-unused
done < <(find . -type f -name mix.exs \
  -not -path './.git/*' -not -path '*/deps/*' -not -path '*/_build/*' \
  -not -path '*/node_modules/*' | sort)

exit "$status"

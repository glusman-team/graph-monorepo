#!/usr/bin/env bash
# Run a production image with only the public file fixture and a disposable signing key.
set -euo pipefail
cd "$(dirname "$0")/../../.."

image=${LINKOUTS_IMAGE:-linkouts:ci}
port=${LINKOUTS_PREVIEW_PORT:-18080}
name="linkouts-preview-smoke-${GITHUB_RUN_ID:-local}"
secret="$(openssl rand -hex 64)"
home="$(mktemp)"
edge="$(mktemp)"

cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -f "$home" "$edge"
}
trap cleanup EXIT
cleanup

docker_flags=(
  --detach --rm --name "$name"
  --publish "127.0.0.1:${port}:8080"
  --env SECRET_KEY_BASE="$secret"
  --env PHX_HOST=127.0.0.1
  --env LINKOUTS_PREVIEW_FIXTURES=/app/preview-docs.ndjson
  --volume "$PWD/dagster/projects/linkouts/contracts/docs.ndjson:/app/preview-docs.ndjson:ro"
)
# No CPU cap by default: the host (or the CI daemon's own limits) governs.
# Set LINKOUTS_PREVIEW_CPUS explicitly when a bound is wanted; values above
# the daemon's CPU count are rejected by docker itself.
if [[ -n "${LINKOUTS_PREVIEW_CPUS:-}" ]]; then
  docker_flags+=(--cpus "$LINKOUTS_PREVIEW_CPUS")
fi
docker run "${docker_flags[@]}" "$image" >/dev/null

for attempt in $(seq 1 60); do
  if curl --fail --silent --show-error "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == 60 ]]; then
    docker logs "$name"
    exit 1
  fi
  sleep 1
done

curl --fail --silent --show-error "http://127.0.0.1:${port}/" >"$home"
curl --fail --silent --show-error \
  "http://127.0.0.1:${port}/edges/12ae7437-12dc-3c2a-b487-5297c09fc5e5" >"$edge"
grep -q 'DEV preview' "$home"
grep -q 'noindex, nofollow' "$home"
grep -q 'drugapprovals' "$home"
grep -q '12ae7437-12dc-3c2a-b487-5297c09fc5e5' "$edge"
test "$(docker exec "$name" id -u)" != 0
printf 'Production image and credential-free preview smoke test passed.\n'

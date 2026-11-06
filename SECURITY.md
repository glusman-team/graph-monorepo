# Security

## Report privately

Use [GitHub private vulnerability reporting](https://github.com/glusman-team/graph-monorepo/security) for `glusman-team/graph-monorepo`. If that is unavailable, contact the maintainer through https://github.com/SkyeAv.

**Do not open a public issue for a leaked credential or vulnerability.** Include the smallest reproduction, affected component, whether a real credential is involved, and urgency. A real leaked Cosmos or Fly credential must be rotated immediately; deleting a commit does not make it safe.

## Secrets and public fixtures

- Cosmos keys come from the process environment. Phoenix receives only `COSMOS_PRIMARY_CONNECTION_STRING_R` or an equivalent read-only endpoint/key. The worker uses the read-write value only for authorized ingestion.
- The committed `.envrc` contains only `use flake`. `.envrc.example` documents names and public paths only. Real values stay outside Git.
- Production Phoenix secrets live in Fly secrets. `FLY_API_TOKEN` is a GitHub secret used only by the deployment job after CI succeeds.
- Never place secrets in Dagster run configuration, Pipes extras/metadata, asset output, committed TOML/YAML, test reports, or logs.
- `gitleaks` scans changes, with project rules for Cosmos connection-string keys and Fly tokens. Large-file and merge-conflict hooks provide additional backstops.
- The public preview serves only `dagster/projects/linkouts/contracts/docs.ndjson`. It receives no Cosmos credentials, production origin key, or production cluster membership. Its own generated Phoenix signing key is not a production secret.

## Read-only Phoenix boundary

Phoenix in `sites/linkouts/` performs point reads only. It cannot upsert or delete documents. The Go worker's write key never belongs in the Phoenix environment, so a web-tier compromise cannot mutate the serving store with the web app's credentials.

The storage and signing contracts are documented in the Linkouts ADRs on the documentation site.

## Origin lockdown

Production is reached through Cloudflare. Every non-health request must carry the secret `X-Origin-Key` header, compared with `Plug.Crypto.secure_compare/2` to avoid a timing oracle.

- `sites/linkouts/lib/edge_linkouts_web/origin_check.ex` rejects missing, wrong, or repeated headers before routing/store reads.
- LiveView socket transports are dispatched before endpoint plugs, so `live_socket.ex` repeats the same constant-time check during `connect/3`.
- Public digested static assets carry no private data and do not spend RU.
- `GET /healthz` is the sole exemption; it returns `200 ok` without touching Cosmos so Fly health checks cannot cause a dependency restart loop.
- The key is read once at boot from `X_ORIGIN_KEY`. Unset disables the check for local/test use; production configuration must set it. It lives only in Fly secrets and Cloudflare configuration.

## RU isolation

The Cosmos free-tier account provides 1000 RU/s. Phoenix defaults to `RU_BUDGET_WEB=100`; the ingestion worker defaults to `RU_BUDGET_CLI=900`. Do not raise the web allowance during an upload. Both sides pace on measured request charges, and reads are coalesced/cached by TTL class.

## Dagster mutation controls

Normal `linkouts_upload` runs are manually launched. Dictionary training, inspection, verification, and provisioning are separate narrow jobs. No schedule or sensor is installed.

Destructive assets/jobs are absent at definition load unless `LINKOUTS_ENABLE_DESTRUCTIVE_JOBS=1` is deliberately set. Execution additionally requires exact target/scope confirmation; dry-run cannot be used for container deletion. Do not enable these definitions in a normal shared webserver or daemon environment.

Tests use file stores and fake engines. Real Cosmos/Fly/public-dataset operations require separate explicit authorization and are never part of CI or offline verification.

## Application hardening

- Phoenix has no form POST ingestion routes; LiveView events use the socket. Body parsing is restricted and size-limited.
- Display configs produce escaped text and safe HTTP(S) links, not raw HTML.
- Production Docker bases are digest-pinned and the container runs as non-root. The root build-context allowlist prevents unrelated local data/credentials reaching a remote builder.
- CI pins third-party actions by full commit SHA, uses least-privilege permissions, and confines Pages write/OIDC permissions to the deploy job.
- Dependabot tracks npm, uv/Python, Go, Mix, Docker, and GitHub Actions. Nix flake inputs require explicit reviewed lock updates.
- Blocking CI audits cover Go (`govulncheck`), Hex (`mix hex.audit`), and npm (`npm audit`). No permanent vulnerability exception is accepted.

See `CONTRIBUTING.md` for the offline verification matrix and safe development setup.

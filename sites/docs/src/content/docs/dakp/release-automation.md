---
title: Automated DAKP releases
description: Friday release check, GPU-gated build, Hugging Face publication, Linkouts ingestion, and failure reporting.
sidebar:
  order: 2
---

Dagster owns release scheduling. There is no release crontab or systemd timer. The production
host is **wenceslaus**; the weekly tick is **Friday 17:00 America/Los_Angeles**.

## Workflow

1. `dakp_release_check` runs preflight before source acquisition. It validates
   `DAKP_FULLMAP`, the executable extraction worker, fixture data, disk headroom,
   HF/GitHub credentials and repositories, release safety flags, and the managed
   NER cache. It fetches `origin/main` and fails if `HEAD` differs or tracked files
   are dirty. It never updates the checkout itself.
2. Acquisition applies source-specific freshness gates. Source refs are reduced
   to sorted BLAKE3 fingerprints and compared with the last published state.
   No changes means no build, version bump, or upload.
3. `dakp_gpu_gate` evaluates hourly. Every GPU must use **at most 1024 MiB**. The
   check-run start time plus 24 hours is the deadline. A closed gate skips until
   the next tick; an expired gate files one issue and does not build.
4. The sensor reads HF dataset tags, chooses the highest `vX.Y.Z`, increments its
   patch component, and launches `dakp_release_build` with
   `dakp/publication_version`. Package versions are not changed.
5. The build uses the extraction, shaping, Tablassert, and QC assets, then
   `release_verify`, `dataset_card`, `gpu_release_check`, and `hf_publish`.
6. Publication uploads `<version>/`, commits the generated card, prunes only
   superseded version directories with existing pinning tags, verifies uploaded
   checksums, and creates `v<version>`. State updates only after success. Older
   releases remain accessible through dataset tags, not mutable branches.
7. `dakp_build_failure_issues` opens or comments on a fingerprinted GitHub issue
   with errors, step timing/status, GPU diagnostics, and truncated logs.

## Linkouts ingestion after publication

`hf_publish` also emits a small public Linkouts contract in asset metadata:

- `version`
- `dry_run`
- `verified`
- `linkouts_key`
- `linkouts_nodes_path`
- `linkouts_edges_path`

The Linkouts code location defines `linkouts_dakp_release`, an asset sensor on the
global `hf_publish` asset key. It does not import DAKP implementation code. On each
materialization it:

1. skips disabled automation;
2. skips dry-run or unverified publications;
3. checks that `linkouts_key` is exactly `infores:drugapprovals-kp-<version>`;
4. checks that the published KGX node/edge pair exists and is non-empty on the
   Dagster host;
5. checks that the configured zstd dictionary exists;
6. launches the narrow `linkouts_load_release` job with a deterministic
   version-scoped run key.

The job invokes the hidden Linkouts worker with a direct `load` operation. This is a
merge-load into the existing `edge_linkouts/edges` Cosmos container, not a
stage/push blue/green replacement. Existing edge documents retain their older
release versions, matching how prior DAKP releases were loaded.

The sensor is stopped unless `LINKOUTS_DAKP_INGEST_ENABLED=1` is set when the
Linkouts code location loads. A stored STOPPED sensor state can require an explicit
UI start after the first enabled deployment.

| Environment variable                  | Default                                                    | Meaning                                              |
| ------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------- |
| `LINKOUTS_DAKP_INGEST_ENABLED`        | `0`                                                        | Start the DAKP release sensor.                       |
| `LINKOUTS_DAKP_TARGET_STORE`          | `cosmos`                                                   | Direct load target.                                  |
| `LINKOUTS_DAKP_DICTIONARY_PATH`       | `sites/linkouts/priv/zstd/drugapprovals-kp.77140996.dict`  | zstd dictionary required by the worker.              |
| `LINKOUTS_DAKP_SAMPLE_SIZE`           | `1024`                                                     | Random-pool capacity written by the live load.       |
| `LINKOUTS_DAKP_SAMPLE_SEED`           | `0`                                                        | Reservoir sample seed.                               |
| `LINKOUTS_DAKP_CONCURRENCY`           | `8`                                                        | Worker concurrency; use 4 on the shared build host.  |
| `LINKOUTS_DAKP_THREADS`               | `0`                                                        | Join engine thread count.                            |
| `LINKOUTS_DAKP_ENGINE`                | `chdb`                                                     | `chdb` in production, `fake` in deterministic tests. |
| `LINKOUTS_DAKP_PROGRESS`              | `1`                                                        | Structured worker progress logging.                  |
| `LINKOUTS_DAKP_ZSTD_LEVEL`            | `0`                                                        | zstd codec level from 0 through 22.                  |
| `LINKOUTS_WORKER_BIN`                 | `dagster/projects/linkouts/workers/go/bin/linkouts-worker` | Hidden worker executable.                            |
| `COSMOS_PRIMARY_CONNECTION_STRING_RW` | none                                                       | Worker write credential; never set in code or docs.  |

The Cosmos write credential stays in the mode-600 Dagster environment file. It is
not copied into sensor metadata, run config, artifacts, or documentation.

The direct-load report records edges, created/merged/skipped documents, version
count, random-pool size, byte counts, throttling, and measured RU charges. A failed
run does not publish an artifact reference. Because the run key is deterministic,
the sensor will not automatically start a second run for the same release version
after any existing launch; inspect the failed run, correct the cause, and relaunch
`linkouts_load_release` manually with the same typed config when needed.

## Freshness and manual builds

`DAKP_SOURCE_REFRESH` controls source acquisition:

- `auto` (default): reuse fresh source data; refresh after the source's age gate.
- `cached`: no source network access, regardless of age. Missing cache entries
  fail loudly. Use this for on-demand warm builds between weekly checks.
- `force`: rediscover and redownload. `DAKP_FORCE=1` also bypasses operation caches.

The common fast-source default is `DAKP_SOURCE_MAX_AGE_DAYS=7`. Overrides:

| Environment variable                 | Default | Scope                         |
| ------------------------------------ | ------- | ----------------------------- |
| `DAKP_DAILYMED_MAX_AGE_DAYS`         | global  | Full-release SPL data         |
| `DAKP_DRUGSFDA_MAX_AGE_DAYS`         | global  | Drugs@FDA ZIP                 |
| `DAKP_EMA_MAX_AGE_DAYS`              | global  | Medicines workbook            |
| `DAKP_EMA_DOCUMENTS_MAX_AGE_DAYS`    | global  | Documents JSON report         |
| `DAKP_EMA_SMPC_MAX_AGE_DAYS`         | 30      | Product-information PDF crawl |
| `DAKP_CANADA_VIGILANCE_MAX_AGE_DAYS` | 30      | Monthly ZIP                   |
| `DAKP_FAERS_MAX_AGE_DAYS`            | global  | Discovery index only          |
| `DAKP_ICD10CM_MAX_AGE_DAYS`          | global  | CDC release discovery only    |

A non-positive age means no age-based reuse: every `auto` run re-checks that source
upstream. Use `cached` to skip the network instead. FAERS reuses cached quarter ZIPs
indefinitely and downloads only newly discovered quarters unless forced. ICD-10-CM
selects the newest fiscal-year directory, preferring an April update within the same
fiscal year; its selected ZIP is immutable and reused. `DAKP_ICD10CM_URL` explicitly
pins a historical release instead. Spaces and hyphens in CDC ZIP names are tolerated.

```bash
cd "$DAKP_REPO_ROOT/dagster/projects/dakp"
DAKP_SOURCE_REFRESH=cached uv run --no-sync dagster job execute \
  -m dakp_dag.definitions -j dakp_build
```

Narrow `dakp_build_<stage>` jobs consume published refs without running upstream
acquisition. Scheduled releases require `auto`, online NER, and no quarter/release
limits.

## Install the 24/7 runtime

The flake generates a tmux supervisor and three systemd user units. Use tmux when
sudo or lingering are unavailable. Launchers are generated by Nix; there are no
added repository shell scripts. The unauthenticated Dagster UI binds to
**127.0.0.1**. Use an SSH tunnel or an authenticating reverse proxy; do not expose
it directly to the network.

Tmux survives SSH logout when the host does not kill user processes on logout, but
**not a host reboot**. After a reboot, enter the Nix shell again to start it.
Automatic reboot recovery requires host-level service permissions; neither tmux
nor a non-lingering user manager can provide it.

From a clean checkout equal to `origin/main`:

```bash
cd "$DAKP_REPO_ROOT"
# Each code location and the deployment have separate Python environments.
(cd dagster/projects/dakp && uv sync --locked --group ner)
(cd dagster/projects/linkouts && uv sync --locked)
(cd dagster/deployments/local && uv sync --locked)
(cd dagster/projects/dakp/workers/go && go build -o bin/dakp-extract ./cmd/dakp-extract)
(cd dagster/projects/linkouts/workers/go && go build -o bin/linkouts-worker ./cmd/linkouts-worker)

mkdir -p ~/.config/dagster ~/.local/state/dagster
install -m 600 dagster/projects/dakp/release.env.example ~/.config/dagster/dagster.env
# Edit the file: absolute paths, HF_TOKEN, GITHUB_TOKEN, and host-specific settings.
cp dagster/deployments/local/dagster.yaml ~/.local/state/dagster/dagster.yaml

# Keep persistent out-links as GC roots for the generated services and launchers.
nix build .#dagster-tmux --out-link "$HOME/.local/state/dagster-tmux"
nix build .#dakp-nercache --out-link "$HOME/.local/state/dakp-nercache"
```

### Tmux supervisor

If migrating from user units, avoid duplicate cache/Dagster owners:

```bash
systemctl --user disable --now dakp-nercache dagster-daemon dagster-webserver
```

Entering the deployment devshell automatically starts or reuses the detached
`dakp` session when `~/.config/dagster/dagster.env` exists:

```bash
cd /path/to/graph-monorepo
nix develop .#deploy
```

Auto-start applies to interactive shells. Set `DAKP_TMUX_AUTOSTART=0` before shell
entry to suppress it, or `DAKP_TMUX_AUTOSTART=1` to explicitly enable it for a
non-interactive `nix develop .#deploy -c` invocation. Use `DAKP_SERVICE_ENV` to select
another environment file. The supervisor refuses to start beside active or enabled
systemd units. `process-compose` restarts a service 10 seconds after an unexpected
exit; the next `up` repairs a stopped process-compose pane. Startup is serialized
with a lock.

```bash
dakp-services status
dakp-services attach
dakp-services logs
dakp-services logs daemon
dakp-services restart
dakp-services down
```

`dakp-services logs` reads the process-compose buffers. Rotated service logs are in
`$DAGSTER_HOME/tmux/services.log*`; orchestrator logs are in `process-compose.log`.
The control socket directory is mode 700. `down` asks process-compose to stop each
service with SIGTERM and a 120-second timeout, allowing NER-cache cleanup and
Dagster shutdown. The session remains after SSH logout. After a host reboot, run
`nix develop .#deploy` in the checkout again.

### Systemd user units

Use this alternative only when the host permits user services to remain active
without a login session:

```bash
nix build .#dagster-units --out-link "$HOME/.local/state/dagster-units"
mkdir -p ~/.config/systemd/user
install -m 0644 "$HOME/.local/state/dagster-units/share/systemd/user/"*.service \
  ~/.config/systemd/user/
systemctl --user daemon-reload
sudo loginctl enable-linger "$USER"
systemctl --user enable --now dakp-nercache dagster-daemon dagster-webserver
```

Set `DAKP_REPO_ROOT`, `DAGSTER_HOME`, `DAKP_WORKDIR`, `LINKOUTS_WORKDIR`, and
`MO_PUBLISHED_ROOT` to absolute paths in the environment file. The NER daemon uses
`$DAKP_WORKDIR/cache/ner/` and publishes the existing localhost `server.json` there.
`DAKP_NERCACHE_EXTERNAL=1` prevents Python from spawning another Pebble owner;
`DAKP_NERCACHE_REQUIRED=1` makes release preflight fail if it is unhealthy.

`HF_TOKEN` needs dataset write access. `GITHUB_TOKEN` needs repository Issues
read/write and metadata read access. The Cosmos write connection string must be
present for Linkouts ingestion, and a read-only string can be used by Phoenix. Do
not commit any token or connection string. The environment file must remain mode 600. The tmux supervisor sources it as Bash; keep it to plain `KEY=value` lines as
shown in `release.env.example`, without command substitution or other shell code.

### Rehearse before enabling

Keep `DAKP_RELEASE_ENABLED=0` and `DAKP_PUBLISH_DRY_RUN=1` initially. Keep
`LINKOUTS_DAKP_INGEST_ENABLED=0` until the Linkouts worker binary and Cosmos write
credential are installed. Manually run `dakp_release_check` from Dagster, inspect
its delta, then evaluate or launch the GPU gate, or launch `dakp_release_build`
with the chosen version and `dakp/check_run_id` run tags.

Dry run stages the files and card and records the intended HF operations; it does
not upload, prune, tag, or advance release state. Its `hf_publish` materialization
is still emitted and includes `dry_run=true`, so the Linkouts sensor skips it.

After a successful full rehearsal and failure drill, set `DAKP_PUBLISH_DRY_RUN=0`
and `DAKP_RELEASE_ENABLED=1`. Enable Linkouts only in the same deliberate step by
setting `LINKOUTS_DAKP_INGEST_ENABLED=1`. Restart the services with
`dakp-services restart` or restart both Dagster systemd services. Inspect the UI:
DAKP schedule/sensors and the Linkouts sensor must show RUNNING. An existing stored
STOPPED state may need enabling in the UI. Confirm the next Friday tick.

Do not enable unattended publication or live Cosmos ingestion before rehearsal
succeeds.

### Operate and update

```bash
dakp-services status
dakp-services logs daemon
systemctl --user status dakp-nercache dagster-daemon dagster-webserver
journalctl --user -u dagster-daemon -u dakp-nercache --since today
ssh -L 3000:localhost:3000 wenceslaus
```

To deploy new `main`, stop or reload only while no release run is active. Fast-forward
the checkout, sync locked environments, rebuild changed Go workers and the Nix
supervisor, then restart the code locations and services. Preflight intentionally
fails on a stale checkout rather than mutating live code.

State lives in `$MO_PUBLISHED_ROOT/dakp/release/state.json`; pending changes in
`pending.json`; staged uploads in `staging/<version>/`. Back up release state with
the workdir and published outputs. Missing state treats every source as changed.
Never edit a tag or reuse an already-uploaded version directory to recover a failed
publish: inspect the remote tree and issue first. A partially uploaded version is
rejected by the next publish attempt, preventing accidental overwrite.

The persistent Pebble cache contains reproducible NER mentions, not model weights.
Do not run a second `dakp-nercache` against the same directory, including one under
systemd and one under tmux. A managed restart is safe: Python external clients wait
for the live `server.json` endpoint and never spawn a competing server. Back up the
store while the service is stopped.

## FULLMAP rebuilds

Use a fresh sibling directory and preserve the current database until both a
`quick-map` and a narrow DAKP `kg_build` pass. The primary and all 16 shards are one
artifact; keep them together. A from-scratch build downloads BABEL and can need more
than 200 GiB transient space, so check the filesystem first. Tablassert uses all
available threads by default; do not set a thread flag.

Run inside a remote tmux session from the Nix devshell. Select the current
Tablassert version explicitly for later rebuilds.

```bash
FULLMAP_ROOT=/local_raid1/sgoetz/DBSTORE
FULLMAP_VERSION=22.2.2
PROD="$FULLMAP_ROOT/FULLMAP"
NEW="$FULLMAP_ROOT/FULLMAP.v$FULLMAP_VERSION"
OLD="$FULLMAP_ROOT/FULLMAP.pre-$FULLMAP_VERSION"
uv venv "$NEW/venv"
uv pip install --python "$NEW/venv/bin/python" \
  "tablassert[cli,log,qc,aria2]==$FULLMAP_VERSION"
"$NEW/venv/bin/tablassert" build-fullmap --force \
  --output "$NEW/fullmap" --cache "$NEW/downloads"
```

`--force` bypasses the prebuilt archive. The download cache is resumable; the spill
directory self-cleans on success. Validate with the builder and DAKP's installed
Tablassert before swapping. `PYTHONIOENCODING=utf-8` avoids Rich terminal failures
under a Latin-1 locale.

```bash
export PYTHONIOENCODING=utf-8 LC_ALL=C.UTF-8
"$NEW/venv/bin/tablassert" quick-map --fullmap "$NEW/fullmap" metformin
"$DAKP_REPO_ROOT/dagster/projects/dakp/.venv/bin/tablassert" \
  quick-map --fullmap "$NEW/fullmap" metformin
# Confirm no Dagster runs are active, then rename PROD to OLD and NEW to PROD.
# Roll back the directory rename if the second rename fails.
```

After the production-path quick-map passes, run `dakp_build_kg_build` with the
intended `DAKP_PUBLICATION_VERSION`. This verifies DAKP without invoking the HF
publish asset. Only after that run succeeds, remove intermediates and the old
database:

```bash
rm -rf -- "$OLD" "$PROD/downloads" "$PROD/venv" "$PROD/fullmap.spill.d"
```

Directory removal does not follow symlinks; preserve any external targets separately
and verify them afterward. Keep build and smoke logs under `$DAGSTER_HOME` for audit.

## Upstream cadence references

- [DailyMed full and periodic releases](https://dailymed.nlm.nih.gov/dailymed/spl-resources-all-drug-labels.cfm)
- [Drugs@FDA data files](https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-data-files)
- [EMA medicines downloads](https://www.ema.europa.eu/en/medicines/download-medicine-data)
- [FAERS quarterly discovery](https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html)
- [Canada Vigilance data extract](https://www.canada.ca/en/health-canada/services/drugs-health-products/medeffect-canada/adverse-reaction-database/canada-vigilance-online-database-data-extract.html)
- [ICD-10-CM fiscal-year and April directories](https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/)

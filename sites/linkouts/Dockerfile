# Build the Phoenix application from the monorepo root:
#   docker build -f sites/linkouts/Dockerfile -t linkouts .
# KG display configurations compile into the release through @external_resource.
# Base matches CI: Elixir 1.19 on OTP 28, Debian bookworm slim.

# ---- deps: only manifests invalidate the dependency layer ------------------------------
FROM docker.io/hexpm/elixir:1.19.6-erlang-28.5.0.7-debian-bookworm-20261005-slim@sha256:3120d2b962915184aac7b745d398674a37209eac03dac3391f28a16062055997 AS deps
WORKDIR /build/sites/linkouts
ENV MIX_ENV=prod
# git is required by the pinned heroicons and daisyui dependencies. Base digests pin the OS;
# apt remains unpinned so each intentional rebuild receives current security updates.
# hadolint ignore=DL3008
RUN apt-get update -y && apt-get install -y --no-install-recommends git ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && mix local.hex --force && mix local.rebar --force
COPY sites/linkouts/mix.exs sites/linkouts/mix.lock ./
RUN mix deps.get --only prod

# ---- build: compile, digest assets, assemble the release -------------------------------
FROM deps AS build
WORKDIR /build
COPY sites/linkouts ./sites/linkouts
WORKDIR /build/sites/linkouts
# Compile first: phoenix_live_view writes colocated CSS imported by Tailwind. These steps share
# the same source inputs, so one layer avoids redundant cache invalidation.
RUN mix deps.compile && mix compile --warnings-as-errors \
  && mix assets.deploy && mix release --overwrite

# ---- runtime ---------------------------------------------------------------------------
FROM docker.io/debian:bookworm-20261005-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587 AS runtime
# hadolint ignore=DL3008
RUN apt-get update -y && apt-get install -y --no-install-recommends \
  libstdc++6 libncurses6 openssl ca-certificates tzdata \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd -r app && useradd -r -g app app
WORKDIR /app
COPY --from=build --chown=app:app /build/sites/linkouts/_build/prod/rel/edge_linkouts /app
USER app
ENV MIX_ENV=prod \
    PHX_SERVER=true \
    PORT=8080
EXPOSE 8080
CMD ["/app/bin/edge_linkouts", "start"]

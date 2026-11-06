export GOMAXPROCS ?= 4
export GOFLAGS ?= -p=4
export GOTOOLCHAIN ?= local
export ERL_FLAGS ?= +S 4:4
export ELIXIR_ERL_OPTIONS ?= +fnu

LINKOUTS = dagster/projects/linkouts
LINKOUTS_GO = $(LINKOUTS)/workers/go
LINKOUTS_WEB = sites/linkouts

.PHONY: dev up test lint fmt hooks locks go-test-race check help
.PHONY: docs-dev docs-build docs-test docs-format docs-check
.PHONY: dakp-test dakp-lint
.PHONY: linkouts-test linkouts-lint linkouts-pipeline-test linkouts-dag-test linkouts-dag-lint
.PHONY: linkouts-worker-build linkouts-go-test linkouts-go-lint linkouts-contract-test
.PHONY: linkouts-web-test linkouts-web-lint linkouts-web-build linkouts-check-deploy linkouts-smoke linkouts-dialyzer
.PHONY: linkouts-web-setup linkouts-preview docs-verify

dev:
	nix run .#dagster-dev

up:
	nix run .#processes

test:
	npx nx run-many -t test --parallel=4

lint:
	npx nx run-many -t lint --parallel=4

fmt:
	nix fmt
	npm run docs:format

hooks:
	prek install

locks:
	scripts/check-locks.sh

go-test-race:
	scripts/run-go-tests.sh --all

check: lint test go-test-race docs-test linkouts-check-deploy

# Documentation (Astro Starlight + Pagefind).
docs-dev:
	npm run docs:dev

docs-build:
	npm run docs:build

docs-test:
	npm run docs:verify

docs-format:
	npm run docs:format

docs-check:
	npm run docs:format:check

docs-verify: docs-test

# DAKP component commands.
dakp-test:
	cd dagster/projects/dakp && uv run pytest -q

dakp-lint:
	cd dagster/projects/dakp && uv run ruff check src tests && uv run ruff format --check src tests

# Linkouts component commands.
linkouts-test: linkouts-pipeline-test linkouts-dag-test linkouts-go-test linkouts-web-test

linkouts-lint: linkouts-dag-lint linkouts-go-lint linkouts-web-lint linkouts-dialyzer

linkouts-pipeline-test:
	cd $(LINKOUTS)/pipeline && uv run pytest -q

linkouts-dag-test:
	cd $(LINKOUTS) && uv run pytest -q

linkouts-dag-lint:
	cd $(LINKOUTS) && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
	cd $(LINKOUTS)/pipeline && uv run ruff check . && uv run ruff format --check .

linkouts-worker-build:
	cd $(LINKOUTS_GO) && go build -o bin/linkouts-worker ./cmd/linkouts-worker

linkouts-go-test:
	cd $(LINKOUTS_GO) && go test -race -count=1 ./...

linkouts-go-lint:
	cd $(LINKOUTS_GO) && golangci-lint run ./... && golangci-lint fmt --diff ./... && go mod tidy -diff

linkouts-contract-test:
	cd $(LINKOUTS_GO) && go test -count=1 ./internal/contract
	cd $(LINKOUTS_WEB) && mix test test/contract_test.exs --warnings-as-errors

linkouts-web-test:
	cd $(LINKOUTS_WEB) && mix test --warnings-as-errors && mix test --warnings-as-errors --only cluster

linkouts-web-setup:
	cd $(LINKOUTS_WEB) && mix deps.get && mix compile

linkouts-preview:
	cd $(LINKOUTS_WEB) && COSMOS_BACKEND=file COSMOS_DOCS=$(CURDIR)/$(LINKOUTS)/contracts/docs.ndjson mix phx.server

linkouts-web-lint:
	cd $(LINKOUTS_WEB) && mix format --check-formatted
	cd $(LINKOUTS_WEB) && MIX_ENV=test mix compile --force --warnings-as-errors
	cd $(LINKOUTS_WEB) && mix credo --strict
	cd $(LINKOUTS_WEB) && MIX_ENV=test mix linkouts.check
	cd $(LINKOUTS_WEB) && mix deps.unlock --check-unused

linkouts-dialyzer:
	cd $(LINKOUTS_WEB) && MIX_ENV=dev mix dialyzer

linkouts-web-build:
	cd $(LINKOUTS_WEB) && MIX_ENV=prod mix compile --warnings-as-errors
	cd $(LINKOUTS_WEB) && MIX_ENV=prod mix assets.deploy
	cd $(LINKOUTS_WEB) && MIX_ENV=prod mix release --overwrite

linkouts-check-deploy:
	python3 sites/linkouts/scripts/check_deploy.py

linkouts-smoke:
	sites/linkouts/scripts/smoke_preview.sh

help:
	@echo "make dev / up              Dagster development UI or full local runtime"
	@echo "make test / lint           cached Nx tests and lint, at most four parallel tasks"
	@echo "make check                 offline aggregate verification"
	@echo "make locks                 recursive uv/npm/Go/Mix lock checks"
	@echo "make go-test-race          race suites in every independent Go module"
	@echo "make docs-dev / docs-test  Starlight development server or full documentation checks"
	@echo "make dakp-test             DAKP Python tests"
	@echo "make linkouts-test         all Linkouts Python, Go, and Phoenix tests"
	@echo "make linkouts-lint         all Linkouts static checks"
	@echo "make linkouts-contract-test cross-language byte-contract tests"
	@echo "make linkouts-web-build    production Phoenix release"
	@echo "make linkouts-web-setup    fetch and compile Phoenix dependencies"
	@echo "make linkouts-preview      Phoenix against the public contract corpus (no credentials)"
	@echo "make linkouts-check-deploy offline deployment-path and preview-isolation checks"
	@echo "make hooks                 install prek pre-commit/pre-push hooks"

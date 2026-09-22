# Fiboki V2 operator Makefile.
#
# Run from the repository root:
#     make -f deploy/Makefile setup
# or, more conveniently, symlink it:
#     ln -s deploy/Makefile Makefile && make setup
#
# Every target is the same command CI runs, so "it passes locally" means
# something. Where CI and this file differ, this file is wrong -- fix it here.

ROOT       := $(shell cd $(dir $(lastword $(MAKEFILE_LIST)))/.. && pwd)
VENV       := $(ROOT)/.venv
PY         := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip
FIBOKI     := $(VENV)/bin/fiboki
CONSTRAINTS:= $(ROOT)/deploy/constraints.txt
LOCKFILE   := $(ROOT)/deploy/requirements.lock
PYTEST_ARGS?= -q

.DEFAULT_GOAL := help
.PHONY: help setup lock lock-check test test-golden test-fast lint lint-fix typecheck \
        live-flag-check audit run-api run-worker campaign health doctor docker-build \
        compose-up compose-down clean check

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- environment

setup: ## Create the venv and install fiboki with dev extras, honouring constraints
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -c $(CONSTRAINTS) -e "$(ROOT)[dev]"
	$(PY) $(ROOT)/scripts/lockfile.py record --path $(LOCKFILE)
	@echo
	@$(FIBOKI) system doctor || true

lock: ## Re-record the dependency lockfile from this environment
	$(PY) $(ROOT)/scripts/lockfile.py record --path $(LOCKFILE)

lock-check: ## Fail if the environment has drifted from the lockfile
	$(PY) $(ROOT)/scripts/lockfile.py verify --path $(LOCKFILE)

# ---------------------------------------------------------------- quality

lint: ## ruff check
	$(VENV)/bin/ruff check $(ROOT)/src $(ROOT)/tests $(ROOT)/scripts

lint-fix: ## ruff check --fix
	$(VENV)/bin/ruff check --fix $(ROOT)/src $(ROOT)/tests $(ROOT)/scripts

typecheck: ## mypy over the source tree
	$(VENV)/bin/mypy --ignore-missing-imports --no-strict-optional $(ROOT)/src/fiboki

test: ## Full offline test suite
	cd $(ROOT) && $(PY) -m pytest tests/ $(PYTEST_ARGS)

test-fast: ## Everything except the slow marker
	cd $(ROOT) && $(PY) -m pytest tests/ -m "not slow" $(PYTEST_ARGS)

test-golden: ## ONLY the hand-calculated financial tests. Never weaken these.
	cd $(ROOT) && $(PY) -m pytest tests/ -m golden $(PYTEST_ARGS)

live-flag-check: ## Fail if committed config enables live execution
	$(PY) $(ROOT)/scripts/check_live_flags.py $(ROOT)

audit: ## Dependency vulnerability audit (best effort; needs network)
	$(PIP) install --quiet pip-audit==2.7.3
	$(VENV)/bin/pip-audit --strict --desc || \
	  (echo "pip-audit failed. Offline? Re-run with network."; exit 1)

check: lint typecheck live-flag-check lock-check test-golden test ## Everything CI gates on

# ---------------------------------------------------------------- run

run-api: ## Serve the API in the foreground
	cd $(ROOT) && $(VENV)/bin/uvicorn fiboki.api.app:app --host 127.0.0.1 --port 8000

run-worker: ## Run the research worker PROCESS in the foreground
	cd $(ROOT) && $(FIBOKI) worker run research --nice 10

campaign: ## Plan a research campaign. CAMPAIGN=path/to/campaign.json
	@test -n "$(CAMPAIGN)" || (echo "usage: make campaign CAMPAIGN=research/campaigns/x.json"; exit 2)
	cd $(ROOT) && $(FIBOKI) research campaign $(CAMPAIGN)

health: ## Run the real health checks
	$(FIBOKI) system health

doctor: ## Diagnose a broken local setup
	$(FIBOKI) system doctor

# ---------------------------------------------------------------- containers

docker-build: ## Build the runtime image
	cd $(ROOT) && docker build -f deploy/Dockerfile -t fiboki:local .

compose-up: ## Postgres + API + research worker
	cd $(ROOT)/deploy && docker compose --profile local up -d

compose-down: ## Stop the stack
	cd $(ROOT)/deploy && docker compose --profile local down

clean: ## Remove caches
	find $(ROOT) -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(ROOT)/.pytest_cache $(ROOT)/.mypy_cache $(ROOT)/.ruff_cache

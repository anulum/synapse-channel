# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — developer task runner

PY ?= python
PIP ?= $(PY) -m pip
SRC := src tests benchmarks

.DEFAULT_GOAL := help

.PHONY: help install install-hooks lint fmt typecheck test cov preflight \
	preflight-fast reuse build docs docs-build bench clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install the package with its dev toolchain (editable)
	$(PIP) install -e ".[dev,benchmark]"

install-hooks: ## Install the pre-commit git hooks
	$(PY) -m pre_commit install

lint: ## Check style (ruff lint + format check)
	$(PY) -m ruff check $(SRC)
	$(PY) -m ruff format --check $(SRC)

fmt: ## Auto-fix style (ruff lint --fix + format)
	$(PY) -m ruff check --fix $(SRC)
	$(PY) -m ruff format $(SRC)

typecheck: ## Run strict type checking (mypy)
	$(PY) -m mypy

test: ## Run the test suite with the coverage gate
	$(PY) -m pytest --cov=synapse_channel

cov: ## Run tests with a per-line missing-coverage report
	$(PY) -m pytest --cov=synapse_channel --cov-report=term-missing

preflight: lint typecheck test reuse ## Full local gate before a commit

preflight-fast: lint ## Lint-only gate (fast)

reuse: ## Check SPDX/REUSE 3.x licensing compliance
	$(PY) -m reuse lint

build: ## Build the sdist and wheel
	$(PY) -m build

docs: ## Serve the documentation site locally
	$(PY) -m mkdocs serve

docs-build: ## Build the documentation site (strict)
	$(PY) -m mkdocs build --strict

bench: ## Run the committed benchmark harnesses
	$(PY) benchmarks/relay_token_benchmark.py
	$(PY) benchmarks/routing_benchmark.py

clean: ## Remove build artefacts and caches
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache .ruff_cache \
		.mypy_cache .coverage htmlcov site
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +

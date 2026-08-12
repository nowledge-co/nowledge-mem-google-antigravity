.DEFAULT_GOAL := help

.PHONY: setup test validate package/release verify/release clean help

##@ Bootstrap

setup: ## Install project dependencies and configure local git index
	npm install
	git update-index --skip-worktree mcp_config.json || true

##@ Quality & Verification

test: ## Run hooks unit tests
	npm run test

test-integration: ## Run pytest container integration test suite
	uv run pytest -v tests/integration/ || python3 -m pytest -v tests/integration/

lint: ## Run static analysis, ruff linter, ty type checks, and pre-commit
	uv run pre-commit run --all-files


format: ## Run ruff code formatter
	uv run ruff format tests/integration/

validate: ## Validate manifest, pre-commit, and run unit tests
	npm run validate




##@ Build & Packaging

package/release: ## Package the plugin for release
	npm run package:release

verify/release: ## Validate and package release bundle
	npm run verify:release

clean: ## Remove build and temporary artifacts
	rm -rf dist *.tgz hooks/__pycache__ tests/__pycache__

##@ Utilities

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} \
	  /^[a-zA-Z0-9_/-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 } \
	  /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)

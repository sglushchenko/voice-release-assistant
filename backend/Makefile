.PHONY: install check lint format typecheck test openapi image live run

install:  ## Install dependencies from the lock file
	uv sync --frozen

check: lint typecheck test  ## Everything CI runs

lint:  ## Style and static analysis
	uv run ruff check .
	uv run ruff format --check .

format:  ## Apply formatting and safe fixes
	uv run ruff format .
	uv run ruff check --fix .

typecheck:  ## Strict type checking of the source
	uv run mypy

test:  ## Tests with a coverage floor
	uv run pytest --cov

openapi:  ## Regenerate the committed API contract (docs/openapi.json)
	uv run python -m release_assistant.api.openapi_export ../docs/openapi.json

image:  ## Build the container image (context is the repository root)
	docker build -f Dockerfile -t voice-release-assistant:dev ..

live:  ## Run the opt-in tests that call a real model provider (spends tokens)
	RELEASE_ASSISTANT_RUN_LIVE=1 uv run pytest -m live -s

run:  ## Start the API server (reads .env)
	uv run release-assistant

.PHONY: install check lint format typecheck test run

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

run:  ## Start the API server (reads .env)
	uv run release-assistant

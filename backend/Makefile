.PHONY: install check lint format typecheck test openapi image live eval eval-llm eval-compare eval-baseline run chat live-asc

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

eval:  ## Scripted evaluations: free, offline, checked against the committed baseline (this is what CI runs)
	uv run release-assistant-evals run --tier scripted --check-baseline

eval-llm:  ## Evaluations with a real model (spends tokens, run rarely); appends to evals/history.csv
	uv run release-assistant-evals run --tier llm

eval-compare:  ## Compare the two newest LLM runs case by case (BASE=<run> NEW=<run> to choose)
	uv run release-assistant-evals compare $(BASE) $(NEW)

eval-baseline:  ## Accept the newest scripted run as the baseline (review the diff first)
	uv run release-assistant-evals baseline

live:  ## Run the opt-in tests that call a real model provider (spends tokens)
	RELEASE_ASSISTANT_RUN_LIVE=1 uv run pytest -m live -s

chat:  ## Talk to the assistant in the terminal (mock data unless .env selects the real backend)
	uv run release-assistant-chat

live-asc:  ## Read-only calls to your real App Store Connect account (needs the key settings)
	RELEASE_ASSISTANT_RUN_LIVE_ASC=1 uv run pytest -m live -s tests/live/test_appstoreconnect_live.py

run:  ## Start the API server (reads .env)
	uv run release-assistant

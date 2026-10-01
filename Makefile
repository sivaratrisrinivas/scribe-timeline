.PHONY: help setup test lint typecheck schema check viewer-test fixtures capture

help:
	@echo "setup        Install Python and viewer dependencies"
	@echo "test         Run the Python test suite"
	@echo "viewer-test  Run the viewer test suite"
	@echo "lint         Ruff + mypy + tsc"
	@echo "schema       Regenerate schema/run-record.schema.json from the models"
	@echo "check        Everything CI runs (no network, no API key)"
	@echo "fixtures     Generate the speech clips (needs ELEVENLABS_API_KEY, once)"
	@echo "capture      Stream one condition to Scribe (needs ELEVENLABS_API_KEY)"

setup:
	uv sync --extra dev
	cd viewer && npm install

test:
	uv run pytest

viewer-test:
	cd viewer && npm run test

schema:
	uv run python scripts/generate_json_schema.py

lint:
	uv run ruff check .
	uv run mypy
	cd viewer && npx tsc --noEmit

check: lint test viewer-test
	@uv run python scripts/generate_json_schema.py --check

fixtures:
	uv run python -m scribe_timeline.capture.clips

capture:
	uv run python -m scribe_timeline.capture.probe

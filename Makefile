.PHONY: help setup test lint typecheck schema check viewer-test viewer-export viewer fixtures capture matrix

help:
	@echo "setup          Install Python and viewer dependencies, export the viewer bundle"
	@echo "test           Run the Python test suite"
	@echo "viewer-test    Run the viewer test suite"
	@echo "viewer-export  Rebuild viewer/public from saved run records and committed clips"
	@echo "viewer         Serve the viewer locally (no network, no API key)"
	@echo "lint           Ruff + mypy + tsc"
	@echo "schema         Regenerate schema/run-record.schema.json from the models"
	@echo "check          Everything CI runs (no network, no API key)"
	@echo "fixtures       Generate the speech clips (needs ELEVENLABS_API_KEY, once)"
	@echo "capture        Stream one condition to Scribe (needs ELEVENLABS_API_KEY)"
	@echo "matrix         Run the full comparison matrix (needs ELEVENLABS_API_KEY)"

setup:
	uv sync --extra dev
	cd viewer && npm install
	$(MAKE) viewer-export

test:
	uv run pytest

viewer-test:
	cd viewer && npm run test

# Rebuilds viewer/public from the committed evidence. No network, no API key: the
# run records and the speech clips are already in the repository, so the bundle is
# derived rather than captured.
viewer-export:
	uv run python scripts/export_viewer.py

viewer:
	cd viewer && npm run dev

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

matrix:
	uv run python -m scribe_timeline.capture.matrix $(ARGS)

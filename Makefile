.PHONY: help setup test check-live lint typecheck schema check viewer-test viewer-export viewer build walkthrough fixtures capture matrix

help:
	@echo "setup          Take a fresh clone to a working state: dependencies, then the viewer bundle"
	@echo "test           Run the Python test suite"
	@echo "viewer-test    Run the viewer test suite"
	@echo "viewer-export  Rebuild viewer/public from saved run records and committed clips"
	@echo "viewer         Serve the viewer locally (no network, no API key)"
	@echo "build          Build the static bundle in viewer/dist, ready to host anywhere"
	@echo "walkthrough    Re-record docs/walkthrough.webm from the built viewer (needs npm + network for the first run)"
	@echo "lint           Ruff + mypy + tsc"
	@echo "schema         Regenerate schema/run-record.schema.json from the models"
	@echo "check          Everything CI runs (no network, no API key)"
	@echo "check-live     The tests that reach the network (the published site). No API key, no credit."
	@echo "fixtures       Generate the speech clips (needs ELEVENLABS_API_KEY, once)"
	@echo "capture        Stream one condition to Scribe (needs ELEVENLABS_API_KEY)"
	@echo "matrix         Run the full comparison matrix (needs ELEVENLABS_API_KEY)"

# The one command a fresh clone needs, and the only one a reader has to run.
#
# Order matters twice over. `uv sync --extra dev` first, because `uv run` otherwise
# resolves a runtime-only environment and fails to find the test and lint tools -- a
# confusing way to learn that setup was skipped. The viewer bundle last, so the
# project is only declared working once there is something to serve.
setup:
	uv sync --extra dev
	cd viewer && npm install
	$(MAKE) viewer-export

test:
	uv run pytest

# The tests that reach the network, which `check` excludes by design. Nothing here costs
# API credit: it fetches the published static site, which is free to read.
#
# Separate from `check` rather than inside it, because `check`'s promise is that it needs
# no network, and a promise that depends on the failure handling holding up is not a
# promise. `-m live` overrides the `not live` default in pyproject.toml.
check-live:
	uv run pytest -m live -v

viewer-test:
	cd viewer && npm run test

# Rebuilds viewer/public from the committed evidence. No network, no API key: the
# run records and the speech clips are already in the repository, so the bundle is
# derived rather than captured.
viewer-export:
	uv run python scripts/export_viewer.py

viewer:
	cd viewer && npm run dev

# The public artifact: a directory of files that can be hosted as-is. `viewer/public`
# is served as static assets by the build, so the whole bundle -- every run record,
# every rebuilt WAV, the comparison -- lands in `dist` with no server-side anything.
# Still no network and no API key: the input is the committed evidence.
build: viewer-export
	cd viewer && npm run build

schema:
	uv run python scripts/generate_json_schema.py

# Re-records the walkthrough from the built viewer. Depends on `build` because the
# recording drives viewer/dist rather than a dev server -- the same directory that is
# published, mounted under a subpath, so the video shows what a reader would see.
#
# The one target here that reaches the network, and it does so only to install
# Playwright's browser if it is not already present. It needs no API key and spends no
# credit: the runs it records are the committed ones.
walkthrough: build
	cd viewer && npm install --no-save playwright
	npx playwright install chromium
	node scripts/record_walkthrough.js

lint:
	uv run ruff check .
	uv run mypy
	cd viewer && npx tsc --noEmit

# The viewer's tests read the exported bundle -- `ConditionTable.test.tsx` reads
# `viewer/public/comparison.json` -- so a fresh clone cannot run them until the
# bundle exists. `viewer-export` is therefore FIRST: make runs prerequisites left to
# right, and putting it later would leave a clean checkout failing the gate for a
# reason that has nothing to do with the code under test. It costs nothing: the
# export is derived from committed files and takes a couple of seconds.
check: viewer-export lint test viewer-test
	@uv run python scripts/generate_json_schema.py --check

fixtures:
	uv run python -m scribe_timeline.capture.clips

capture:
	uv run python -m scribe_timeline.capture.probe

matrix:
	uv run python -m scribe_timeline.capture.matrix $(ARGS)

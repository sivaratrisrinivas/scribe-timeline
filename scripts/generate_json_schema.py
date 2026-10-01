"""Regenerate the canonical run-record JSON Schema from the Python models.

Run via `make schema` (or `python3 scripts/generate_json_schema.py`) after changing
any model in `scribe_timeline.records`. CI checks the committed file is current,
so the schema cannot silently drift from the models that produce run records.

Usage:
    python3 scripts/generate_json_schema.py          # write schema/run-record.schema.json
    python3 scripts/generate_json_schema.py --check  # exit 1 if stale
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from scribe_timeline.records import RunRecord  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "schema" / "run-record.schema.json"


def render() -> str:
    schema = RunRecord.model_json_schema()
    # $defs keys and $id are not part of the contract the viewer consumes; sorting
    # keeps the committed file diffable.
    schema.pop("$id", None)
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> int:
    desired = render()
    if "--check" in sys.argv:
        if not SCHEMA_PATH.exists():
            print(f"missing {SCHEMA_PATH.relative_to(REPO_ROOT)}")
            return 1
        current = SCHEMA_PATH.read_text()
        if current != desired:
            print(f"{SCHEMA_PATH.relative_to(REPO_ROOT)} is stale; run: make schema")
            return 1
        print("run-record.schema.json is up to date")
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(desired)
    print(f"wrote {SCHEMA_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The Python models and the committed JSON Schema must describe the same contract.

The viewer's types are derived from the schema, and the runner's records are
produced by the Python models. If those two ever disagree, the viewer either
misreads real runs or rejects them -- and the failure would show up as a wrong
number in a published report rather than as an error.

So the schema is generated from the models, and a test re-derives it and compares.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "run-record.schema.json"

JSONObject = dict[str, Any]
GENERATOR = REPO_ROOT / "scripts" / "generate_json_schema.py"


def test_schema_file_exists() -> None:
    assert SCHEMA_PATH.exists(), "run `make schema` to generate the committed schema"


def test_committed_schema_matches_the_models() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_schema_forbids_additional_properties() -> None:
    """Unknown fields are rejected, which is what keeps a credential inadmissible."""
    schema = json.loads(SCHEMA_PATH.read_text())

    assert schema["additionalProperties"] is False
    for name, definition in schema["$defs"].items():
        assert definition.get("additionalProperties") is False, name


def test_schema_has_no_credential_bearing_property() -> None:
    """Mirrors the Python-side guard, on the artefact the viewer consumes.

    The walk asserts it *visited* at least one credential-shaped name. Without
    that, a renamed or emptied schema would pass by never entering the branch.
    """
    schema = json.loads(SCHEMA_PATH.read_text())
    banned = ("key", "token", "secret", "auth", "credential", "password")
    visited: list[str] = []

    def walk(node: JSONObject, path: str) -> None:
        for name, subschema in node.get("properties", {}).items():
            if any(word in name.lower() for word in banned):
                visited.append(f"{path}.{name}")
                # Only a boolean flag recording that nothing was stored is allowed.
                assert subschema.get("type") == "boolean", f"{path}.{name}"
            walk(subschema, f"{path}.{name}")
        for name, subschema in node.get("$defs", {}).items():
            walk(subschema, f"{path}.{name}")

    walk(schema, "$")

    assert visited == ["$.api_key_present"], f"unexpected credential-shaped names: {visited}"


def test_free_form_payload_is_still_open_in_the_schema() -> None:
    """Documents the gap the Python validator closes.

    `payload` is a free-form object, so the schema alone cannot reject a secret
    inside it. The guarantee comes from the `RawEvent` validator, which walks the
    payload -- see `test_event_payload_rejects_credentials_at_any_depth`. If this
    test ever starts failing because the schema grew a constraint, the validator
    can be simplified.
    """
    schema = json.loads(SCHEMA_PATH.read_text())
    payload = schema["$defs"]["RawEvent"]["properties"]["payload"]

    assert payload.get("additionalProperties") is not False


def test_required_fields_are_the_ones_always_present() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())

    assert set(schema["required"]) == {
        "schema_version",
        "run_id",
        "condition_id",
        "commit_strategy",
        "repeat_index",
        "manifest",
        "echoed_config",
    }


def test_generated_schema_is_deterministic() -> None:
    """Two independent renders must be byte-identical.

    This compares the generator's *output* twice. Running `--check` twice only
    proves the file is not stale, which the previous test already covers, so it
    could not fail for the reason its name claims.
    """
    first = subprocess.run(
        [sys.executable, "-c", _RENDER_SNIPPET],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    second = subprocess.run(
        [sys.executable, "-c", _RENDER_SNIPPET],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONHASHSEED": "12345"},
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


_RENDER_SNIPPET = (
    "import sys; sys.path.insert(0, 'src');"
    "sys.path.insert(0, 'scripts');"
    "from generate_json_schema import render;"
    "sys.stdout.write(render())"
)

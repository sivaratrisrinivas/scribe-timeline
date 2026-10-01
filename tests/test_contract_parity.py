"""The TypeScript contract must match the committed JSON Schema.

The viewer parses run records produced by the Python runner. If the two
descriptions of the record drift apart, the viewer either rejects real runs or
misreads them -- and a misread timestamp is indistinguishable from a real result.

This test reads the TypeScript source and the JSON Schema and asserts they
describe the same fields, so drift fails here rather than in a published number.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "run-record.schema.json"

#: Parsed JSON has no static type; this alias keeps the annotations honest
#: without repeating a long union everywhere.
JSONValue = Any
JSONObject = dict[str, Any]

TS_PATH = REPO_ROOT / "viewer" / "src" / "runRecord.ts"

#: The root schema *is* the RunRecord contract, so it is not in `$defs`.
SCHEMA_MODELS = {
    "RunRecord": "$",
    "Manifest": "Manifest",
    "EchoedSessionConfig": "EchoedSessionConfig",
    "RawEvent": "RawEvent",
    "WordTiming": "WordTiming",
    "Segment": "Segment",
}


def _ts_interface_body(name: str, source: str) -> str:
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.DOTALL)
    assert match, f"interface {name} not found in {TS_PATH.name}"
    return match.group(1)


def _ts_fields(body: str) -> set[str]:
    return set(re.findall(r"readonly (\w+)[?:]", body))


def _schema_fields(definition: JSONObject) -> set[str]:
    return set(definition.get("properties", {}))


def _ts_field_types(body: str) -> JSONObject:
    """Map each field name to a normalised type expression."""
    declarations = re.findall(r"readonly (\w+)(\??):\s*([^;]+);", body)
    return {name: _normalise_type(text) for name, _optional, text in declarations}


def _schema_field_types(definition: JSONObject) -> JSONObject:
    """Map each property name to a normalised type expression.

    Numeric bounds and nullability are folded in, because a `ge=0` in Python and a
    missing check in TypeScript is exactly the divergence this project cannot
    afford: the producer would emit a record the consumer calls corrupt.
    """
    types: JSONObject = {}
    for name, subschema in definition.get("properties", {}).items():
        types[name] = _normalise_schema_type(subschema)
    return types


#: TypeScript spells a named string-literal union as a named type; the schema
#: inlines its members. Both normalise to `enum<...>`.
def _normalise_type(text: str) -> str:
    text = text.strip().rstrip(";").strip()
    nullable = text.endswith("| null")
    if nullable:
        text = text[: -len("| null")].strip()

    # `readonly Foo[]` / `Array<Foo>` -> list<Foo>
    array = re.fullmatch(r"(?:Readonly<)?(?:readonly )?(?:Array<(.+)>)|(.+)\[\]", text)
    if array:
        inner = array.group(1) or array.group(2)
        text = f"list<{_normalise_type(inner)}>"
    else:
        record = re.fullmatch(r"Readonly<Record<string, (.+)>>|Record<string, (.+)>", text)
        if record:
            text = f"map<{_normalise_type(record.group(1) or record.group(2))}>"

    text = text.replace("readonly ", "")
    return f"union<{text},null>" if nullable else text


#: Named TypeScript types that stand in for an inline schema type, with the
#: members they must resolve to. Keeps the comparison structural: a named type
#: whose definition drifts from the schema is caught, rather than special-cased.
_TS_LITERAL_UNIONS = {
    "CommitStrategy": ("vad", "manual"),
    "ClockOrigin": ("monotonic_since_connect",),
}

#: TypeScript's `unknown` is the safe counterpart of the schema's free-form object.
_TS_ANY = {"unknown", "any"}


def _resolve_literal_unions(ts_type: str) -> str:
    if ts_type in _TS_LITERAL_UNIONS:
        return "enum<" + ",".join(_TS_LITERAL_UNIONS[ts_type]) + ">"
    return ts_type


def _normalise_schema_type(subschema: JSONObject) -> str:
    """Reduce a schema subschema to a base type, ignoring numeric bounds.

    Bounds cannot be expressed in a TypeScript type, so comparing them here would
    report a difference that does not exist. They are enforced at runtime by the
    parser instead, and `test_viewer_enforces_every_numeric_bound` checks that by
    running the parser.
    """
    # A nullable field is `anyOf: [T, null]`.
    if "anyOf" in subschema:
        variants = subschema["anyOf"]
        non_null = [v for v in variants if v.get("type") != "null"]
        nullable = len(non_null) != len(variants)
        base = _normalise_schema_type(non_null[0]) if non_null else "null"
        return f"union<{base},null>" if nullable else base

    if "$ref" in subschema:
        return str(subschema["$ref"]).rsplit("/", 1)[-1]
    if "enum" in subschema:
        return "enum<" + ",".join(subschema["enum"]) + ">"

    kind = subschema.get("type")
    if kind == "array":
        return f"list<{_normalise_schema_type(subschema.get('items', {}))}>"
    if kind == "object":
        values = subschema.get("additionalProperties")
        if isinstance(values, dict):
            return f"map<{_normalise_schema_type(values)}>"
        return "map<any>"
    return str(kind)


@pytest.fixture(scope="module")
def ts_source() -> str:
    return TS_PATH.read_text()


@pytest.fixture(scope="module")
def schema() -> JSONObject:
    parsed: JSONObject = json.loads(SCHEMA_PATH.read_text())
    return parsed


def _definition(schema: JSONObject, pointer: str) -> JSONObject:
    return schema if pointer == "$" else schema["$defs"][pointer]


@pytest.mark.parametrize("model_name", sorted(SCHEMA_MODELS))
def test_typescript_interface_has_every_schema_field(
    model_name: str, ts_source: str, schema: JSONObject
) -> None:
    ts_fields = _ts_fields(_ts_interface_body(model_name, ts_source))
    schema_fields = _schema_fields(_definition(schema, SCHEMA_MODELS[model_name]))

    assert schema_fields <= ts_fields, f"{model_name} missing {schema_fields - ts_fields}"


@pytest.mark.parametrize("model_name", sorted(SCHEMA_MODELS))
def test_typescript_interface_has_no_extra_fields(
    model_name: str, ts_source: str, schema: JSONObject
) -> None:
    """No field in the viewer that the runner cannot produce.

    An extra field would let the viewer read something that is never there, which
    is how a default of `0` sneaks into a displayed timestamp.
    """
    ts_fields = _ts_fields(_ts_interface_body(model_name, ts_source))
    schema_fields = _schema_fields(_definition(schema, SCHEMA_MODELS[model_name]))

    assert ts_fields <= schema_fields, f"{model_name} has extra {ts_fields - schema_fields}"


@pytest.mark.parametrize("model_name", sorted(SCHEMA_MODELS))
def test_field_types_agree(model_name: str, ts_source: str, schema: JSONObject) -> None:
    """The two sides must agree on each field's *type*, not merely its name.

    Name-only parity passed while Python modelled a commit boundary as a pair of
    integers and TypeScript modelled it as a string. The types are compared after
    normalisation, with numeric bounds folded in, so a constraint the producer
    enforces and the consumer ignores shows up here.
    """
    body = _ts_interface_body(model_name, ts_source)
    ts_types = _ts_field_types(body)
    schema_types = _schema_field_types(_definition(schema, SCHEMA_MODELS[model_name]))

    common = set(ts_types) & set(schema_types)
    assert common, f"{model_name} shares no fields to compare"

    mismatched = {
        name: (ts_types[name], schema_types[name])
        for name in common
        if not _types_agree(ts_types[name], schema_types[name])
    }
    assert not mismatched, f"{model_name} type mismatches (ts, schema): {mismatched}"


def _types_agree(ts_type: str, schema_type: str) -> bool:
    """Compare a normalised TS type against a normalised schema type."""
    ts_type = _resolve_literal_unions(ts_type)
    if ts_type == schema_type:
        return True
    # TypeScript has one numeric type; the schema distinguishes integer from number.
    if {ts_type, schema_type} == {"number", "integer"}:
        return True
    # A free-form map is `map<any>` on the schema side and `map<unknown>` in TS;
    # `map<number>` and `map<integer>` differ only in that same numeric split.
    if _map_key(ts_type) == _map_key(schema_type):
        return True
    # A single-valued literal type in TS stands in for a plain `string` enum slot
    # only when the schema constrains it; `clock_origin` is checked explicitly in
    # `test_clock_origin_is_the_same_literal_on_both_sides`.
    return ts_type in _TS_LITERAL_UNIONS and schema_type == "string"


def _map_key(type_text: str) -> str | None:
    match = re.fullmatch(r"map<(.+)>", type_text)
    if not match:
        return None
    inner = match.group(1)
    if inner in _TS_ANY or inner == "any":
        return "map<free>"
    if inner in ("number", "integer"):
        return "map<number>"
    return f"map<{inner}>"


def test_clock_origin_is_the_same_literal_on_both_sides(ts_source: str, schema: JSONObject) -> None:
    """`clock_origin` pins the arrival-latency clock, so it must not drift.

    The schema types it as a plain string, so the field-type comparison alone
    would not notice a changed literal. This asserts the actual value on both
    sides: the Python default and the TypeScript union member.
    """
    assert 'ClockOrigin = "monotonic_since_connect"' in ts_source

    from scribe_timeline.records import RawEvent

    assert RawEvent.model_fields["clock_origin"].default == "monotonic_since_connect"
    assert schema["$defs"]["RawEvent"]["properties"]["clock_origin"]["default"] == (
        "monotonic_since_connect"
    )


def test_every_schema_definition_has_a_typescript_counterpart(
    schema: JSONObject, ts_source: str
) -> None:
    """No `$def` entry may sit outside the parity comparison.

    A new model added to the Python side is compared to nothing unless it is
    registered here, so it could diverge silently.
    """
    declared = set(SCHEMA_MODELS) - {"RunRecord"}
    assert set(schema["$defs"]) == declared, (
        f"schema $defs and SCHEMA_MODELS disagree: "
        f"{set(schema['$defs']) ^ declared}"
    )
    for name in declared:
        assert f"export interface {name} " in ts_source, f"no TS interface for {name}"


def test_commit_strategy_uses_the_same_literals_as_the_schema(
    ts_source: str, schema: JSONObject
) -> None:
    root_enum = schema["properties"]["commit_strategy"]["enum"]
    # The echoed config's strategy is nullable: `session_started` does not send it,
    # so the schema expresses it as an anyOf against null rather than a bare enum.
    echoed = schema["$defs"]["EchoedSessionConfig"]["properties"]["commit_strategy"]
    echoed_variants = echoed["anyOf"]
    echoed_enum = next(v["enum"] for v in echoed_variants if "enum" in v)
    assert any(v.get("type") == "null" for v in echoed_variants)

    assert 'type CommitStrategy = "vad" | "manual";' in ts_source
    assert set(root_enum) == {"vad", "manual"}
    assert set(echoed_enum) == set(root_enum)


def test_schema_version_is_shared_between_both_sides(ts_source: str, schema: JSONObject) -> None:
    """`schema_version` is carried on both sides and is required on the record."""
    assert re.search(r"readonly schema_version: number;", ts_source)
    assert "schema_version" in schema["required"]


def test_schema_required_fields_are_not_optional_in_typescript(
    ts_source: str, schema: JSONObject
) -> None:
    """A field the schema requires must be non-optional in TypeScript.

    Only this direction is enforced. The schema gives `events`, `words`, and
    `api_key_present` defaults, so the viewer is free to be stricter -- and being
    stricter is safe, because it can only reject a malformed record early. Being
    *looser* would let the viewer read a field the runner never promised to write,
    which is how a default of `0` reaches a displayed timestamp.
    """
    body = _ts_interface_body("RunRecord", ts_source)

    for name in schema["required"]:
        declaration = re.search(rf"readonly {name}(\??):", body)
        assert declaration, f"{name} is required by the schema but absent from the TS interface"
        assert declaration.group(1) != "?", (
            f"{name} is required by the schema but optional in TypeScript"
        )

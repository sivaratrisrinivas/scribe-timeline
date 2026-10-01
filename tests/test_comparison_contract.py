"""The TypeScript comparison contract must match the report Python actually emits.

The run record has a generated JSON Schema and `test_contract_parity.py` to hold
the viewer to it. The comparison has neither: `ComparisonReport.to_dict` is a
hand-written dict beside the hand-written TypeScript, so nothing but a test stops
the two from drifting.

Drift here is quiet and consequential. A field Python adds and TypeScript does not
declare is a figure the report contains and the page silently omits -- and the page
is where a maintainer goes to check the finding. A field TypeScript declares and
Python does not emit is worse: the viewer refuses to draw a report it was handed.

Both directions are asserted against a real report built from real records, with
every branch populated, so a field that only appears for a control or only for a
non-anchor condition is covered too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scribe_timeline.analysis.compare import ComparisonReport, compare_runs
from scribe_timeline.audio.family import MARKER_TEXT
from scribe_timeline.audio.timeline import SAMPLE_WIDTH_BYTES  # noqa: F401  (documents PCM16)
from scribe_timeline.capture.completion import TIMESTAMPED_EVENT
from scribe_timeline.records import (
    EchoedSessionConfig,
    Manifest,
    RawEvent,
    RunRecord,
    Segment,
    WordTiming,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TS_PATH = REPO_ROOT / "viewer" / "src" / "comparison.ts"

SAMPLE_RATE = 16_000
MARKER_SAMPLE = 192_000
TIMELINE_SAMPLES = 288_000

#: The TypeScript interfaces the report's structure maps onto.
TS_INTERFACES = (
    "ComparisonReport",
    "ComparisonContext",
    "ConditionSummary",
    "ClaimComparison",
    "ControlConclusion",
    "ConditionContext",
)

#: Which report key holds which nested objects, and the interface describing them.
#: Paths that stop at an object describe it; paths ending in a list are read through
#: their first entry.
NESTED = (
    ("conditions", "ConditionSummary"),
    ("claims", "ClaimComparison"),
    ("context", "ComparisonContext"),
    ("context.conditions", "ConditionContext"),
)


def _record(condition_id: str, *, repeat: int, marker_ms: float, prior: int) -> RunRecord:
    return RunRecord(
        schema_version=1,
        run_id=f"run__{condition_id}__rep{repeat}",
        condition_id=condition_id,
        commit_strategy="manual" if condition_id.startswith("manual") else "vad",
        repeat_index=repeat,
        manifest=Manifest(
            condition_id=condition_id,
            sample_rate=SAMPLE_RATE,
            sample_count=TIMELINE_SAMPLES,
            markers={MARKER_TEXT: MARKER_SAMPLE},
            segments=(
                *(
                    Segment(clip_id="earlier", start_sample=index * 96_000, sample_count=16_472)
                    for index in range(prior)
                ),
                Segment(
                    clip_id="marker",
                    start_sample=MARKER_SAMPLE,
                    sample_count=18_701,
                    marker_text=MARKER_TEXT,
                ),
            ),
        ),
        echoed_config=EchoedSessionConfig(
            model_id="scribe_v2_realtime",
            sample_rate=SAMPLE_RATE,
            include_timestamps=True,
        ),
        events=tuple(
            RawEvent(
                type=TIMESTAMPED_EVENT,
                received_at_ms=float(index),
                payload={"words": []},
            )
            for index in range(prior + 1)
        ),
        words=(
            WordTiming(
                text=MARKER_TEXT + ".",
                start_ms=marker_ms,
                end_ms=marker_ms + 580.0,
                logprob=-1.3,
            ),
        ),
    )


@pytest.fixture(scope="module")
def report() -> ComparisonReport:
    """A report with every branch populated: anchor, drift, control, claims, context."""
    records: list[RunRecord] = []
    for condition, prior, times in (
        ("vad_0", 0, [12_200.0, 12_200.0, 12_200.0]),
        ("vad_1", 1, [12_300.0, 12_300.0, 12_300.0]),
        ("vad_2", 2, [12_380.0, 12_380.0, 12_380.0]),
        ("manual_2", 2, [12_200.0, 12_200.0, 12_200.0]),
    ):
        records += [
            _record(condition, repeat=index, marker_ms=time, prior=prior)
            for index, time in enumerate(times)
        ]
    return compare_runs(records, marker_text=MARKER_TEXT)


@pytest.fixture(scope="module")
def ts_fields() -> dict[str, set[str]]:
    """Every field each exported TypeScript interface declares, in snake_case."""
    source = TS_PATH.read_text()
    declared: dict[str, set[str]] = {}
    for name in TS_INTERFACES:
        match = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.DOTALL)
        assert match, f"interface {name} not found in {TS_PATH.name}"
        declared[name] = {
            _to_snake(field) for field in re.findall(r"readonly (\w+)[?:]", match.group(1))
        }
    return declared


def _to_snake(camel: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()


def _keys_at(payload: dict[str, Any], path: str) -> set[str]:
    """The keys of the report object at `path`, reading one entry of a list.

    A non-anchor condition is preferred where the path holds a list, because the
    anchor's `delta_*` fields are null and would prove nothing about whether
    TypeScript declares them.
    """
    node: Any = payload
    for part in path.split("."):
        node = node[part]
    if not isinstance(node, list):
        return set(node)
    return set(next((item for item in node if not item.get("is_anchor")), node[0]))


def test_every_report_field_is_declared_in_typescript(
    report: ComparisonReport, ts_fields: dict[str, set[str]]
) -> None:
    missing = set(report.to_dict()) - ts_fields["ComparisonReport"]

    assert not missing, (
        f"ComparisonReport does not declare {sorted(missing)}; the report carries them and the "
        f"page would silently omit figures the reader came for"
    )


def test_typescript_declares_no_report_field_python_does_not_emit(
    report: ComparisonReport, ts_fields: dict[str, set[str]]
) -> None:
    """A field the viewer expects and the report lacks would stop the page drawing at all."""
    emitted = set(report.to_dict())

    assert ts_fields["ComparisonReport"] <= emitted, (
        f"ComparisonReport declares {sorted(ts_fields['ComparisonReport'] - emitted)}, which the "
        f"report never emits"
    )


@pytest.mark.parametrize(("path", "interface"), NESTED)
def test_every_nested_field_is_declared_in_typescript(
    path: str, interface: str, report: ComparisonReport, ts_fields: dict[str, set[str]]
) -> None:
    emitted = _keys_at(report.to_dict(), path)

    assert not (emitted - ts_fields[interface]), (
        f"{interface} does not declare {sorted(emitted - ts_fields[interface])} from {path}"
    )


@pytest.mark.parametrize(("path", "interface"), NESTED)
def test_every_nested_typescript_field_is_emitted_by_the_report(
    path: str, interface: str, report: ComparisonReport, ts_fields: dict[str, set[str]]
) -> None:
    emitted = _keys_at(report.to_dict(), path)

    assert ts_fields[interface] <= emitted, (
        f"{interface} declares {sorted(ts_fields[interface] - emitted)}, which the report never "
        f"emits under {path}"
    )


def test_every_control_field_is_declared_in_typescript(
    report: ComparisonReport, ts_fields: dict[str, set[str]]
) -> None:
    """The control's conclusion is the finding, so it is checked like any other block.

    Separate from the `NESTED` cases because it is one object rather than a list, and
    because it is the only field the report can legitimately emit as `null` -- a
    bundle with no manual control says so instead of omitting the section.
    """
    control = report.to_dict()["control"]
    assert control is not None, "this report was built with a control, so it must have one"

    assert set(control) == ts_fields["ControlConclusion"]


def test_the_viewer_rejects_a_field_the_report_gained(ts_fields: dict[str, set[str]]) -> None:
    """The drift guard itself, asserted rather than assumed.

    `requireExactKeys` is what turns a report that grew a field into a visible
    failure instead of a page quietly missing a number. The field-level tests above
    would still pass if that check were dropped, so its presence is asserted here --
    it is the only place the guarantee actually lives.
    """
    assert "requireExactKeys" in TS_PATH.read_text()
    assert ts_fields["ConditionSummary"], "the interface under test declares nothing"


def test_the_report_is_json_serialisable_with_the_shape_the_viewer_expects(
    report: ComparisonReport,
) -> None:
    """Belt and braces: the emitted document is JSON, and its conditions are a list.

    The TypeScript parser is strict about types, and the cheapest way to know the
    Python side is emitting the types it expects is to look at real serialised
    output rather than at the dataclass.
    """
    payload = json.loads(report.to_json())

    assert isinstance(payload["conditions"], list)
    assert all(isinstance(c["median_marker_ms"], float) for c in payload["conditions"])
    assert all(isinstance(c["is_anchor"], bool) for c in payload["conditions"])
    assert isinstance(payload["reporter_claimed_offset_ms"], dict)
    assert all(isinstance(v, float) for v in payload["reporter_claimed_offset_ms"].values())

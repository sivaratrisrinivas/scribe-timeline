"""Turning a live session's events into a run record.

The network is the one part that cannot be tested offline, so everything
downstream of it is factored to be testable on its own: given the events a
session produced, build the record that gets saved and published.

The echoed configuration is read from `session_started` rather than from what was
sent, because the original report found the server echoing VAD durations that did
not account for the observed offset. Assuming sent and echoed agree would hide
exactly the thing worth checking.
"""

from __future__ import annotations

import json

import pytest

from pcm_fixtures import SAMPLE_RATE, ramp_pcm
from scribe_timeline.analysis.matching import MATCH_RULE, locate_marker
from scribe_timeline.audio.family import MARKER_TEXT, build_vad_family
from scribe_timeline.audio.timeline import Clip
from scribe_timeline.capture.plan import CapturePlan, build_plan
from scribe_timeline.capture.recorder import SessionCapture, build_run_record
from scribe_timeline.records import RunRecord


def _observe_marker(capture: SessionCapture, *, at_ms: float, start_s: float = 12.2) -> None:
    """Append the commit a real session would end with.

    Most assertions here are about something other than the marker, but every
    record needs one, so it is supplied rather than repeated in each test.
    """
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": MARKER_TEXT, "start": start_s, "end": start_s + 0.58}]},
        at_ms=at_ms,
    )


def _plan() -> tuple[CapturePlan, dict[str, Clip]]:
    clips = {
        "earlier": Clip(id="earlier", pcm=ramp_pcm(4000), sample_rate=SAMPLE_RATE),
        "marker": Clip(id="marker", pcm=ramp_pcm(4000), sample_rate=SAMPLE_RATE),
    }
    family = build_vad_family(
        clips=clips,
        sample_rate=SAMPLE_RATE,
        total_samples=SAMPLE_RATE * 14,
        earlier_starts=(0, SAMPLE_RATE * 6),
        final_marker_start=SAMPLE_RATE * 12,
    )
    return build_plan(condition=family[0], clips=clips, marker_text=MARKER_TEXT), clips


def test_record_pairs_the_plan_with_what_the_server_returned() -> None:
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe("session_started", {"config": {"model_id": "scribe_v2_realtime"}}, at_ms=0.0)
    capture.observe(
        "committed_transcript_with_timestamps",
        {
            "text": "Zorblax",
            "words": [{"text": "Zorblax", "start": 12.2, "end": 12.78, "logprob": -1.29}],
        },
        at_ms=14_200.0,
    )

    record = build_run_record(
        plan=plan, clips=clips, capture=capture, run_id="r1", repeat_index=0
    )

    assert record.condition_id == plan.condition_id
    assert record.commit_strategy == plan.commit_strategy
    assert record.manifest.markers[MARKER_TEXT] == plan.marker_start_sample
    assert [w.text for w in record.words] == [MARKER_TEXT]
    # The API returns seconds; the record stores milliseconds.
    assert record.words[0].start_ms == 12_200.0
    assert record.words[0].end_ms == 12_780.0


def test_record_carries_events_unedited() -> None:
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe("session_started", {"config": {"model_id": "x"}}, at_ms=0.0)
    capture.observe("partial_transcript", {"text": "Zor"}, at_ms=100.0)
    capture.observe("partial_transcript", {"text": "Zorblax"}, at_ms=200.0)
    _observe_marker(capture, at_ms=300.0)

    record = build_run_record(plan=plan, clips=clips, capture=capture, run_id="r2", repeat_index=0)

    assert [e.type for e in record.events[1:3]] == ["partial_transcript", "partial_transcript"]
    assert record.events[1].payload == {"text": "Zor"}


def test_echoed_config_comes_from_the_server_not_the_request() -> None:
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe(
        "session_started",
        {
            "config": {
                "model_id": "scribe_v2_realtime",
                "language_code": "en",
                "sample_rate": SAMPLE_RATE,
                "include_timestamps": True,
                "commit_strategy": "vad",
                "vad_silence_threshold_secs": 2.0,
                "vad_threshold": 0.5,
                "min_speech_duration_ms": 200,
                "min_silence_duration_ms": 200,
            }
        },
        at_ms=0.0,
    )
    _observe_marker(capture, at_ms=14_000.0)

    record = build_run_record(plan=plan, clips=clips, capture=capture, run_id="r3", repeat_index=0)

    # The values here are the server's, deliberately different from what we sent.
    assert record.echoed_config.vad_silence_threshold_secs == 2.0
    assert record.echoed_config.min_speech_duration_ms == 200


def test_arrival_latency_is_recorded_separately_from_the_sample_clock() -> None:
    """Arrival time and audio-clock time are different things and must not be merged."""
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe("session_started", {"config": {"model_id": "scribe_v2_realtime"}}, at_ms=0.0)
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": MARKER_TEXT, "start": 12.2, "end": 12.78}]},
        at_ms=14_200.0,
    )

    record = build_run_record(plan=plan, clips=clips, capture=capture, run_id="r4", repeat_index=0)

    assert record.words[0].start_ms == 12_200.0  # audio sample clock, converted from seconds
    assert record.events[-1].received_at_ms == 14_200.0  # arrival clock
    assert record.events[-1].clock_origin == "monotonic_since_connect"


def test_timestamps_are_converted_from_the_unit_the_api_actually_used() -> None:
    """The API returns seconds, not milliseconds.

    Found on the first live capture: a marker at 12.0s came back as `12.2`. Storing
    that in a field named `start_ms` would be wrong by 1000x while still looking
    like a plausible timestamp, so the conversion is explicit and the raw unit
    travels with the record.
    """
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe("session_started", {"config": {"model_id": "x"}}, at_ms=0.0)
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": MARKER_TEXT, "start": 12.2, "end": 12.78}]},
        at_ms=1.0,
    )

    record = build_run_record(
        plan=plan, clips=clips, capture=capture, run_id="unit", repeat_index=0
    )

    assert record.source_timestamp_unit == "seconds"
    assert record.words[0].start_ms == 12_200.0
    # The raw value is still recoverable from the unedited event.
    assert record.events[-1].payload["words"][0]["start"] == 12.2


def test_a_word_duration_reads_as_speech_not_as_a_microsecond() -> None:
    """0.58 source units is ~580ms, which is a plausible spoken word.

    Read as milliseconds it would be 0.58ms -- physically impossible, and the clue
    that the unit is seconds.
    """
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe("session_started", {"config": {"model_id": "x"}}, at_ms=0.0)
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": MARKER_TEXT, "start": 12.2, "end": 12.78}]},
        at_ms=1.0,
    )

    record = build_run_record(
        plan=plan, clips=clips, capture=capture, run_id="dur", repeat_index=0
    )

    duration_ms = record.words[0].end_ms - record.words[0].start_ms
    assert 300.0 < duration_ms < 1500.0


def test_match_rule_travels_with_the_record() -> None:
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe("session_started", {"config": {"model_id": "x"}}, at_ms=0.0)
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": MARKER_TEXT, "start": 1.0, "end": 2.0}]},
        at_ms=3.0,
    )

    record = build_run_record(plan=plan, clips=clips, capture=capture, run_id="r5", repeat_index=0)

    assert record.match_rule == MATCH_RULE


def test_record_notes_whether_a_credential_was_present() -> None:
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe("session_started", {"config": {"model_id": "x"}}, at_ms=0.0)
    _observe_marker(capture, at_ms=14_000.0)

    record = build_run_record(
        plan=plan, clips=clips, capture=capture, run_id="r6", repeat_index=0, api_key_present=True
    )

    assert record.api_key_present is True


def test_a_run_whose_marker_is_missing_fails_rather_than_recording_zero() -> None:
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe("session_started", {"config": {"model_id": "x"}}, at_ms=0.0)
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": "something else", "start": 1.0, "end": 2.0}]},
        at_ms=3.0,
    )

    with pytest.raises(Exception, match="not found"):
        build_run_record(plan=plan, clips=clips, capture=capture, run_id="r7", repeat_index=0)


def test_record_serialises_to_a_publishable_json_document() -> None:
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe("session_started", {"config": {"model_id": "x"}}, at_ms=0.0)
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": MARKER_TEXT, "start": 1.0, "end": 2.0}]},
        at_ms=3.0,
    )

    record = build_run_record(plan=plan, clips=clips, capture=capture, run_id="r8", repeat_index=0)
    payload = json.loads(record.model_dump_json())

    assert payload["run_id"] == "r8"
    assert RunRecord.model_validate(payload) == record


def test_capture_collects_events_in_arrival_order() -> None:
    capture = SessionCapture()
    capture.observe("a", {"n": 1}, at_ms=1.0)
    capture.observe("b", {"n": 2}, at_ms=2.0)
    capture.observe("c", {"n": 3}, at_ms=3.0)

    assert [e.type for e in capture.events] == ["a", "b", "c"]
    assert [e.received_at_ms for e in capture.events] == [1.0, 2.0, 3.0]


def test_capture_keeps_every_timestamped_word_across_commits() -> None:
    capture = SessionCapture()
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": "one", "start": 0.0, "end": 1.0}]},
        at_ms=1.0,
    )
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": "two", "start": 6_000.0, "end": 6_500.0}]},
        at_ms=7_000.0,
    )

    assert [w["text"] for w in capture.all_words()] == ["one", "two"]


def test_matched_marker_is_found_across_a_multi_commit_session() -> None:
    plan, clips = _plan()
    capture = SessionCapture()
    capture.observe("session_started", {"config": {"model_id": "x"}}, at_ms=0.0)
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": "Kalvik", "start": 100.0, "end": 400.0}]},
        at_ms=1_000.0,
    )
    capture.observe(
        "committed_transcript_with_timestamps",
        {"words": [{"text": MARKER_TEXT, "start": 11_984.0, "end": 12_093.0}]},
        at_ms=13_000.0,
    )

    record = build_run_record(plan=plan, clips=clips, capture=capture, run_id="r9", repeat_index=0)

    assert [w.text for w in record.words] == ["Kalvik", MARKER_TEXT]
    assert locate_marker(capture.all_words(), MARKER_TEXT).start_ms == 11_984.0

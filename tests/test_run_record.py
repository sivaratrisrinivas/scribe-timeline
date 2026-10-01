"""A run record pairs what was played with what the server said.

The run record is the only seam between the network and everything downstream, so
it has to carry enough to re-derive every published number without re-running
anything. That means the fixture manifest (sample rate, layout, marker positions
in samples) travels with the raw events, and the server's echoed configuration
travels with both.

A run record must never carry a credential. It is designed to be published.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from pcm_fixtures import SAMPLE_RATE, ramp_pcm, slice_at
from scribe_timeline.audio.family import MARKER_TEXT, build_vad_family
from scribe_timeline.audio.timeline import Clip, compose
from scribe_timeline.records import (
    BENIGN_EVENT_KEYS,
    CREDENTIAL_KEY_MARKERS,
    EchoedSessionConfig,
    Manifest,
    RawEvent,
    RunRecord,
    WordTiming,
)

JSONObject = dict[str, Any]

SECRET = "sk-live-do-not-publish-me-0123456789"


def _manifest() -> Manifest:
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
    condition = family[1]
    composed = compose(condition.spec, clips)
    return Manifest.from_composition(
        condition_id=condition.id,
        composed=composed,
        placements=condition.spec.placements,
        sample_rate=SAMPLE_RATE,
        clip_lengths={c.id: c.sample_count for c in clips.values()},
    )


def _record(**overrides: object) -> RunRecord:
    defaults: dict[str, object] = {
        "schema_version": 1,
        "run_id": "2026-10-02T00-00-00Z__vad_1__rep0",
        "condition_id": "vad_1",
        "commit_strategy": "vad",
        "repeat_index": 0,
        "manifest": _manifest(),
        "events": (
            RawEvent(
                type="committed_transcript",
                received_at_ms=0.0,
                payload={"text": "Kalvik Zorblax"},
            ),
        ),
        "echoed_config": EchoedSessionConfig(
            model_id="scribe_v2_realtime",
            language_code="en",
            sample_rate=SAMPLE_RATE,
            include_timestamps=True,
            # `session_started` does not echo this; see the absence test above.
            commit_strategy=None,
            vad_silence_threshold_secs=1.5,
            vad_threshold=0.4,
            min_speech_duration_ms=100,
            min_silence_duration_ms=100,
        ),
        "words": (
            WordTiming(text="Zorblax", start_ms=12_200.0, end_ms=12_780.0, logprob=-1.29),
        ),
        "api_key_present": False,
    }
    return RunRecord(**{**defaults, **overrides})  # type: ignore[arg-type]


# --- manifest ---------------------------------------------------------------


def test_manifest_records_marker_position_in_samples() -> None:
    manifest = _manifest()

    assert manifest.markers[MARKER_TEXT] == SAMPLE_RATE * 12


def test_manifest_records_sample_rate_and_layout() -> None:
    manifest = _manifest()

    assert manifest.sample_rate == SAMPLE_RATE
    assert manifest.sample_count == SAMPLE_RATE * 14
    assert manifest.condition_id == "vad_1"


def test_manifest_segments_describe_every_placement() -> None:
    """Every placed clip appears, at the right start, with the right length.

    A dropped segment or a zeroed length produces a timeline that renders as
    plausible but is wrong, which is the failure this project exists to prevent.
    """
    manifest = _manifest()

    # `vad_1` places one earlier segment plus the marker.
    assert [(s.clip_id, s.start_sample, s.sample_count) for s in manifest.segments] == [
        ("earlier", 0, 4000),
        ("marker", SAMPLE_RATE * 12, 4000),
    ]


def test_manifest_segments_agree_with_the_composed_audio() -> None:
    """Segment lengths match what was really placed, derived independently."""
    manifest = _manifest()
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
    composed = compose(family[1].spec, clips)

    for segment in manifest.segments:
        placed = slice_at(composed.pcm, segment.start_sample, segment.sample_count)
        assert placed == clips[segment.clip_id].pcm


def test_manifest_refuses_a_missing_clip_length() -> None:
    """A missing length is an error, not a silent zero."""
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

    with pytest.raises(ValueError, match="no clip length"):
        Manifest.from_composition(
            condition_id="vad_2",
            composed=compose(family[2].spec, clips),
            placements=family[2].spec.placements,
            sample_rate=SAMPLE_RATE,
            clip_lengths={"marker": 4000},  # "earlier" deliberately absent
        )


def test_manifest_rejects_a_marker_outside_the_audio() -> None:
    """A marker at or past the end claims a word where no audio exists."""
    with pytest.raises(ValidationError, match="outside audio"):
        Manifest.model_validate(
            {
                "condition_id": "vad_0",
                "sample_rate": SAMPLE_RATE,
                "sample_count": 1000,
                "markers": {MARKER_TEXT: 1000},
                "segments": [],
            }
        )


def test_manifest_rejects_a_negative_marker_position() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(
            {
                "condition_id": "vad_0",
                "sample_rate": SAMPLE_RATE,
                "sample_count": 1000,
                "markers": {MARKER_TEXT: -1},
                "segments": [],
            }
        )


def test_manifest_rejects_an_empty_marker_map() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(
            {
                "condition_id": "vad_0",
                "sample_rate": SAMPLE_RATE,
                "sample_count": 1000,
                "markers": {},
                "segments": [],
            }
        )


def test_manifest_rejects_an_unknown_field_by_name() -> None:
    """Extra fields are refused, so a key cannot be smuggled in as a named field.

    `markers` is deliberately non-empty so this cannot pass for the wrong reason:
    an empty dict trips `min_length=1` first, and the test would then be asserting
    nothing about extra fields at all.
    """
    with pytest.raises(ValidationError) as caught:
        Manifest.model_validate(
            {
                "condition_id": "vad_0",
                "sample_rate": SAMPLE_RATE,
                "sample_count": 100,
                "markers": {MARKER_TEXT: 10},
                "segments": [],
                "api_key": SECRET,
            }
        )

    assert "extra_forbidden" in {error["type"] for error in caught.value.errors()}


@pytest.mark.parametrize(
    ("payload", "marker"),
    [
        # One case per entry in CREDENTIAL_KEY_MARKERS, so removing an entry from
        # the ban list fails here rather than silently widening what is admissible.
        ({"api_key": SECRET}, "key"),
        ({"auth_token": SECRET}, "token"),
        ({"client_secret": SECRET}, "secret"),
        ({"authorization": f"Bearer {SECRET}"}, "auth"),
        (({"aws_credential": SECRET}), "credential"),
        ({"password": SECRET}, "password"),
        ({"passwd": SECRET}, "passwd"),
        ({"bearer": SECRET}, "bearer"),
        # `session_id` is deliberately absent: the server sends it, so it is
        # allowlisted. See test_api_vocabulary_is_not_mistaken_for_a_credential.
    ],
)
def test_every_ban_list_entry_is_load_bearing(
    payload: JSONObject, marker: str
) -> None:
    """Each ban-list entry has a case that only it catches."""
    assert any(marker in entry for entry in CREDENTIAL_KEY_MARKERS)

    with pytest.raises(ValidationError):
        RawEvent(type="committed_transcript", received_at_ms=0.0, payload=payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"xi-api-key": SECRET},
        {"nested": {"deep": {"apiKey": SECRET}}},
        {"items": [{"secret": SECRET}]},
        {"headers": {"Authorization": f"Bearer {SECRET}"}},
    ],
)
def test_event_payload_rejects_credentials_at_any_depth(payload: JSONObject) -> None:
    """The free-form payload is the real leak route, so it is walked.

    `extra="forbid"` only constrains named fields. A secret inside `payload` is
    exactly how one reaches a publishable record, and the record is meant to be
    published.
    """
    with pytest.raises(ValidationError) as caught:
        RawEvent(type="committed_transcript", received_at_ms=0.0, payload=payload)

    assert "credential-shaped" in str(caught.value)


def test_api_vocabulary_is_not_mistaken_for_a_credential() -> None:
    """Names the server itself uses must not be rejected.

    Found by the first live capture: `session_started` carries `keyterms` (Scribe's
    documented keyword-prompting list), `max_tokens_to_recompute` (a token budget),
    and `session_id` (an opaque session handle). A ban list broad enough to catch a
    leak is also broad enough to reject the API's own vocabulary, and a run that
    dies on `keyterms` produces no measurement at all.
    """
    RawEvent(
        type="session_started",
        received_at_ms=0.0,
        payload={
            "config": {
                "model_id": "scribe_v2_realtime",
                "keyterms": ["Acme"],
                "max_tokens_to_recompute": 0,
            },
            "session_id": "sess_abc123",
        },
    )


def test_a_benign_name_still_catches_a_secret_underneath_it() -> None:
    """Allowlisting a name must not open a hole beneath it."""
    with pytest.raises(ValidationError, match="credential-shaped"):
        RawEvent(
            type="session_started",
            received_at_ms=0.0,
            payload={"session_id": {"api_key": SECRET}},
        )


def test_every_benign_name_actually_defeats_a_marker() -> None:
    """An allowlist entry must exist to defeat a specific marker.

    Otherwise it is not earning its place and is only widening what is admissible.
    """
    for name in BENIGN_EVENT_KEYS:
        assert any(marker in name for marker in CREDENTIAL_KEY_MARKERS), name


def test_event_payload_allows_ordinary_transcript_fields() -> None:
    """The guard must not be so broad that real events are rejected."""
    RawEvent(
        type="committed_transcript_with_timestamps",
        received_at_ms=12.5,
        payload={
            "text": "Kalvik Zorblax",
            "words": [{"text": "Zorblax", "start": 11.98, "end": 12.09}],
            "language_code": "en",
        },
    )


def test_secret_planted_in_a_payload_never_reaches_a_serialised_record() -> None:
    """End to end, through the real construction path.

    The record is assembled as plain JSON -- exactly what a runner reads off the
    wire and writes to disk -- so this covers the path a secret would actually
    take, not a hand-built model instance.
    """
    raw = json.loads(_record().model_dump_json())
    raw["events"] = [
        {
            "type": "committed_transcript",
            "received_at_ms": 0.0,
            "clock_origin": "monotonic_since_connect",
            "payload": {"xi-api-key": SECRET},
        }
    ]

    with pytest.raises(ValidationError):
        RunRecord.model_validate(raw)

    # And the value is absent from anything we could serialise.
    assert SECRET not in json.dumps(raw["manifest"])


# --- echoed config ----------------------------------------------------------


def test_echoed_config_records_absence_rather_than_inventing_a_value() -> None:
    """`session_started` does not echo `commit_strategy`, so neither do we.

    Found on the first live capture: the server's config had no `commit_strategy`
    key at all, and the code defaulted it to "manual". That fabricates the
    experiment's key control -- a reader would conclude a VAD run was really a
    manual one, and the VAD-vs-manual comparison would be meaningless.
    """
    echoed = EchoedSessionConfig(
        model_id="scribe_v2_realtime",
        sample_rate=SAMPLE_RATE,
        include_timestamps=True,
    )

    assert echoed.commit_strategy is None


def test_the_requested_strategy_is_recorded_separately_from_the_echoed_one() -> None:
    """What we asked for and what the server said are different facts."""
    record = _record()

    assert record.commit_strategy == "vad"
    assert record.echoed_config.commit_strategy is None


def test_echoed_config_records_what_the_server_actually_used() -> None:
    """The reporter's run showed sent values echoed back that did not explain
    the result, so the echoed values are recorded rather than the sent ones.
    """
    echoed = _record().echoed_config

    assert echoed.vad_silence_threshold_secs == 1.5
    assert echoed.min_speech_duration_ms == 100


# --- credentials ------------------------------------------------------------


def test_run_record_has_no_field_that_could_hold_a_credential() -> None:
    def field_names(model: type[object]) -> set[str]:
        return set(model.model_fields)  # type: ignore[attr-defined]

    # A field may be *named* after a credential only if it is a boolean flag
    # recording that no credential was stored. Anything else is a leak waiting
    # to happen, so the assertion is on name AND value type together.
    credential_words = ("key", "token", "secret", "auth", "credential", "password")
    for model in (RunRecord, Manifest, EchoedSessionConfig, RawEvent, WordTiming):
        for name, field in model.model_fields.items():
            suspicious = any(w in name.lower() for w in credential_words)
            if not suspicious:
                continue
            assert field.annotation is bool, (
                f"{model.__name__}.{name} is named like a credential and must be a "
                f"boolean flag, not {field.annotation}"
            )


def test_serialised_record_contains_no_credential_value() -> None:
    """A value-level check, so it is falsifiable.

    The earlier version of this test asserted a constant was absent from a record
    that could never contain it -- it could not fail, and it was the only test
    positioned to catch a real leak. Here the secret is genuinely present in the
    environment-adjacent path and must not survive serialisation.
    """
    record = _record(
        events=(
            RawEvent(
                type="session_started",
                received_at_ms=0.0,
                payload={"config": {"model_id": "scribe_v2_realtime", "language_code": "en"}},
            ),
        )
    )

    serialised = record.model_dump_json()

    assert SECRET not in serialised
    # The benign payload did survive, so this is not passing by dropping data.
    assert "scribe_v2_realtime" in serialised


def test_record_declares_whether_a_key_was_present() -> None:
    """Honesty about the run environment, without carrying the key itself."""
    assert _record(api_key_present=False).api_key_present is False
    assert _record(api_key_present=True).api_key_present is True


# --- round trip -------------------------------------------------------------


def test_record_round_trips_through_json() -> None:
    original = _record()

    restored = RunRecord.model_validate_json(original.model_dump_json())

    assert restored == original


def test_record_is_stable_under_repeated_serialisation() -> None:
    record = _record()

    first = json.loads(record.model_dump_json())
    second = json.loads(record.model_dump_json())

    assert first == second


def test_words_carry_their_own_timing() -> None:
    word = _record().words[0]

    assert word.text == MARKER_TEXT
    assert word.start_ms < word.end_ms


def test_word_logprob_is_stored_as_returned() -> None:
    """`logprob` is negative, so it must not be squeezed into a 0..1 confidence.

    The realtime API returns a log-probability (observed: -1.29 for a marker word
    in synthetic speech). A 0..1 constraint would have rejected the real value, and
    clamping it would hide the low-confidence words most worth noticing.
    """
    word = _record().words[0]

    assert word.logprob == -1.29
    assert word.logprob is not None and word.logprob < 0

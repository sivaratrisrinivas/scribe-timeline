"""Turning a live session's events into a run record.

The WebSocket is the one part that cannot be exercised offline, so everything
downstream of it is factored to be testable on its own: given the events a
session produced, build the record that gets saved and published. `run_condition`
does the streaming and then calls straight into this.

Two decisions are load-bearing:

* **The echoed configuration is read from `session_started`, not from the
  request.** The original report found the server echoing VAD durations that did
  not account for the offset it observed, so sent and echoed values are not
  assumed to agree. Recording the request instead would hide the finding.
* **Arrival time and audio-clock time stay separate.** `received_at_ms` is wall
  time since connect; `WordTiming.start_ms` is position on the input sample
  clock. Conflating them would make a latency change look like a timing error.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scribe_timeline.analysis.matching import MATCH_RULE, locate_marker
from scribe_timeline.audio.timeline import Clip
from scribe_timeline.capture.plan import CapturePlan
from scribe_timeline.records import (
    EchoedSessionConfig,
    Manifest,
    RawEvent,
    RunRecord,
    WordTiming,
)

SCHEMA_VERSION = 1

_TIMESTAMPED_EVENT = "committed_transcript_with_timestamps"
_SESSION_STARTED = "session_started"

#: Unit the realtime API actually returns word timestamps in.
#:
#: Observed on 2026-10-02: with the marker clip placed at 6.0s the API returned
#: `start: 6.18`, and at 12.0s it returned `12.2`. Word durations came back as
#: ~0.58-0.60, which is ~580-600ms and therefore correct for speech. The values
#: track the clip position in *seconds*.
#:
#: This matters because a `ms`-named field holding 12.2 is wrong by a factor of
#: 1000 and still looks like a plausible number -- exactly the failure this project
#: exists to catch. So the conversion is explicit, and the raw unit travels with
#: every record.
SOURCE_TIMESTAMP_UNIT = "seconds"
_MS_PER_SOURCE_UNIT = 1000.0


class SessionCapture:
    """Accumulates the events one session produced, in arrival order.

    `observe` can fail -- a payload may trip the credential guard -- and the SDK
    invokes handlers inside a broad `except` that only prints. So a rejection is
    retained here and re-raised once streaming finishes, instead of surfacing as an
    unexplained timeout.
    """

    def __init__(self) -> None:
        self._events: list[RawEvent] = []
        self._rejected: list[tuple[str, Exception]] = []

    def observe(self, event_type: str, payload: dict[str, Any], *, at_ms: float) -> None:
        try:
            self._events.append(RawEvent(type=event_type, received_at_ms=at_ms, payload=payload))
        except ValueError as exc:
            self._rejected.append((event_type, exc))

    def raise_if_rejected(self) -> None:
        """Re-raise the first rejected event, naming the event type."""
        if not self._rejected:
            return
        event_type, exc = self._rejected[0]
        raise type(exc)(f"{event_type} event was rejected: {exc}") from exc

    @property
    def events(self) -> tuple[RawEvent, ...]:
        return tuple(self._events)

    def all_words(self) -> list[dict[str, Any]]:
        """Every word from every timestamped commit, in order.

        A session commits more than once, so the measured marker may arrive in any
        commit. Collecting them all first is what lets the marker be found wherever
        it landed.
        """
        words: list[dict[str, Any]] = []
        for event in self._events:
            if event.type != _TIMESTAMPED_EVENT:
                continue
            raw = event.payload.get("words")
            if isinstance(raw, list):
                words.extend(word for word in raw if isinstance(word, dict))
        return words

    def echoed_config(self) -> EchoedSessionConfig:
        """The session configuration as the server reported it."""
        for event in self._events:
            if event.type != _SESSION_STARTED:
                continue
            config = event.payload.get("config")
            if isinstance(config, dict):
                return _echoed_from(config)
        raise ValueError(
            "no session_started event was captured, so the server's configuration "
            "is unknown; refusing to record values we only sent"
        )


def _echoed_from(config: Mapping[str, Any]) -> EchoedSessionConfig:
    def optional_float(key: str) -> float | None:
        value = config.get(key)
        return float(value) if isinstance(value, int | float) else None

    def optional_int(key: str) -> int | None:
        value = config.get(key)
        return int(value) if isinstance(value, int | float) else None

    # Absent stays absent. `session_started` does not echo `commit_strategy`, and
    # defaulting it would invent the experiment's key control.
    raw_strategy = config.get("commit_strategy")
    if raw_strategy is not None and raw_strategy not in ("vad", "manual"):
        raise ValueError(f"server echoed an unknown commit strategy: {raw_strategy!r}")

    return EchoedSessionConfig(
        model_id=str(config.get("model_id", "unknown")),
        language_code=str(config["language_code"]) if config.get("language_code") else None,
        sample_rate=int(config.get("sample_rate") or 16_000),
        include_timestamps=bool(config.get("include_timestamps", False)),
        commit_strategy=raw_strategy,
        vad_silence_threshold_secs=optional_float("vad_silence_threshold_secs"),
        vad_threshold=optional_float("vad_threshold"),
        min_speech_duration_ms=optional_int("min_speech_duration_ms"),
        min_silence_duration_ms=optional_int("min_silence_duration_ms"),
    )


def build_run_record(
    *,
    plan: CapturePlan,
    clips: Mapping[str, Clip],
    capture: SessionCapture,
    run_id: str,
    repeat_index: int,
    api_key_present: bool = False,
) -> RunRecord:
    """Assemble the run record for one condition and one repeat.

    Raises `MarkerNotFound` if the marker word is absent from the transcript. A run
    without its marker supports no measurement, and substituting a nearby word
    would produce a confident wrong number.
    """
    words = capture.all_words()
    locate_marker(words, plan.marker_text)  # fail loudly before anything is written

    return RunRecord(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        condition_id=plan.condition_id,
        commit_strategy=plan.commit_strategy,  # type: ignore[arg-type]
        repeat_index=repeat_index,
        manifest=Manifest.from_composition(
            condition_id=plan.condition_id,
            composed=plan.composed,
            placements=plan.placements,
            sample_rate=plan.sample_rate,
            clip_lengths={clip_id: clip.sample_count for clip_id, clip in clips.items()},
        ),
        events=capture.events,
        echoed_config=capture.echoed_config(),
        words=tuple(
            WordTiming(
                text=str(word.get("text", "")),
                # Converted explicitly from the unit the API actually used. The raw
                # values remain in `events`, so the conversion is always checkable.
                start_ms=float(word["start"]) * _MS_PER_SOURCE_UNIT,
                end_ms=float(word["end"]) * _MS_PER_SOURCE_UNIT,
                logprob=(
                    float(word["logprob"])
                    if isinstance(word.get("logprob"), int | float)
                    else None
                ),
            )
            for word in words
            if isinstance(word.get("text"), str)
        ),
        api_key_present=api_key_present,
        match_rule=MATCH_RULE,
        source_timestamp_unit=SOURCE_TIMESTAMP_UNIT,
    )




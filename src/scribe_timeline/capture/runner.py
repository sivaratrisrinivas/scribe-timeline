"""Streaming one condition to Scribe Realtime and recording what came back.

This is the only module that touches the network, and it is deliberately thin: it
carries out a `CapturePlan` and hands the events to the recorder. Everything worth
testing -- pacing arithmetic, marker matching, record assembly -- lives below it
and is verified offline.

Pacing is the reason the audio is not simply dumped and closed. The server's voice
activity detection judges speech boundaries from the rate audio arrives, so sending
14 seconds of audio in 14 milliseconds would have it commit against a timeline that
does not exist. Chunks are therefore sent on a wall-clock schedule matched to their
duration.

The API key is read from the environment and never passed to, or stored by, this
module. Its presence is recorded as a boolean so a run record is honest about the
environment it came from.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from datetime import datetime, timezone
from typing import Any

from elevenlabs.client import ElevenLabs
from elevenlabs.realtime.connection import RealtimeConnection, RealtimeEvents
from elevenlabs.realtime.scribe import AudioFormat
from elevenlabs.realtime.scribe import CommitStrategy as SdkCommitStrategy

from scribe_timeline.audio.family import Condition
from scribe_timeline.audio.speech import assert_speakable
from scribe_timeline.audio.timeline import Clip
from scribe_timeline.capture.completion import CompletionCondition
from scribe_timeline.capture.plan import CapturePlan, boundaries_due, build_plan
from scribe_timeline.capture.recorder import SessionCapture, build_run_record
from scribe_timeline.records import CommitStrategy, RunRecord

MODEL_ID = "scribe_v2_realtime"
LANGUAGE_CODE = "en"

#: How long to keep listening for the marker's commit after the last audio chunk.
LISTEN_TIMEOUT_SECONDS = 30.0

#: Upper bound on timestamped commits before a capture gives up.
#:
#: Generous on purpose: three conditions produce at most three commits plus a
#: flush, so this only ever fires on a session that has gone wrong. It exists so
#: a pathological stream cannot hold a socket open indefinitely -- and it is
#: reported as an outcome distinct from finding the marker, so a run that trips it
#: fails loudly instead of being published as if it held the evidence.
MAX_COMMITS = 12

#: The reporter's configuration, pinned so a rerun is comparable.
VAD_OPTIONS: dict[str, float | int] = {
    "vad_silence_threshold_secs": 1.5,
    "vad_threshold": 0.4,
    "min_speech_duration_ms": 100,
    "min_silence_duration_ms": 100,
}

#: Events worth keeping. Partial transcripts are noisy but show the server was
#: tracking speech, which is useful context when a commit returns nothing.
RECORDED_EVENTS = (
    RealtimeEvents.SESSION_STARTED,
    RealtimeEvents.PARTIAL_TRANSCRIPT,
    RealtimeEvents.COMMITTED_TRANSCRIPT,
    RealtimeEvents.COMMITTED_TRANSCRIPT_WITH_TIMESTAMPS,
)


class CaptureError(RuntimeError):
    """The session could not be completed."""


def _key_present() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def _require_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise CaptureError(
            "ELEVENLABS_API_KEY is not set. Export it before running capture; "
            "nothing else in this project needs it."
        )
    return key


def _chunks_of(plan: CapturePlan) -> list[bytes]:
    width = 2
    return [
        plan.pcm[chunk.start_sample * width : (chunk.start_sample + chunk.length) * width]
        for chunk in plan.chunks
    ]


async def run_condition(
    *,
    condition: Condition,
    clips: dict[str, Clip],
    marker_text: str,
    run_id: str,
    repeat_index: int = 0,
    commit_strategy: CommitStrategy = "vad",
) -> RunRecord:
    """Stream one condition and return the run record for it."""
    _require_key()
    for clip_id, clip in clips.items():
        assert_speakable(clip.pcm, label=clip_id, sample_rate=clip.sample_rate)

    plan = build_plan(
        condition=condition,
        clips=clips,
        marker_text=marker_text,
        commit_strategy=commit_strategy,
    )
    capture = await _stream(plan)
    return build_run_record(
        plan=plan,
        clips=clips,
        capture=capture,
        run_id=run_id,
        repeat_index=repeat_index,
        api_key_present=_key_present(),
    )


async def _stream(plan: CapturePlan) -> SessionCapture:
    client = ElevenLabs(api_key=_require_key())
    capture = SessionCapture()
    connected_at: float | None = None
    finished = asyncio.Event()
    condition = CompletionCondition(marker_text=plan.marker_text, max_commits=MAX_COMMITS)

    connection: RealtimeConnection = await client.speech_to_text.realtime.connect(
        {
            "model_id": MODEL_ID,
            "audio_format": AudioFormat.PCM_16000,
            "sample_rate": plan.sample_rate,
            "commit_strategy": SdkCommitStrategy(plan.commit_strategy),
            "include_timestamps": True,
            "language_code": LANGUAGE_CODE,
            **VAD_OPTIONS,
        }
    )

    def elapsed_ms() -> float:
        return (time.monotonic() - (connected_at or time.monotonic())) * 1000

    def on_event(event: RealtimeEvents) -> Any:
        def handler(data: dict[str, Any]) -> None:
            capture.observe(event.value, data, at_ms=elapsed_ms())
            # Listen until the marker is actually in hand. Under VAD the earlier
            # segments commit first, so "a commit arrived" is not the same thing as
            # "the measurement is possible".
            condition.observe(event.value, data)
            if condition.satisfied:
                finished.set()

        return handler

    for event in RECORDED_EVENTS:
        connection.on(event, on_event(event))

    await _await_open(connection)
    connected_at = time.monotonic()

    # Under VAD the server picks its own cut points, so sending our own would
    # change the condition being measured. Under manual it makes none at all, and
    # the boundaries are what give the control its preceding commits.
    boundaries = plan.manual_commit_boundaries if plan.commit_strategy == "manual" else ()

    try:
        await _send_paced(connection, plan, boundaries)
        # Flush explicitly. Under VAD the server only commits once it has seen
        # `vad_silence_threshold_secs` of trailing silence, so a timeline that ends
        # close to the last word may never commit on its own. An explicit commit is
        # valid under both strategies and removes that dependency.
        await connection.commit()
        await asyncio.wait_for(finished.wait(), timeout=LISTEN_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        capture.raise_if_rejected()
        raise CaptureError(
            f"no commit carrying {plan.marker_text!r} arrived within "
            f"{LISTEN_TIMEOUT_SECONDS:.0f}s "
            f"({plan.sample_count} samples at {plan.sample_rate} Hz); "
            f"events seen: {[e.type for e in capture.events] or 'none'}"
        ) from exc
    finally:
        await connection.close()

    capture.raise_if_rejected()
    if condition.outcome != "marker-found":
        raise CaptureError(
            f"capture ended after {condition.commits_seen} timestamped commit(s) "
            f"without {plan.marker_text!r} in any of them; this run holds no "
            f"measurement. Events seen: {[e.type for e in capture.events] or 'none'}"
        )
    return capture


async def _await_open(connection: RealtimeConnection) -> None:
    """Wait until the socket is usable.

    The SDK starts its reader task on connect, so this only confirms the socket
    opened rather than polling for transcripts.
    """
    for _ in range(100):
        if getattr(connection, "websocket", None) is not None:
            return
        await asyncio.sleep(0.01)
    raise CaptureError("websocket did not open")


async def _send_paced(
    connection: RealtimeConnection,
    plan: CapturePlan,
    commit_boundaries: tuple[int, ...] = (),
) -> None:
    """Send each chunk on a wall-clock schedule matching its audio duration.

    Without this the server's VAD sees all the audio at once and commits against a
    timeline that never existed, so any timestamp it returned would describe audio
    that was not played at that rate.

    `commit_boundaries` are sample positions after which an explicit commit is
    requested. Only the manual strategy uses them; under VAD the server's own
    segmentation is the thing under test and must not be second-guessed here.
    """
    started = time.monotonic()
    commits_sent = 0
    for chunk, payload in zip(plan.chunks, _chunks_of(plan), strict=True):
        target = started + (chunk.start_sample / plan.sample_rate)
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        await connection.send({"audio_base_64": base64.b64encode(payload).decode("ascii")})

        for _ in range(
            boundaries_due(
                chunk.start_sample + chunk.length,
                commit_boundaries,
                already_sent=commits_sent,
            )
        ):
            await connection.commit()
            commits_sent += 1


def new_run_id(condition_id: str, repeat_index: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{stamp}__{condition_id}__rep{repeat_index}"

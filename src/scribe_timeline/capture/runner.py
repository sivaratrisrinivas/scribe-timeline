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
from elevenlabs.realtime.scribe import AudioFormat, CommitStrategy

from scribe_timeline.audio.family import Condition
from scribe_timeline.audio.speech import assert_speakable
from scribe_timeline.audio.timeline import Clip
from scribe_timeline.capture.plan import CapturePlan, build_plan
from scribe_timeline.capture.recorder import SessionCapture, build_run_record
from scribe_timeline.records import RunRecord

MODEL_ID = "scribe_v2_realtime"
LANGUAGE_CODE = "en"

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
    commit_strategy: str = "vad",
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
    commits_seen = 0
    finished = asyncio.Event()

    connection: RealtimeConnection = await client.speech_to_text.realtime.connect(
        {
            "model_id": MODEL_ID,
            "audio_format": AudioFormat.PCM_16000,
            "sample_rate": plan.sample_rate,
            "commit_strategy": CommitStrategy(plan.commit_strategy),
            "include_timestamps": True,
            "language_code": LANGUAGE_CODE,
            **VAD_OPTIONS,
        }
    )

    def elapsed_ms() -> float:
        return (time.monotonic() - (connected_at or time.monotonic())) * 1000

    def on_event(event: RealtimeEvents) -> Any:
        def handler(data: dict[str, Any]) -> None:
            nonlocal commits_seen
            capture.observe(event.value, data, at_ms=elapsed_ms())
            if event is RealtimeEvents.COMMITTED_TRANSCRIPT_WITH_TIMESTAMPS:
                commits_seen += 1
                finished.set()

        return handler

    for event in RECORDED_EVENTS:
        connection.on(event, on_event(event))

    await _await_open(connection)
    connected_at = time.monotonic()

    try:
        await _send_paced(connection, plan)
        # Flush explicitly. Under VAD the server only commits once it has seen
        # `vad_silence_threshold_secs` of trailing silence, so a timeline that ends
        # close to the last word may never commit on its own. An explicit commit is
        # valid under both strategies and removes that dependency.
        await connection.commit()
        await asyncio.wait_for(finished.wait(), timeout=30.0)
    except TimeoutError as exc:
        capture.raise_if_rejected()
        raise CaptureError(
            f"no timestamped commit arrived within 30s "
            f"({plan.sample_count} samples at {plan.sample_rate} Hz); "
            f"events seen: {[e.type for e in capture.events] or 'none'}"
        ) from exc
    finally:
        await connection.close()

    capture.raise_if_rejected()
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


async def _send_paced(connection: RealtimeConnection, plan: CapturePlan) -> None:
    """Send each chunk on a wall-clock schedule matching its audio duration.

    Without this the server's VAD sees all the audio at once and commits against a
    timeline that never existed, so any timestamp it returned would describe audio
    that was not played at that rate.
    """
    started = time.monotonic()
    for chunk, payload in zip(plan.chunks, _chunks_of(plan), strict=True):
        target = started + (chunk.start_sample / plan.sample_rate)
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        await connection.send({"audio_base_64": base64.b64encode(payload).decode("ascii")})


def new_run_id(condition_id: str, repeat_index: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{stamp}__{condition_id}__rep{repeat_index}"

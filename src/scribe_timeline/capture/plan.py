"""The capture plan: what to send, in what order, and how fast.

Pure arithmetic over already-composed audio. No network, no key, no sleeping --
`plan_chunks` and `build_plan` decide *what* happens, and the runner only carries
it out. That split is what makes the pacing testable: if the arithmetic were
buried in the async send loop, a dropped chunk or a mis-timed interval would only
show up as a plausible-looking timestamp.

Pacing is not a detail. Audio has to reach the server at the rate it was
recorded, or the server's voice activity detection sees a timeline that is not the
one on disk, and every timestamp it returns describes audio that was never played.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scribe_timeline.audio.family import Condition
from scribe_timeline.audio.timeline import Clip, ComposedTimeline, Placement, compose

CHUNK_SAMPLES = 1600
"""Samples per chunk: 100 ms at 16 kHz.

Small enough that voice activity detection sees speech onset promptly, large
enough that per-message overhead stays negligible.
"""

CHUNK_INTERVAL_MS = 100
"""Real time allowed per chunk. Paired with CHUNK_SAMPLES, this is realtime pacing."""


@dataclass(frozen=True)
class PlannedChunk:
    index: int
    start_sample: int
    length: int

    @property
    def offset_ms(self) -> float:
        return self.start_sample * 1000 / 16_000


def plan_chunks(sample_count: int, chunk_samples: int) -> tuple[PlannedChunk, ...]:
    """Split `sample_count` samples into contiguous chunks.

    Every sample appears in exactly one chunk, in order. The final chunk holds
    the remainder and is the only one that may be short.
    """
    if chunk_samples <= 0:
        raise ValueError("chunk_samples must be positive")
    if sample_count < 0:
        raise ValueError("sample_count must be non-negative")

    chunks: list[PlannedChunk] = []
    start = 0
    while start < sample_count:
        length = min(chunk_samples, sample_count - start)
        chunks.append(PlannedChunk(index=len(chunks), start_sample=start, length=length))
        start += length
    return tuple(chunks)


@dataclass(frozen=True)
class CapturePlan:
    """Everything needed to run one condition, decided before anything is sent."""

    condition_id: str
    commit_strategy: str
    prior_segment_count: int
    sample_rate: int
    sample_count: int
    pcm: bytes
    composed: ComposedTimeline
    placements: tuple[Placement, ...]
    chunks: tuple[PlannedChunk, ...]
    marker_text: str
    marker_start_sample: int
    chunk_samples: int
    chunk_interval_ms: int

    @property
    def marker_expected_ms(self) -> float:
        """Where the marker sits on the input sample clock.

        This is the *clip's* position, not the word's onset, which is exactly why
        no reported offset is ever measured against it. It is recorded so a reader
        can see the two numbers side by side.
        """
        return self.marker_start_sample * 1000 / self.sample_rate

    @property
    def expected_duration_ms(self) -> float:
        return self.sample_count * 1000 / self.sample_rate


def build_plan(
    *,
    condition: Condition,
    clips: Mapping[str, Clip],
    marker_text: str,
    commit_strategy: str = "vad",
    chunk_samples: int = CHUNK_SAMPLES,
) -> CapturePlan:
    """Compose a condition's audio and decide how it will be streamed."""
    composed = compose(condition.spec, clips)
    if marker_text not in composed.markers:
        raise ValueError(f"condition {condition.id!r} has no marker {marker_text!r}")

    return CapturePlan(
        condition_id=condition.id,
        commit_strategy=commit_strategy,
        prior_segment_count=condition.prior_segment_count,
        sample_rate=composed.sample_rate,
        sample_count=composed.sample_count,
        pcm=composed.pcm,
        composed=composed,
        placements=condition.spec.placements,
        chunks=plan_chunks(composed.sample_count, chunk_samples),
        marker_text=marker_text,
        marker_start_sample=composed.markers[marker_text],
        chunk_samples=chunk_samples,
        chunk_interval_ms=CHUNK_INTERVAL_MS,
    )

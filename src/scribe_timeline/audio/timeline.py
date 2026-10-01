"""Compose a controlled audio timeline from clips placed at exact sample indices.

The composer is the deepest module in the project: a single `compose` call
returns audio that is guaranteed to be exactly `total_samples` long, with every
clip at exactly its declared index and silence everywhere else. Callers never
validate placement themselves.

Two properties matter more than the rest:

* **Placement is exact.** A clip placed at sample *n* occupies bytes
  ``[n*2, n*2+len)``. Any drift here silently biases every offset measured
  against it while still producing a plausible number.
* **Marker positions are derived, not declared twice.** The composer reports
  where each marker actually landed. A separately maintained marker position is
  a second source of truth, and second sources of truth drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

SAMPLE_WIDTH_BYTES = 2  # mono PCM16
_SILENCE = b"\x00"

CLIP_SILENCE_MS = 120
"""Leading and trailing silence built into every generated speech clip.

Declared here rather than in the generator so the analysis layer can describe the
gap between a marker timestamp and its clip's insertion point without importing the
generator (and with it, the ElevenLabs SDK). One definition, so the figure quoted
in a report cannot drift from the figure used to build the audio.
"""


@dataclass(frozen=True)
class Clip:
    """A reusable chunk of mono PCM16 audio."""

    id: str
    pcm: bytes
    sample_rate: int

    def __post_init__(self) -> None:
        if len(self.pcm) % SAMPLE_WIDTH_BYTES != 0:
            raise ValueError(f"clip {self.id!r} is not whole PCM16 samples")
        if self.sample_rate <= 0:
            raise ValueError(f"clip {self.id!r} has a non-positive sample rate")

    @property
    def sample_count(self) -> int:
        return len(self.pcm) // SAMPLE_WIDTH_BYTES


@dataclass(frozen=True)
class Placement:
    """Where a clip sits in the composed timeline.

    `marker_text` is the distinctive word the recogniser is expected to return
    for this clip. It is matched exactly, so it must be unique across a timeline.
    """

    clip_id: str
    start_sample: int
    marker_text: str | None = None


@dataclass(frozen=True)
class TimelineSpec:
    sample_rate: int
    total_samples: int
    placements: tuple[Placement, ...]


@dataclass(frozen=True)
class ComposedTimeline:
    """Composed audio plus the marker positions that were actually realised."""

    pcm: bytes
    sample_rate: int
    markers: Mapping[str, int]

    @property
    def sample_count(self) -> int:
        return len(self.pcm) // SAMPLE_WIDTH_BYTES


def compose(spec: TimelineSpec, clips: Mapping[str, Clip]) -> ComposedTimeline:
    """Render a timeline spec to PCM16, enforcing every placement invariant.

    Raises `ValueError` if any clip is missing, mismatched in sample rate, out of
    bounds, overlapping another clip, or if two placements claim the same marker
    text (which would make exact-text marker matching ambiguous).
    """
    if spec.sample_rate <= 0:
        raise ValueError("timeline sample rate must be positive")
    if spec.total_samples < 0:
        raise ValueError("total_samples must be non-negative")

    buffer = bytearray(b"\x00" * (spec.total_samples * SAMPLE_WIDTH_BYTES))
    markers: dict[str, int] = {}
    occupied: list[tuple[int, int, str]] = []

    for placement in spec.placements:
        clip = clips.get(placement.clip_id)
        if clip is None:
            raise ValueError(f"no clip registered with id {placement.clip_id!r}")
        if clip.sample_rate != spec.sample_rate:
            raise ValueError(
                f"clip {clip.id!r} is {clip.sample_rate} Hz, "
                f"but the timeline is {spec.sample_rate} Hz"
            )
        if placement.start_sample < 0:
            raise ValueError(f"clip {clip.id!r} starts at negative sample {placement.start_sample}")

        end_sample = placement.start_sample + clip.sample_count
        if end_sample > spec.total_samples:
            raise ValueError(
                f"clip {clip.id!r} ends at sample {end_sample}, "
                f"past the timeline end of {spec.total_samples}"
            )

        _reject_overlap(occupied, placement.start_sample, end_sample, clip.id)
        occupied.append((placement.start_sample, end_sample, clip.id))

        offset = placement.start_sample * SAMPLE_WIDTH_BYTES
        buffer[offset : offset + len(clip.pcm)] = clip.pcm

        if placement.marker_text is not None:
            if clip.sample_count == 0:
                raise ValueError(
                    f"clip {clip.id!r} is marked {placement.marker_text!r} but contains no audio; "
                    "a marker must sit on real samples"
                )
            if placement.marker_text in markers:
                raise ValueError(
                    f"marker text {placement.marker_text!r} is used by more than one placement; "
                    "exact-text matching would be ambiguous"
                )
            markers[placement.marker_text] = placement.start_sample

    return ComposedTimeline(pcm=bytes(buffer), sample_rate=spec.sample_rate, markers=markers)


def _reject_overlap(
    occupied: list[tuple[int, int, str]], start: int, end: int, clip_id: str
) -> None:
    for other_start, other_end, other_id in occupied:
        if start < other_end and other_start < end:
            raise ValueError(
                f"clip {clip_id!r} at samples [{start}, {end}) overlaps "
                f"clip {other_id!r} at samples [{other_start}, {other_end})"
            )

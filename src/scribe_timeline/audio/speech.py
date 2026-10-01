"""Checking that a fixture clip is actually audible speech-shaped audio.

The rest of the fixture machinery is verified with deterministic ramps, which is
the right way to prove sample accounting. But a ramp is not speech, and a ramp
streamed to a recogniser returns an empty transcript -- which would produce a
plausible-looking run record containing no measurement at all.

So every clip is validated before it is sent: it must be whole PCM16, long enough
to hold a word, and audible rather than silent. Failing here costs nothing; failing
after a paid capture costs a run and a confused reading of the result.
"""

from __future__ import annotations

import struct

SAMPLE_WIDTH_BYTES = 2
FULL_SCALE = 32_767

MIN_CLIP_MS = 100
"""Shorter than a single short word, so it cannot be the marker."""

MIN_RMS = 200.0
"""Below this a clip is treated as effectively silent.

Deliberately far under speech level: this catches a muted or mis-encoded clip
without rejecting quiet but genuine speech.
"""


class SpeechClipError(ValueError):
    """A clip cannot be used as a speech fixture."""


def rms(pcm: bytes) -> float:
    """Root-mean-square amplitude of mono PCM16, in full-scale units."""
    if not pcm:
        return 0.0
    total = 0
    count = len(pcm) // SAMPLE_WIDTH_BYTES
    for (value,) in struct.iter_unpack("<h", pcm[: count * SAMPLE_WIDTH_BYTES]):
        total += value * value
    return float((total / count) ** 0.5)


def validate_speech_clip(pcm: bytes, *, label: str, sample_rate: int = 16_000) -> float:
    """Return the clip's RMS level, or raise `SpeechClipError` explaining why not."""
    if not pcm:
        raise SpeechClipError(f"clip {label!r} is empty")
    if len(pcm) % SAMPLE_WIDTH_BYTES != 0:
        raise SpeechClipError(f"clip {label!r} is not whole PCM16 samples")

    duration_ms = (len(pcm) // SAMPLE_WIDTH_BYTES) * 1000 / sample_rate
    if duration_ms < MIN_CLIP_MS:
        raise SpeechClipError(
            f"clip {label!r} is too short to hold a word: {duration_ms:.0f}ms "
            f"(minimum {MIN_CLIP_MS}ms)"
        )

    level = rms(pcm)
    if level == 0.0:
        raise SpeechClipError(
            f"clip {label!r} is silent, so it would return no words and no measurement"
        )
    if level < MIN_RMS:
        raise SpeechClipError(
            f"clip {label!r} is too quiet to transcribe: RMS {level:.0f} "
            f"(minimum {MIN_RMS:.0f})"
        )
    return level


def assert_speakable(pcm: bytes, *, label: str, sample_rate: int = 16_000) -> float:
    """Validate a clip, naming it in any failure."""
    return validate_speech_clip(pcm, label=label, sample_rate=sample_rate)

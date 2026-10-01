"""Speech fixtures must be real audio, and the marker word must be in them.

Everything upstream of this is ramps and silence, which proves sample accounting
but says nothing about whether a recogniser can hear anything. Before spending
money on capture, the fixture has to be checked on its own terms: does the
marker word actually appear, and is the clip audible at all.

Silence and near-silence are rejected explicitly, because a clip of digital
silence would produce a plausible run record containing no measurement.
"""

from __future__ import annotations

import struct

import pytest

from scribe_timeline.audio.speech import (
    SpeechClipError,
    assert_speakable,
    rms,
    validate_speech_clip,
)

SAMPLE_RATE = 16_000


def _tone(n_samples: int, amplitude: int, frequency: float = 220.0) -> bytes:
    """A deterministic sine, standing in for speech in these unit tests."""
    import math

    frames = b"".join(
        struct.pack(
            "<h",
            int(amplitude * math.sin(2 * math.pi * frequency * i / SAMPLE_RATE)),
        )
        for i in range(n_samples)
    )
    return frames


def test_rms_of_a_tone_is_positive() -> None:
    assert rms(_tone(SAMPLE_RATE, 8000)) > 0.0


def test_rms_of_silence_is_zero() -> None:
    assert rms(b"\x00" * 4000) == 0.0


def test_a_speakable_clip_passes_validation() -> None:
    validate_speech_clip(_tone(SAMPLE_RATE, 8000), label="marker")


def test_digital_silence_is_rejected() -> None:
    """A silent clip yields a run record with no measurement in it."""
    with pytest.raises(SpeechClipError, match="silent"):
        validate_speech_clip(b"\x00" * (SAMPLE_RATE // 2), label="marker")


def test_a_near_silent_clip_is_rejected() -> None:
    with pytest.raises(SpeechClipError, match="too quiet"):
        validate_speech_clip(_tone(SAMPLE_RATE // 2, 3), label="marker")


def test_an_empty_clip_is_rejected() -> None:
    with pytest.raises(SpeechClipError):
        validate_speech_clip(b"", label="marker")


def test_odd_length_pcm_is_rejected() -> None:
    with pytest.raises(SpeechClipError, match="whole PCM16"):
        validate_speech_clip(b"\x01\x02\x03", label="marker")


def test_a_very_short_clip_is_rejected() -> None:
    """Too short to hold a word, so it cannot be the marker."""
    with pytest.raises(SpeechClipError, match="too short"):
        validate_speech_clip(_tone(800, 8000), label="marker")


def test_the_error_names_the_offending_clip() -> None:
    with pytest.raises(SpeechClipError, match="earlier"):
        validate_speech_clip(b"\x00" * 100, label="earlier")


def test_assert_speakable_returns_the_measured_level() -> None:
    level = assert_speakable(_tone(SAMPLE_RATE, 8000), label="marker")

    assert level > 0.0

"""Shared helpers for building deterministic PCM fixtures in tests."""

from __future__ import annotations

import struct

SAMPLE_RATE = 16_000
SAMPLE_WIDTH_BYTES = 2  # mono PCM16


def ramp_pcm(n_samples: int) -> bytes:
    """Deterministic non-silent PCM16 whose bytes vary with sample index.

    A ramp rather than a constant so that a misplaced or truncated clip is
    detectable: every sample has a different value, so an off-by-one in the
    start index changes the bytes that land there.
    """
    return b"".join(struct.pack("<h", (i * 7) % 30_000 - 15_000) for i in range(n_samples))


def sample_at(pcm: bytes, index: int) -> int:
    """Read one signed 16-bit sample out of a mono PCM16 buffer."""
    (value,) = struct.unpack_from("<h", pcm, index * SAMPLE_WIDTH_BYTES)
    return int(value)


def slice_at(pcm: bytes, start_sample: int, n_samples: int) -> bytes:
    """Read `n_samples` worth of bytes starting at a sample index."""
    return pcm[start_sample * SAMPLE_WIDTH_BYTES : (start_sample + n_samples) * SAMPLE_WIDTH_BYTES]

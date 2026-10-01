"""The composer must place a clip at exactly its declared sample index.

This is the property the whole experiment rests on. If a clip lands one sample
early, every offset measured against it is wrong in a way that still looks like
a plausible number.
"""

from __future__ import annotations

import pytest

from pcm_fixtures import SAMPLE_RATE, ramp_pcm, slice_at
from scribe_timeline.audio.timeline import Clip, Placement, TimelineSpec, compose

MARKER = "Zorblax"


def test_clip_lands_at_exact_sample_index() -> None:
    clip = Clip(id="marker", pcm=ramp_pcm(1600), sample_rate=SAMPLE_RATE)
    spec = TimelineSpec(
        sample_rate=SAMPLE_RATE,
        total_samples=SAMPLE_RATE,
        placements=(Placement(clip_id="marker", start_sample=8000, marker_text=MARKER),),
    )

    composed = compose(spec, {"marker": clip})

    assert slice_at(composed.pcm, 8000, 1600) == clip.pcm


def test_composed_length_is_exactly_total_samples() -> None:
    spec = TimelineSpec(
        sample_rate=SAMPLE_RATE,
        total_samples=8000,
        placements=(Placement(clip_id="marker", start_sample=4000, marker_text=MARKER),),
    )
    clip = Clip(id="marker", pcm=ramp_pcm(1600), sample_rate=SAMPLE_RATE)

    composed = compose(spec, {"marker": clip})

    assert len(composed.pcm) == 8000 * 2
    assert composed.sample_count == 8000


def test_unoccupied_samples_are_silence() -> None:
    clip = Clip(id="marker", pcm=ramp_pcm(1600), sample_rate=SAMPLE_RATE)
    spec = TimelineSpec(
        sample_rate=SAMPLE_RATE,
        total_samples=8000,
        placements=(Placement(clip_id="marker", start_sample=4000, marker_text=MARKER),),
    )

    composed = compose(spec, {"marker": clip})

    assert composed.pcm[: 4000 * 2] == b"\x00" * (4000 * 2)
    assert composed.pcm[5600 * 2 :] == b"\x00" * (2400 * 2)


def test_compose_is_deterministic() -> None:
    clip = Clip(id="marker", pcm=ramp_pcm(1600), sample_rate=SAMPLE_RATE)
    spec = TimelineSpec(
        sample_rate=SAMPLE_RATE,
        total_samples=8000,
        placements=(Placement(clip_id="marker", start_sample=4000, marker_text=MARKER),),
    )

    assert compose(spec, {"marker": clip}).pcm == compose(spec, {"marker": clip}).pcm


def test_marker_positions_are_reported_from_the_composition() -> None:
    """Marker positions are derived, not maintained separately.

    A second source of truth for marker position is exactly how a declared
    position and an actual position drift apart.
    """
    clip = Clip(id="marker", pcm=ramp_pcm(1600), sample_rate=SAMPLE_RATE)
    spec = TimelineSpec(
        sample_rate=SAMPLE_RATE,
        total_samples=8000,
        placements=(Placement(clip_id="marker", start_sample=4000, marker_text=MARKER),),
    )

    composed = compose(spec, {"marker": clip})

    assert composed.markers == {MARKER: 4000}


@pytest.mark.parametrize(
    ("total_samples", "start_sample", "clip_samples"),
    [
        (8000, 7999, 1600),  # clip runs past the end
        (8000, -1, 1600),  # negative start
    ],
)
def test_placement_must_fit_inside_the_timeline(
    total_samples: int, start_sample: int, clip_samples: int
) -> None:
    clip = Clip(id="marker", pcm=ramp_pcm(clip_samples), sample_rate=SAMPLE_RATE)
    spec = TimelineSpec(
        sample_rate=SAMPLE_RATE,
        total_samples=total_samples,
        placements=(Placement(clip_id="marker", start_sample=start_sample, marker_text=MARKER),),
    )

    with pytest.raises(ValueError):
        compose(spec, {"marker": clip})


def test_a_zero_length_clip_cannot_carry_a_marker() -> None:
    """A marker on empty audio would claim a word where no samples exist."""
    clip = Clip(id="marker", pcm=b"", sample_rate=SAMPLE_RATE)
    spec = TimelineSpec(
        sample_rate=SAMPLE_RATE,
        total_samples=8000,
        placements=(Placement(clip_id="marker", start_sample=4000, marker_text=MARKER),),
    )

    with pytest.raises(ValueError, match="contains no audio"):
        compose(spec, {"marker": clip})


def test_overlapping_placements_are_rejected() -> None:
    clips = {
        "a": Clip(id="a", pcm=ramp_pcm(2000), sample_rate=SAMPLE_RATE),
        "b": Clip(id="b", pcm=ramp_pcm(2000), sample_rate=SAMPLE_RATE),
    }
    spec = TimelineSpec(
        sample_rate=SAMPLE_RATE,
        total_samples=8000,
        placements=(
            Placement(clip_id="a", start_sample=1000),
            Placement(clip_id="b", start_sample=2000),
        ),
    )

    with pytest.raises(ValueError, match="overlaps"):
        compose(spec, clips)


def test_two_placements_cannot_share_a_marker_word() -> None:
    """Exact-text matching cannot tell two placements of one word apart."""
    clips = {
        "a": Clip(id="a", pcm=ramp_pcm(1000), sample_rate=SAMPLE_RATE),
        "b": Clip(id="b", pcm=ramp_pcm(1000), sample_rate=SAMPLE_RATE),
    }
    spec = TimelineSpec(
        sample_rate=SAMPLE_RATE,
        total_samples=8000,
        placements=(
            Placement(clip_id="a", start_sample=0, marker_text=MARKER),
            Placement(clip_id="b", start_sample=4000, marker_text=MARKER),
        ),
    )

    with pytest.raises(ValueError, match="more than one placement"):
        compose(spec, clips)

"""The final marker must be byte-identical across every condition.

The measurement compares the same word's timestamp across conditions that differ
only in how many speech segments precede it. That comparison is only sound if
the final marker's bytes and its sample position are literally identical in
every condition -- otherwise a difference in the returned timestamps could be
explained by a difference in what was played.

A one-sample drift in the final marker would bias every delta the report
publishes, and would still look like a plausible number.
"""

from __future__ import annotations

import pytest

from pcm_fixtures import SAMPLE_RATE, ramp_pcm, sample_at, slice_at
from scribe_timeline.audio.family import MARKER_TEXT, Condition, build_vad_family
from scribe_timeline.audio.timeline import Clip, compose


@pytest.fixture
def clips() -> dict[str, Clip]:
    return {
        "earlier": Clip(id="earlier", pcm=ramp_pcm(4000), sample_rate=SAMPLE_RATE),
        "marker": Clip(id="marker", pcm=ramp_pcm(4000), sample_rate=SAMPLE_RATE),
    }


@pytest.fixture
def family(clips: dict[str, Clip]) -> tuple[Condition, ...]:
    # 6s grid: earlier segments at 0s and 6s, final marker pinned at 12s.
    return build_vad_family(
        clips=clips,
        sample_rate=SAMPLE_RATE,
        total_samples=SAMPLE_RATE * 14,
        earlier_starts=(0, SAMPLE_RATE * 6),
        final_marker_start=SAMPLE_RATE * 12,
        marker_text=MARKER_TEXT,
    )


def test_family_has_one_condition_per_prefix_of_earlier_segments(
    family: tuple[Condition, ...],
) -> None:
    assert [c.prior_segment_count for c in family] == [0, 1, 2]


def test_every_condition_places_the_final_marker_at_the_same_sample(
    family: tuple[Condition, ...], clips: dict[str, Clip]
) -> None:
    starts = {compose(c.spec, clips).markers[MARKER_TEXT] for c in family}

    assert len(starts) == 1


def test_final_marker_bytes_are_identical_across_conditions(
    family: tuple[Condition, ...], clips: dict[str, Clip]
) -> None:
    per_condition = [
        slice_at(
            compose(condition.spec, clips).pcm,
            condition.spec.placements[-1].start_sample,
            clips["marker"].sample_count,
        )
        for condition in family
    ]

    assert len(set(per_condition)) == 1
    assert per_condition[0] == clips["marker"].pcm


def test_final_marker_start_is_the_declared_sample_in_every_condition(
    family: tuple[Condition, ...], clips: dict[str, Clip]
) -> None:
    for condition in family:
        composed = compose(condition.spec, clips)
        assert composed.markers[MARKER_TEXT] == SAMPLE_RATE * 12
        assert sample_at(composed.pcm, SAMPLE_RATE * 12) == sample_at(
            clips["marker"].pcm, 0
        )


def test_conditions_differ_before_the_final_marker(
    family: tuple[Condition, ...], clips: dict[str, Clip]
) -> None:
    """A guard against a degenerate family where every condition is identical.

    The comparison window is derived from each condition's own marker placement
    rather than recomputed from a constant, so changing the fixture moves the
    window with it instead of silently comparing the wrong span.
    """
    prefixes = set()
    for condition in family:
        marker = compose(condition.spec, clips).markers[MARKER_TEXT]
        prefixes.add(compose(condition.spec, clips).pcm[: marker * 2])

    assert len(prefixes) == len(family)


def test_only_the_final_marker_is_a_measurement_target(
    family: tuple[Condition, ...], clips: dict[str, Clip]
) -> None:
    """Earlier segments are unmarked: nothing needs to match them.

    Marking them would put the same word at two placements, and exact-text
    matching cannot tell them apart.
    """
    for condition in family:
        composed = compose(condition.spec, clips)
        assert set(composed.markers) == {MARKER_TEXT}

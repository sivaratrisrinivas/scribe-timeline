"""The capture plan is pure arithmetic over composed audio.

Pacing is what makes a controlled streaming experiment controlled: audio must
reach the server at the rate it was recorded, or the server's own VAD sees a
timeline that is not the one on disk, and every timestamp it returns describes
audio that was never played. So the plan is computed and tested offline, with no
network and no key.
"""

from __future__ import annotations

import itertools

import pytest

from pcm_fixtures import SAMPLE_RATE
from scribe_timeline.audio.family import MARKER_TEXT, Condition, build_vad_family
from scribe_timeline.audio.timeline import Clip, compose
from scribe_timeline.capture.plan import (
    CHUNK_SAMPLES,
    CapturePlan,
    boundaries_due,
    build_plan,
    plan_chunks,
)

CHUNK_MS = 100


def _clips() -> dict[str, Clip]:
    return {
        "earlier": Clip(id="earlier", pcm=b"\x01\x00" * 4000, sample_rate=SAMPLE_RATE),
        "marker": Clip(id="marker", pcm=b"\x02\x00" * 4000, sample_rate=SAMPLE_RATE),
    }


@pytest.fixture
def family() -> tuple[Condition, ...]:
    return build_vad_family(
        clips=_clips(),
        sample_rate=SAMPLE_RATE,
        total_samples=SAMPLE_RATE * 14,
        earlier_starts=(0, SAMPLE_RATE * 6),
        final_marker_start=SAMPLE_RATE * 12,
    )


def test_chunking_covers_every_sample_exactly_once(family: tuple[Condition, ...]) -> None:
    """A dropped or double-sent chunk would silently shift the timeline."""
    composed = compose(family[0].spec, _clips())

    chunks = plan_chunks(composed.sample_count, CHUNK_SAMPLES)

    assert sum(chunk.length for chunk in chunks) == composed.sample_count
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_chunks_are_contiguous_with_no_gaps_or_overlap(family: tuple[Condition, ...]) -> None:
    composed = compose(family[0].spec, _clips())

    chunks = plan_chunks(composed.sample_count, CHUNK_SAMPLES)

    for previous, current in itertools.pairwise(chunks):
        assert current.start_sample == previous.start_sample + previous.length


def test_only_the_last_chunk_is_a_partial_one(family: tuple[Condition, ...]) -> None:
    total = SAMPLE_RATE + 250  # deliberately not a multiple of the chunk size

    chunks = plan_chunks(total, CHUNK_SAMPLES)

    assert all(chunk.length == CHUNK_SAMPLES for chunk in chunks[:-1])
    assert chunks[-1].length == total - CHUNK_SAMPLES * (len(chunks) - 1)
    assert 0 < chunks[-1].length <= CHUNK_SAMPLES


def test_an_empty_timeline_yields_no_chunks() -> None:
    assert plan_chunks(0, CHUNK_SAMPLES) == ()


def test_plan_states_the_marker_position_in_samples(family: tuple[Condition, ...]) -> None:
    clips = _clips()

    plan = build_plan(
        condition=family[1], clips=clips, marker_text=MARKER_TEXT, chunk_samples=CHUNK_SAMPLES
    )

    assert plan.marker_start_sample == SAMPLE_RATE * 12
    assert plan.marker_expected_ms == pytest.approx(12_000.0)
    assert plan.sample_rate == SAMPLE_RATE
    assert plan.sample_count == SAMPLE_RATE * 14


def test_plan_carries_the_composed_audio_verbatim(family: tuple[Condition, ...]) -> None:
    clips = _clips()
    expected = compose(family[1].spec, clips).pcm

    plan = build_plan(
        condition=family[1], clips=clips, marker_text=MARKER_TEXT, chunk_samples=CHUNK_SAMPLES
    )

    assert plan.pcm == expected


def test_plan_records_its_own_timing_constants(family: tuple[Condition, ...]) -> None:
    """The pacing interval is part of the record, not an implicit default."""
    plan: CapturePlan = build_plan(
        condition=family[0], clips=_clips(), marker_text=MARKER_TEXT, chunk_samples=CHUNK_SAMPLES
    )

    assert plan.chunk_samples == CHUNK_SAMPLES
    assert plan.chunk_interval_ms == pytest.approx(CHUNK_MS)
    assert plan.expected_duration_ms == pytest.approx(14_000.0)


# --- Manual commit boundaries -------------------------------------------------
#
# The control condition needs commits the *server* would not make. Under VAD the
# server decides where to cut; under manual it never cuts at all, so the only way
# to give the control the same number of preceding commits as a VAD condition is
# to ask for them at chosen sample positions.
#
# Getting these positions wrong would not fail -- it would quietly give the
# control a different number of commits than the condition it is compared
# against, which is the one thing the control exists to hold fixed.


def test_a_manual_boundary_sits_at_the_end_of_each_clip_before_the_marker(
    family: tuple[Condition, ...],
) -> None:
    clips = _clips()
    earlier_length = clips["earlier"].sample_count

    plan = build_plan(
        condition=family[2], clips=clips, marker_text=MARKER_TEXT, chunk_samples=CHUNK_SAMPLES
    )

    # family[2] places "earlier" at 0s and 6s; each boundary is that clip's end.
    assert plan.manual_commit_boundaries == (
        earlier_length,
        SAMPLE_RATE * 6 + earlier_length,
    )


def test_the_marker_clip_is_never_a_manual_boundary(family: tuple[Condition, ...]) -> None:
    """The runner always flushes after the last chunk, so a boundary there is redundant."""
    clips = _clips()
    marker_start = SAMPLE_RATE * 12

    plan = build_plan(
        condition=family[2], clips=clips, marker_text=MARKER_TEXT, chunk_samples=CHUNK_SAMPLES
    )

    assert all(boundary < marker_start for boundary in plan.manual_commit_boundaries)


def test_a_condition_with_nothing_before_the_marker_has_no_boundaries(
    family: tuple[Condition, ...],
) -> None:
    plan = build_plan(
        condition=family[0], clips=_clips(), marker_text=MARKER_TEXT, chunk_samples=CHUNK_SAMPLES
    )

    assert plan.manual_commit_boundaries == ()


def test_boundaries_are_ordered_and_inside_the_audio(family: tuple[Condition, ...]) -> None:
    plan = build_plan(
        condition=family[2], clips=_clips(), marker_text=MARKER_TEXT, chunk_samples=CHUNK_SAMPLES
    )

    boundaries = plan.manual_commit_boundaries
    assert list(boundaries) == sorted(boundaries)
    assert all(0 < boundary < plan.sample_count for boundary in boundaries)


def test_a_boundary_landing_exactly_on_a_chunk_end_counts(family: tuple[Condition, ...]) -> None:
    """Off-by-one here drops or duplicates a commit, changing the control's shape."""
    boundaries = (CHUNK_SAMPLES * 3, CHUNK_SAMPLES * 5)

    due = boundaries_due(CHUNK_SAMPLES * 3, boundaries, already_sent=0)

    assert due == 1


def test_boundaries_already_committed_are_not_sent_twice(
    family: tuple[Condition, ...],
) -> None:
    boundaries = (CHUNK_SAMPLES * 3, CHUNK_SAMPLES * 5)

    due = boundaries_due(CHUNK_SAMPLES * 9, boundaries, already_sent=1)

    assert due == 1  # the second boundary only; the first was already sent


def test_boundaries_due_never_goes_backwards(family: tuple[Condition, ...]) -> None:
    """Chunks are sent in order, but a defensive clamp keeps the count honest."""
    boundaries = (CHUNK_SAMPLES * 3, CHUNK_SAMPLES * 5)

    due = boundaries_due(CHUNK_SAMPLES, boundaries, already_sent=2)

    assert due == 0

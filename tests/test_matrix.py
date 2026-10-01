"""Which runs the matrix performs, and what it refuses to do.

The comparison is only as trustworthy as the set of runs handed to it, and two
things about that set are easy to get quietly wrong: which conditions are in it,
and which commit strategy each one used. Both are asserted here rather than left
to the printed output, because a manual run mislabelled as VAD would collapse the
control without failing anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcm_fixtures import SAMPLE_RATE
from scribe_timeline.audio.timeline import Clip
from scribe_timeline.capture.matrix import (
    MIN_REPEATS,
    REPEATS,
    TOTAL_SECONDS,
    MatrixCondition,
    build_matrix,
    load_records,
    main,
    plan_matrix,
    planned_commit_boundaries,
)
from scribe_timeline.capture.runner import VAD_OPTIONS


def _clips() -> dict[str, Clip]:
    return {
        "earlier": Clip(id="earlier", pcm=b"\x01\x00" * 4000, sample_rate=SAMPLE_RATE),
        "marker": Clip(id="marker", pcm=b"\x02\x00" * 4000, sample_rate=SAMPLE_RATE),
    }


VAD_SILENCE_THRESHOLD_SECS = VAD_OPTIONS["vad_silence_threshold_secs"]


@pytest.fixture
def matrix() -> tuple[MatrixCondition, ...]:
    return build_matrix(_clips())


def test_the_matrix_covers_three_vad_conditions_and_one_manual_control(
    matrix: tuple[MatrixCondition, ...],
) -> None:
    assert [entry.id for entry in matrix] == ["vad_0", "vad_1", "vad_2", "manual_2"]


def test_each_condition_names_the_strategy_that_triggers_its_commits(
    matrix: tuple[MatrixCondition, ...],
) -> None:
    strategies = {entry.id: entry.commit_strategy for entry in matrix}

    assert strategies == {
        "vad_0": "vad",
        "vad_1": "vad",
        "vad_2": "vad",
        "manual_2": "manual",
    }


def test_the_control_plays_the_same_audio_as_the_condition_it_is_compared_against(
    matrix: tuple[MatrixCondition, ...],
) -> None:
    """A control built from different audio could differ for unrelated reasons."""
    by_id = {entry.id: entry.condition for entry in matrix}

    assert by_id["manual_2"].spec == by_id["vad_2"].spec
    assert by_id["manual_2"].prior_segment_count == by_id["vad_2"].prior_segment_count


def test_the_control_has_a_distinct_id_from_its_vad_counterpart(
    matrix: tuple[MatrixCondition, ...],
) -> None:
    """A run record is read by id and strategy together.

    Reusing `vad_2` for a manual run would make it read as a VAD run in a saved
    report, which is the one error the control cannot survive.
    """
    by_id = {entry.id: entry.condition for entry in matrix}

    assert by_id["manual_2"].id != by_id["vad_2"].id


def test_every_condition_is_repeated(matrix: tuple[MatrixCondition, ...]) -> None:
    planned = plan_matrix(matrix)

    counts: dict[str, int] = {}
    for run in planned:
        counts[run.condition.id] = counts.get(run.condition.id, 0) + 1

    assert counts == {entry.id: REPEATS for entry in matrix}


def test_the_default_is_three_repeats() -> None:
    assert REPEATS == 3


def test_repeats_are_interleaved_so_no_condition_is_measured_all_at_one_moment(
    matrix: tuple[MatrixCondition, ...],
) -> None:
    """Anything drifting over the session would otherwise land on one condition.

    Twelve runs take minutes. If every repeat of `vad_2` ran last, a slow change
    in server behaviour would look exactly like a condition effect.
    """
    planned = plan_matrix(matrix, repeats=3)

    assert [run.condition.id for run in planned] == [
        "vad_0", "vad_1", "vad_2", "manual_2",
    ] * 3
    assert [run.repeat_index for run in planned[:4]] == [0, 0, 0, 0]


def test_a_single_repeat_still_plans_every_condition(
    matrix: tuple[MatrixCondition, ...],
) -> None:
    planned = plan_matrix(matrix, repeats=1)

    assert [run.condition.id for run in planned] == [
        "vad_0", "vad_1", "vad_2", "manual_2",
    ]
    assert {run.repeat_index for run in planned} == {0}


def test_a_missing_run_record_is_refused_rather_than_skipped(tmp_path: Path) -> None:
    """A silently skipped run would shrink a condition's repeats unnoticed."""
    path = tmp_path / "nope.json"

    with pytest.raises(FileNotFoundError):
        load_records([path])


# --- The geometry the matrix measures -----------------------------------------


def test_every_condition_places_the_marker_at_the_same_sample() -> None:
    """The marker's position is held identical across the family.

    If it moved between conditions, a delta could be explained by the audio rather
    than by the commits -- which is the one thing the comparison cannot correct for.
    """
    matrix = build_matrix(_clips())

    starts = {
        entry.id: entry.condition.spec.placements[-1].start_sample for entry in matrix
    }

    assert set(starts.values()) == {SAMPLE_RATE * 12}


def test_the_vad_conditions_differ_only_in_what_precedes_the_marker() -> None:
    matrix = build_matrix(_clips())

    vad = [e.condition.spec.placements for e in matrix if e.commit_strategy == "vad"]
    assert [len(p) for p in vad] == [1, 2, 3]
    for placements in vad:
        assert placements[-1].clip_id == "marker"
        assert placements[-1].start_sample == SAMPLE_RATE * 12


def test_the_manual_control_asks_for_a_commit_after_each_preceding_clip() -> None:
    """The control's whole claim is 'same commits, different chooser'."""
    clips = _clips()
    matrix = build_matrix(clips)
    manual = next(e for e in matrix if e.commit_strategy == "manual")
    earlier_length = clips["earlier"].sample_count

    boundaries = planned_commit_boundaries(manual, clips)

    assert boundaries == (earlier_length, SAMPLE_RATE * 6 + earlier_length)


def test_the_vad_conditions_ask_for_no_commits_of_their_own() -> None:
    """Second-guessing the server's segmentation would change what is measured."""
    clips = _clips()
    matrix = build_matrix(clips)

    for entry in matrix:
        if entry.commit_strategy == "vad":
            assert planned_commit_boundaries(entry, clips) == ()


def test_the_timeline_leaves_vad_enough_trailing_silence_to_commit() -> None:
    """Under VAD the server only commits after `vad_silence_threshold_secs` of silence.

    The 18s timeline with a marker at 12s leaves ~4.8s after the marker's clip ends.
    An earlier attempt with 0.8s never committed at all, so this is checked rather
    than assumed.
    """
    clips = _clips()
    marker_end = SAMPLE_RATE * 12 + clips["marker"].sample_count
    trailing_ms = (SAMPLE_RATE * TOTAL_SECONDS - marker_end) * 1000 / SAMPLE_RATE

    assert trailing_ms > VAD_SILENCE_THRESHOLD_SECS * 1000


# --- Refusing a matrix that cannot support a measurement ----------------------


def test_too_few_repeats_is_refused(matrix: tuple[MatrixCondition, ...]) -> None:
    """One repeat makes `spread_ms` zero by arithmetic, not by observation."""
    assert main(["--repeats", "1"]) == 2


def test_zero_repeats_is_refused(matrix: tuple[MatrixCondition, ...]) -> None:
    assert main(["--repeats", "0"]) == 2


def test_enough_repeats_is_accepted_for_planning(
    matrix: tuple[MatrixCondition, ...],
) -> None:
    """The floor is on the CLI, not on the planner, so the check is at the boundary."""
    assert MIN_REPEATS == 2
    assert len(plan_matrix(matrix, repeats=MIN_REPEATS)) == len(matrix) * MIN_REPEATS
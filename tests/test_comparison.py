"""Turning a set of run records into the measurement.

This is the module the whole project exists to get right, so the tests are
mostly about what the arithmetic must *not* do.

The reported bug is a per-commit timestamp drift, and the tempting way to
measure it is to compare a returned timestamp against the point where audio was
inserted. That is circular: insertion point is not word onset, so the comparison
assumes the answer it is looking for. This project only ever differences the
same word's timestamp *across conditions*, and several tests below exist purely
to make that impossible to undo by accident.

The other half is honesty about uncertainty. One number per condition is not a
measurement, so every condition carries its repeats and the spread across them,
and a claim is only called reproduced when the measured interval cannot
distinguish it from the claim.

No network and no key: every number here comes from a run record on disk.
"""

from __future__ import annotations

from typing import Any

import pytest

from scribe_timeline.analysis.compare import (
    REPORTER_CLAIMS,
    TIMESTAMP_QUANTUM_MS,
    ComparisonReport,
    ConditionSummary,
    compare_runs,
    observed_commit_count,
)
from scribe_timeline.analysis.matching import MarkerNotFound
from scribe_timeline.audio.family import MARKER_TEXT
from scribe_timeline.capture.completion import TIMESTAMPED_EVENT
from scribe_timeline.records import (
    CommitStrategy,
    EchoedSessionConfig,
    Manifest,
    RawEvent,
    RunRecord,
    Segment,
    WordTiming,
)

ANCHOR_SAMPLE = 192_000
SAMPLE_RATE = 16_000
TIMELINE_SAMPLES = 288_000


def _record(
    condition_id: str,
    *,
    repeat_index: int,
    marker_start_ms: float,
    commit_strategy: CommitStrategy = "vad",
    prior_segment_count: int = 0,
    marker_sample: int = ANCHOR_SAMPLE,
    commits: int | None = None,
    words: tuple[WordTiming, ...] | None = None,
) -> RunRecord:
    segments = (
        *(
            Segment(
                clip_id="earlier",
                start_sample=index * 96_000,
                sample_count=16_472,
            )
            for index in range(prior_segment_count)
        ),
        Segment(
            clip_id="marker",
            start_sample=marker_sample,
            sample_count=18_701,
            marker_text=MARKER_TEXT,
        ),
    )
    return RunRecord(
        schema_version=1,
        run_id=f"run__{condition_id}__rep{repeat_index}",
        condition_id=condition_id,
        commit_strategy=commit_strategy,
        repeat_index=repeat_index,
        manifest=Manifest(
            condition_id=condition_id,
            sample_rate=SAMPLE_RATE,
            sample_count=TIMELINE_SAMPLES,
            markers={MARKER_TEXT: marker_sample},
            segments=segments,
        ),
        echoed_config=EchoedSessionConfig(
            model_id="scribe_v2_realtime",
            sample_rate=SAMPLE_RATE,
            include_timestamps=True,
        ),
        words=words
        if words is not None
        else (
            WordTiming(
                text=MARKER_TEXT + ".",
                start_ms=marker_start_ms,
                end_ms=marker_start_ms + 580.0,
                logprob=-1.3,
            ),
        ),
        events=_commit_events(commits if commits is not None else prior_segment_count + 1),
    )


def _commit_events(count: int) -> tuple[RawEvent, ...]:
    """`count` timestamped commits, so commit counting can be asserted."""
    return tuple(
        RawEvent(
            type=TIMESTAMPED_EVENT,
            received_at_ms=float(index * 100),
            payload={"words": []},
        )
        for index in range(count)
    )


def _condition_records(
    condition_id: str, marker_times: list[float], **kwargs: Any
) -> list[RunRecord]:
    return [
        _record(condition_id, repeat_index=index, marker_start_ms=time, **kwargs)
        for index, time in enumerate(marker_times)
    ]


def _matrix() -> list[RunRecord]:
    """Four conditions x three repeats, drifting by 100 ms per preceding commit."""
    return [
        *_condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0]),
        *_condition_records("vad_1", [12_300.0, 12_300.0, 12_300.0], prior_segment_count=1),
        *_condition_records("vad_2", [12_400.0, 12_400.0, 12_400.0], prior_segment_count=2),
        *_condition_records(
            "manual_2",
            [12_200.0, 12_200.0, 12_200.0],
            commit_strategy="manual",
            prior_segment_count=2,
        ),
    ]


def _summary(report: ComparisonReport, condition_id: str) -> ConditionSummary:
    return next(s for s in report.conditions if s.condition_id == condition_id)


# --- The anchor, and what a delta is measured against -------------------------


def test_the_anchor_is_the_condition_with_no_preceding_commits() -> None:
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert report.anchor_condition_id == "vad_0"
    assert _summary(report, "vad_0").is_anchor
    assert not _summary(report, "vad_1").is_anchor


def test_a_delta_is_the_change_in_one_word_across_conditions() -> None:
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert _summary(report, "vad_1").delta_vs_anchor_ms == pytest.approx(100.0)
    assert _summary(report, "vad_2").delta_vs_anchor_ms == pytest.approx(200.0)


def test_the_anchor_itself_has_no_delta() -> None:
    """A delta of zero against itself would read as 'no drift' rather than 'baseline'."""
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert _summary(report, "vad_0").delta_vs_anchor_ms is None


def test_a_delta_ignores_where_the_clip_was_inserted() -> None:
    """The circularity this project exists to avoid.

    Insertion point is not word onset. If any delta were computed against it,
    moving the marker's clip without changing what the server returned would
    change the reported drift. So: same returned timestamps, wildly different
    insertion points, and every delta must stay zero.
    """
    records = _condition_records("vad_0", [12_000.0], marker_sample=0)
    records += _condition_records("vad_1", [12_000.0], marker_sample=287_000, prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    assert _summary(report, "vad_1").delta_vs_anchor_ms == pytest.approx(0.0)


def test_a_delta_is_not_the_gap_between_a_timestamp_and_the_insertion_point() -> None:
    """The anchor sits 200 ms after its insertion point; that is fixture padding."""
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    anchor = _summary(report, "vad_0")
    context = report.context
    assert context.insertion_point_gap_ms[anchor.condition_id] == pytest.approx(200.0)
    # ...and it is kept out of the delta arithmetic entirely.
    assert anchor.delta_vs_anchor_ms is None
    assert _summary(report, "vad_1").delta_vs_anchor_ms == pytest.approx(100.0)


# --- Spread, because one number is not a measurement --------------------------


def test_spread_is_reported_for_every_condition() -> None:
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_1", [12_300.0, 12_310.0, 12_320.0], prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    assert _summary(report, "vad_0").spread_ms == pytest.approx(0.0)
    assert _summary(report, "vad_1").spread_ms == pytest.approx(20.0)


def test_every_repeat_is_kept_not_just_the_middle_one() -> None:
    records = _condition_records("vad_0", [12_200.0])
    records += _condition_records("vad_1", [12_290.0, 12_300.0, 12_330.0], prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    assert _summary(report, "vad_1").marker_start_ms == (12_290.0, 12_300.0, 12_330.0)
    assert _summary(report, "vad_1").median_marker_ms == pytest.approx(12_300.0)


def test_a_single_repeat_reports_zero_spread_rather_than_failing() -> None:
    records = _condition_records("vad_0", [12_200.0])
    records += _condition_records("vad_1", [12_300.0], prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    assert _summary(report, "vad_1").repeat_count == 1
    assert _summary(report, "vad_1").spread_ms == pytest.approx(0.0)


def test_the_delta_interval_covers_every_repeat_against_the_anchor() -> None:
    """The interval is what decides whether a claim is distinguishable."""
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_1", [12_290.0, 12_300.0, 12_330.0], prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    interval = _summary(report, "vad_1").delta_interval_ms
    assert interval is not None
    assert interval[0] == pytest.approx(90.0)
    assert interval[1] == pytest.approx(130.0)


# --- Attributing every figure to its condition --------------------------------


def test_every_figure_names_the_condition_it_came_from() -> None:
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    for summary in report.conditions:
        assert summary.condition_id
        assert summary.commit_strategy
        assert summary.repeat_count >= 1


def test_the_manual_control_is_a_separate_condition_from_its_vad_counterpart() -> None:
    """Same audio and same commit count, different trigger.

    Collapsing these two would erase the entire control.
    """
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    manual = _summary(report, "manual_2")
    vad = _summary(report, "vad_2")
    assert manual.commit_strategy == "manual"
    assert vad.commit_strategy == "vad"
    assert manual.prior_segment_count == vad.prior_segment_count == 2


def test_the_manual_control_reports_whether_offsets_accumulated() -> None:
    """Manual commits that do not accumulate land on the anchor's timestamp."""
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert _summary(report, "manual_2").delta_vs_anchor_ms == pytest.approx(0.0)


# --- Comparing against the reporter, without assuming ------------------------


def test_the_reported_claim_is_compared_as_a_step_not_an_absolute_offset() -> None:
    """Their +9 / +109 / +209 are offsets against an insertion point.

    Ours is anchor-relative and never touches an insertion point, so the only
    comparable quantity is the step between conditions. Comparing our deltas to
    their absolutes would manufacture a ~91 ms disagreement out of nothing.
    """
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    comparison = next(c for c in report.claims if c.condition_id == "vad_1")
    assert comparison.claimed_step_ms == pytest.approx(100.0)
    assert comparison.measured_step_ms == pytest.approx(100.0)
    assert comparison.difference_ms == pytest.approx(0.0)
    assert comparison.reproduces


def test_the_reporter_zero_commit_floor_is_not_treated_as_a_comparable_figure() -> None:
    """+9 ms with no preceding commit is a different finding from per-commit drift.

    It is kept for provenance and never enters a delta or a comparison.
    """
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert report.claims_source == REPORTER_CLAIMS.source
    assert all(c.condition_id != "vad_0" for c in report.claims)
    assert report.reporter_claimed_offset_ms["vad_0"] == pytest.approx(9.0)


def test_a_claim_inside_the_measured_interval_is_reproduced() -> None:
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_1", [12_290.0, 12_300.0, 12_330.0], prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    comparison = next(c for c in report.claims if c.condition_id == "vad_1")
    assert comparison.reproduces


def test_a_claim_outside_the_measured_interval_is_not_reproduced() -> None:
    """Non-reproduction is a result and gets reported like one."""
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_1", [12_201.0, 12_201.0, 12_201.0], prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    comparison = next(c for c in report.claims if c.condition_id == "vad_1")
    assert not comparison.reproduces
    assert comparison.difference_ms == pytest.approx(-99.0)


def test_drift_is_only_called_detected_when_a_delta_clears_its_own_spread() -> None:
    """One repeat landing a millisecond late is noise, not a 1 ms drift."""
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_1", [12_201.0, 12_200.0, 12_199.0], prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    assert not report.drift_detected


def test_drift_is_detected_when_a_delta_excludes_zero() -> None:
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert report.drift_detected


# --- Refusing to guess -------------------------------------------------------


def test_a_run_without_the_anchor_is_refused() -> None:
    """No anchor means no baseline, and inventing one would invent the result."""
    with pytest.raises(ValueError, match="anchor"):
        compare_runs(
            _condition_records("vad_1", [12_300.0], prior_segment_count=1),
            marker_text=MARKER_TEXT,
        )


def test_two_vad_baselines_are_refused_rather_than_one_being_picked() -> None:
    """Two zero-preceding-commit VAD conditions means the baseline is ambiguous.

    Which one is chosen decides every delta in the report, so this is refused
    rather than resolved by iteration order.
    """
    records = _condition_records("vad_0", [12_200.0])
    records += _condition_records("vad_0_other_family", [12_250.0])

    with pytest.raises(ValueError, match="more than one anchor"):
        compare_runs(records, marker_text=MARKER_TEXT)


def test_a_manual_zero_commit_condition_is_not_mistaken_for_the_anchor() -> None:
    """A manual run with nothing preceding it is a second baseline, not the baseline.

    It gets its own delta against the VAD anchor, which is what says whether the
    commit strategy matters when nothing precedes the marker.
    """
    records = _condition_records("vad_0", [12_200.0])
    records += _condition_records("manual_0", [12_200.0], commit_strategy="manual")

    report = compare_runs(records, marker_text=MARKER_TEXT)

    assert report.anchor_condition_id == "vad_0"
    assert _summary(report, "manual_0").is_anchor is False
    assert _summary(report, "manual_0").delta_vs_anchor_ms == pytest.approx(0.0)


def test_a_record_missing_its_marker_is_refused_rather_than_scored_as_zero() -> None:
    """A missing marker is an error, not a zero offset."""
    broken = _record("vad_1", repeat_index=0, marker_start_ms=12_300.0, prior_segment_count=1)
    stripped = broken.model_copy(update={"words": ()})

    with pytest.raises(MarkerNotFound):
        compare_runs(
            [*_condition_records("vad_0", [12_200.0]), stripped],
            marker_text=MARKER_TEXT,
        )


def test_an_empty_run_set_is_refused() -> None:
    with pytest.raises(ValueError, match="no run records"):
        compare_runs([], marker_text=MARKER_TEXT)


# --- Two renderings of one set of numbers ------------------------------------


def test_the_text_rendering_states_every_measured_number() -> None:
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    text = report.render_text()

    assert "vad_1" in text
    assert "+100.0" in text
    assert "12,200" in text or "12200" in text


def test_the_text_rendering_says_so_when_nothing_reproduces() -> None:
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_1", [12_201.0, 12_201.0, 12_201.0], prior_segment_count=1)

    text = compare_runs(records, marker_text=MARKER_TEXT).render_text()

    assert "does not reproduce" in text.lower()


def test_the_text_rendering_says_so_when_the_drift_reproduces() -> None:
    text = compare_runs(_matrix(), marker_text=MARKER_TEXT).render_text()

    assert "reproduces" in text.lower()


def test_the_machine_readable_rendering_carries_the_same_figures() -> None:
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    payload = report.to_dict()

    vad_1 = next(c for c in payload["conditions"] if c["condition_id"] == "vad_1")
    assert vad_1["delta_vs_anchor_ms"] == pytest.approx(100.0)
    assert vad_1["repeat_count"] == 3
    assert payload["anchor_condition_id"] == "vad_0"
    assert payload["drift_detected"] is True


def test_every_number_in_the_text_rendering_also_appears_in_the_json() -> None:
    """Two renderings of one calculation must not be able to disagree."""
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    text = report.render_text()
    payload = report.to_dict()

    for condition in payload["conditions"]:
        assert condition["condition_id"] in text
        if condition["delta_vs_anchor_ms"] is not None:
            assert f"{condition['delta_vs_anchor_ms']:+.1f}" in text


def test_the_json_rendering_is_serialisable() -> None:
    import json

    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert json.loads(json.dumps(report.to_dict()))["marker_text"] == MARKER_TEXT


# --- The control has to conclude something ------------------------------------


def test_the_control_states_that_offsets_do_not_accumulate_under_manual_commits() -> None:
    """The control's entire job. A table row is not a conclusion."""
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert report.control is not None
    assert not report.control.manual_accumulates
    assert "do NOT accumulate" in report.render_text()


def test_the_control_compares_against_the_vad_condition_with_the_same_commit_count() -> None:
    """Compared against anything else it would confound strategy with commit count."""
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert report.control is not None
    assert report.control.compares_to_condition_id == "vad_2"
    assert report.control.manual_condition_id == "manual_2"


def test_a_manual_control_that_does_move_is_reported_as_accumulating() -> None:
    """The conclusion must be able to come out the other way."""
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_2", [12_400.0, 12_400.0, 12_400.0], prior_segment_count=2)
    records += _condition_records(
        "manual_2", [12_400.0, 12_400.0, 12_400.0],
        commit_strategy="manual", prior_segment_count=2,
    )

    report = compare_runs(records, marker_text=MARKER_TEXT)

    assert report.control is not None
    assert report.control.manual_accumulates
    assert "DO accumulate" in report.render_text()


def test_the_control_changing_its_conclusion_changes_the_reported_text() -> None:
    """Guards against a verdict block that reads the same either way."""
    no_accumulation = compare_runs(_matrix(), marker_text=MARKER_TEXT).render_text()
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_2", [12_400.0, 12_400.0, 12_400.0], prior_segment_count=2)
    records += _condition_records(
        "manual_2", [12_400.0, 12_400.0, 12_400.0],
        commit_strategy="manual", prior_segment_count=2,
    )
    accumulation = compare_runs(records, marker_text=MARKER_TEXT).render_text()

    assert no_accumulation != accumulation


def test_a_report_without_the_control_says_so_rather_than_omitting_it() -> None:
    records = _condition_records("vad_0", [12_200.0])
    records += _condition_records("vad_1", [12_300.0], prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    assert report.control is None
    assert "says nothing about whether" in report.render_text()


def test_the_report_carries_the_reporter_figures_and_their_date() -> None:
    """The report is dated evidence, and says whose finding it is checking."""
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert report.claims_source == REPORTER_CLAIMS.source
    assert report.claims_filed == REPORTER_CLAIMS.filed
    text = report.render_text()
    assert REPORTER_CLAIMS.source in text
    assert REPORTER_CLAIMS.filed in text


def test_the_reporters_manual_figure_is_compared_not_just_echoed() -> None:
    """~+10 ms against a +9 ms floor is a ~+1 ms step, and that is what is compared."""
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert report.control is not None
    assert report.control.claimed_manual_step_ms == pytest.approx(1.0)


# --- Commit counts are evidence, not assumptions ------------------------------


def test_the_observed_commit_count_is_reported_for_each_condition() -> None:
    """The number of preceding commits is the independent variable, so it is checked."""
    report = compare_runs(_matrix(), marker_text=MARKER_TEXT)

    assert _summary(report, "vad_0").observed_commit_count == (1, 1, 1)
    assert _summary(report, "vad_1").observed_commit_count == (2, 2, 2)
    assert _summary(report, "vad_2").observed_commit_count == (3, 3, 3)
    assert _summary(report, "manual_2").observed_commit_count == (3, 3, 3)


def test_the_intended_commit_count_is_kept_separate_from_the_observed_one() -> None:
    """A run that returned a different number of commits is not the condition it claims."""
    records = _condition_records("vad_0", [12_200.0])
    records += _condition_records("vad_1", [12_300.0], prior_segment_count=1, commits=5)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    summary = _summary(report, "vad_1")
    assert summary.prior_segment_count == 1
    assert summary.observed_commit_count == (5,)


def test_the_commit_count_is_derived_from_the_events_not_declared() -> None:
    record = _record("vad_0", repeat_index=0, marker_start_ms=12_200.0, commits=4)

    assert observed_commit_count(record) == 4


def test_the_text_rendering_shows_the_observed_commit_count() -> None:
    text = compare_runs(_matrix(), marker_text=MARKER_TEXT).render_text()

    assert "commits" in text
    assert "1/1/1" in text  # vad_0 across its three repeats


# --- Quantisation is not disagreement -----------------------------------------


def test_a_measured_step_within_one_quantum_of_the_claim_reproduces() -> None:
    """Timestamps land on 20 ms steps, so a 20 ms gap is one tick, not a refutation."""
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_2", [12_380.0, 12_380.0, 12_380.0], prior_segment_count=2)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    comparison = next(c for c in report.claims if c.condition_id == "vad_2")
    assert comparison.measured_step_ms == pytest.approx(180.0)
    assert comparison.difference_ms == pytest.approx(-20.0)
    assert comparison.reproduces


def test_a_measured_step_further_out_than_one_quantum_does_not_reproduce() -> None:
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_1", [12_340.0, 12_340.0, 12_340.0], prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    comparison = next(c for c in report.claims if c.condition_id == "vad_1")
    assert not comparison.reproduces


def test_the_tolerance_is_one_quantum_not_a_free_float() -> None:
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_2", [12_380.0, 12_380.0, 12_380.0], prior_segment_count=2)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    comparison = next(c for c in report.claims if c.condition_id == "vad_2")
    assert pytest.approx(20.0) == TIMESTAMP_QUANTUM_MS
    assert abs(comparison.difference_ms) == pytest.approx(TIMESTAMP_QUANTUM_MS)


def test_the_quantisation_the_tolerance_relies_on_is_stated_in_the_report() -> None:
    """A tolerance is only honest if the reader is told what it absorbs."""
    text = compare_runs(_matrix(), marker_text=MARKER_TEXT).render_text()

    assert "20 ms" in text


# --- A noisy baseline must not manufacture drift ------------------------------


def test_a_noisy_anchor_does_not_manufacture_drift() -> None:
    """Deltas are measured against the anchor's median.

    If the baseline itself wobbles by 30 ms, every condition measured against that
    median inherits the wobble, and calling the result drift would blame the
    commits for the baseline's noise.
    """
    records = _condition_records("vad_0", [12_180.0, 12_200.0, 12_220.0])
    records += _condition_records("vad_1", [12_190.0, 12_210.0, 12_230.0], prior_segment_count=1)

    report = compare_runs(records, marker_text=MARKER_TEXT)

    assert not report.drift_detected


def test_a_noisy_anchor_widens_the_interval_a_claim_must_land_in() -> None:
    """The anchor's spread is the baseline's own uncertainty, so it widens the interval.

    Measured step is +180 either way, but a steady baseline puts it in a
    [180, 180] interval while a +/-20 ms baseline puts it in [160, 200]. Only the
    second honestly represents what the run knew.
    """
    steady = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    steady += _condition_records("vad_2", [12_380.0, 12_380.0, 12_380.0], prior_segment_count=2)
    noisy = _condition_records("vad_0", [12_180.0, 12_200.0, 12_220.0])
    noisy += _condition_records("vad_2", [12_380.0, 12_380.0, 12_380.0], prior_segment_count=2)

    steady_interval = next(
        c for c in compare_runs(steady, marker_text=MARKER_TEXT).claims
        if c.condition_id == "vad_2"
    ).measured_interval_ms
    noisy_interval = next(
        c for c in compare_runs(noisy, marker_text=MARKER_TEXT).claims
        if c.condition_id == "vad_2"
    ).measured_interval_ms

    assert steady_interval[0] == pytest.approx(180.0)
    assert steady_interval[1] == pytest.approx(180.0)
    assert noisy_interval[0] == pytest.approx(140.0)
    assert noisy_interval[1] == pytest.approx(220.0)
    assert noisy_interval[1] - noisy_interval[0] > steady_interval[1] - steady_interval[0]


def test_the_control_is_compared_to_the_vad_condition_with_the_same_commit_count() -> None:
    """Compared to the anchor, a 2-commit manual run would be blamed for its own commits."""
    records = _condition_records("vad_0", [12_200.0, 12_200.0, 12_200.0])
    records += _condition_records("vad_1", [12_300.0, 12_300.0, 12_300.0], prior_segment_count=1)
    records += _condition_records("vad_2", [12_400.0, 12_400.0, 12_400.0], prior_segment_count=2)
    records += _condition_records(
        "manual_2", [12_200.0, 12_200.0, 12_200.0],
        commit_strategy="manual", prior_segment_count=2,
    )

    report = compare_runs(records, marker_text=MARKER_TEXT)

    assert report.control is not None
    assert report.control.compares_to_condition_id == "vad_2"
    # The anchor is 0-prior, so matching on prior_segment_count would pick it.
    assert report.anchor_condition_id == "vad_0"
    assert report.control.vad_delta_ms == pytest.approx(200.0)
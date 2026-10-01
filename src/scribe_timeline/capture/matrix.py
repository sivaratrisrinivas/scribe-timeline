"""Running the whole matrix and reporting the comparison.

    make matrix

Four conditions, three repeats each: the VAD family with zero, one, and two
preceding commits, plus a manual control that plays byte-identical audio to the
two-preceding-commit condition with the same number of commits requested
explicitly. The control differs from `vad_2` in one respect only -- who chose
the cut points -- so any difference between them is attributable to the commit
strategy rather than to the audio.

Repeats are interleaved rather than run condition-by-condition. Twelve runs take
a few minutes, and anything that drifts over that window (server-side load, a
deploy) would otherwise land entirely on whichever conditions happened to run
last. Interleaving spreads that risk across every condition instead of
confounding it with the condition.

The comparison itself lives in `scribe_timeline.analysis.compare` and is built
from saved run records, so `--from-saved` re-derives the whole report without
spending a cent of API time.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scribe_timeline.analysis.compare import (
    ComparisonReport,
    compare_runs,
    marker_timestamp_ms,
)
from scribe_timeline.analysis.matching import MarkerNotFound
from scribe_timeline.audio.family import MARKER_TEXT, Condition, build_vad_family, manual_control
from scribe_timeline.audio.timeline import Clip
from scribe_timeline.capture.clips import FixtureError, load_clips
from scribe_timeline.capture.plan import CapturePlan, build_plan
from scribe_timeline.capture.runner import (
    LANGUAGE_CODE,
    MODEL_ID,
    VAD_OPTIONS,
    CaptureError,
    new_run_id,
    run_condition,
)
from scribe_timeline.records import CommitStrategy, RunRecord

#: The fixture geometry, restated here rather than imported from the probe.
#:
#: The probe is a single-condition diagnostic and its constants are its own; a
#: matrix that silently inherited them would redefine the experiment whenever the
#: probe was edited. Restating them here makes the geometry the matrix measures
#: explicit at the point it is measured, and the tests assert the placement that
#: results.
SAMPLE_RATE = 16_000
TOTAL_SECONDS = 18
SLOT_SECONDS = 6
MARKER_SECOND = 12

#: Three per condition. Enough that the spread is worth reporting, which one
#: number per condition would not be.
REPEATS = 3

#: Floor on repeats, enforced rather than documented.
#:
#: With one repeat, `spread_ms` is 0.0 by arithmetic rather than by observation, and
#: a report would print a confident-looking column of zeros. That is precisely the
#: "a single number is not a measurement" failure the comparison module is built to
#: avoid, so it is refused at the point where it can still be refused cheaply.
MIN_REPEATS = 2

RUNS_DIR = Path("runs")
COMPARISON_FILENAME = "comparison.json"


@dataclass(frozen=True)
class MatrixCondition:
    """A condition to run, paired with the strategy that triggers its commits.

    Held together deliberately. `Condition` describes the audio and carries no
    strategy, so a matrix described as bare conditions would have to recover the
    strategy from the condition id -- and a VAD run mislabelled as manual, or the
    reverse, would quietly destroy the control.
    """

    condition: Condition
    commit_strategy: CommitStrategy

    @property
    def id(self) -> str:
        return self.condition.id

    @property
    def prior_segment_count(self) -> int:
        return self.condition.prior_segment_count


@dataclass(frozen=True)
class PlannedRun:
    """One run of the matrix: a condition, a strategy, and a repeat index."""

    condition: Condition
    commit_strategy: CommitStrategy
    repeat_index: int

    @property
    def label(self) -> str:
        return f"{self.condition.id} rep{self.repeat_index} ({self.commit_strategy})"


def plan_matrix(
    matrix: Sequence[MatrixCondition], *, repeats: int = REPEATS
) -> tuple[PlannedRun, ...]:
    """The runs to perform, interleaved by repeat.

    The anchor is not forced to the front. Interleaving matters more than
    ordering convenience: a baseline measured at a different moment from the
    conditions measured against it is a baseline that can drift too.
    """
    return tuple(
        PlannedRun(
            condition=entry.condition,
            commit_strategy=entry.commit_strategy,
            repeat_index=repeat,
        )
        for repeat in range(repeats)
        for entry in matrix
    )


def build_matrix(clips: dict[str, Clip]) -> tuple[MatrixCondition, ...]:
    """The VAD family plus its manual control."""
    family = build_vad_family(
        clips=clips,
        sample_rate=SAMPLE_RATE,
        total_samples=SAMPLE_RATE * TOTAL_SECONDS,
        earlier_starts=(0, SAMPLE_RATE * SLOT_SECONDS),
        final_marker_start=SAMPLE_RATE * MARKER_SECOND,
    )
    return (
        *(
            MatrixCondition(condition=condition, commit_strategy="vad")
            for condition in family
        ),
        MatrixCondition(
            condition=manual_control(family[-1]),
            commit_strategy="manual",
        ),
    )


def planned_commit_boundaries(
    entry: MatrixCondition, clips: dict[str, Clip]
) -> tuple[int, ...]:
    """Where this condition's explicit commits would fall.

    Empty for every VAD condition, by design: under VAD the server's own
    segmentation is the thing under test, and asking for commits at chosen samples
    would replace the variable being measured with a different one. Only the manual
    control uses these, and reporting them here puts the control's geometry next to
    the condition it controls for rather than buried in the send loop.
    """
    if entry.commit_strategy != "manual":
        return ()

    plan: CapturePlan = build_plan(
        condition=entry.condition,
        clips=clips,
        marker_text=MARKER_TEXT,
        commit_strategy=entry.commit_strategy,
    )
    return plan.manual_commit_boundaries


async def run_matrix(
    matrix: Sequence[MatrixCondition], *, repeats: int = REPEATS, verbose: bool = True
) -> list[RunRecord]:
    """Run every planned condition `repeats` times, saving each record."""
    clips = load_clips()
    RUNS_DIR.mkdir(exist_ok=True)

    records: list[RunRecord] = []
    planned = plan_matrix(matrix, repeats=repeats)
    total = len(planned)
    for index, run in enumerate(planned, start=1):
        if verbose:
            print(f"[{index}/{total}] {run.label} ...", flush=True)
        record = await run_condition(
            condition=run.condition,
            clips=clips,
            marker_text=MARKER_TEXT,
            run_id=new_run_id(run.condition.id, run.repeat_index),
            repeat_index=run.repeat_index,
            commit_strategy=run.commit_strategy,
        )
        path = RUNS_DIR / f"{record.run_id}.json"
        path.write_text(record.model_dump_json(indent=2))
        if verbose:
            seen = marker_timestamp_ms(record, MARKER_TEXT)
            print(f"          marker at {seen:,.1f} ms -> {path.name}", flush=True)
        records.append(record)
    return records


def compare_saved(records: Sequence[RunRecord]) -> ComparisonReport:
    """The comparison over a set of run records.

    Named for what it does rather than `report`, which is what the probe's
    same-named function is: it prints and returns nothing. Two functions with one
    name and different contracts is exactly the ambiguity worth removing.
    """
    return compare_runs(records, marker_text=MARKER_TEXT)


def load_records(paths: Sequence[Path]) -> list[RunRecord]:
    """Read run records back from disk.

    The same comparison is rebuilt from saved records as from live ones, which is
    what makes the report checkable by a reader who spends nothing to run it.
    """
    return [RunRecord.model_validate_json(path.read_text()) for path in paths]


async def _run_live(repeats: int) -> int:
    try:
        matrix = build_matrix(load_clips())
    except FixtureError as exc:
        print(f"fixture error: {exc}", file=sys.stderr)
        return 2

    if repeats < MIN_REPEATS:
        print(
            f"refusing to run with {repeats} repeat(s) per condition: the ticket "
            f"requires at least {MIN_REPEATS}, because a spread across fewer "
            "repeats cannot be distinguished from a single number",
            file=sys.stderr,
        )
        return 2

    clips = load_clips()
    print(f"model           {MODEL_ID}  language {LANGUAGE_CODE}")
    for key, value in VAD_OPTIONS.items():
        print(f"  {key:<28} {value}")
    print()
    for entry in matrix:
        boundaries = planned_commit_boundaries(entry, clips)
        where = (
            f"explicit commits at samples {list(boundaries)}"
            if entry.commit_strategy == "manual"
            else "commits chosen by the server's VAD"
        )
        print(
            f"condition {entry.id:<10} {entry.commit_strategy:<7} "
            f"prior segments: {entry.prior_segment_count}  ({where})"
        )
    print()

    try:
        records = await run_matrix(matrix, repeats=repeats)
    except (CaptureError, MarkerNotFound) as exc:
        print(f"\ncapture failed: {exc}", file=sys.stderr)
        return 1

    _emit(compare_saved(records))
    return 0


def _from_saved(paths: list[Path]) -> int:
    missing = [path for path in paths if not path.exists()]
    if missing:
        print(f"no such run record(s): {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 2
    _emit(compare_saved(load_records(paths)))
    return 0


def _emit(comparison: ComparisonReport) -> None:
    print()
    print(comparison.render_text())
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / COMPARISON_FILENAME
    path.write_text(comparison.to_json())
    print(f"\nsaved            {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repeats",
        type=int,
        default=REPEATS,
        help=f"repeats per condition, minimum {MIN_REPEATS} (default: {REPEATS})",
    )
    parser.add_argument(
        "--from-saved",
        nargs="+",
        type=Path,
        metavar="RUN_RECORD",
        help="re-derive the comparison from saved run records, spending no API time",
    )
    args = parser.parse_args(argv)

    if args.repeats < 1:
        print("--repeats must be at least 1", file=sys.stderr)
        return 2
    if args.from_saved:
        return _from_saved(args.from_saved)
    return asyncio.run(_run_live(args.repeats))


if __name__ == "__main__":
    raise SystemExit(main())
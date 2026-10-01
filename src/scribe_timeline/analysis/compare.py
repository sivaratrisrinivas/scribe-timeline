"""The measurement: comparing one word's timestamp across conditions.

Everything downstream of the run-record boundary, and nothing above it. This
module reads run records and produces numbers; it never re-runs anything and
never talks to the API.

Two rules are load-bearing, and both exist because breaking them produces a
plausible wrong answer rather than an error.

**A delta is a difference between two conditions, never between a timestamp and
an insertion point.** Insertion point is not word onset, so measuring against it
assumes the answer. The anchor is the zero-preceding-commit condition, and every
other condition's delta is measured against the anchor's median. The gap between
the anchor's timestamp and its clip's insertion point *is* recorded, because a
reader comparing against the original report needs to see that it is ~200 ms of
this project's own fixture padding -- but it is kept in a separate `context`
field and never enters a delta.

**One number per condition is not a measurement.** Every condition keeps all of
its repeats and reports the spread across them. A claim is called reproduced only
when the measured interval cannot distinguish it from the claim; otherwise it is
reported as not reproduced, which is an equally good outcome.

The original report's figures (+9 / +109 / +209 ms) are offsets against an
insertion point. Only their *steps* -- the difference between conditions -- are
comparable to what this module computes, so that is what the comparison uses.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

from scribe_timeline.analysis.matching import MarkerNotFound, locate_marker
from scribe_timeline.audio.timeline import CLIP_SILENCE_MS
from scribe_timeline.capture.completion import TIMESTAMPED_EVENT
from scribe_timeline.records import CommitStrategy, RunRecord, prior_segment_count

#: The original report, and the date its figures were filed.
REPORTER_SOURCE = "elevenlabs-python#849"
REPORTER_FILED = "2026-08-19"

#: Granularity of the timestamps the realtime API returns.
#:
#: Every marker timestamp observed in live capture (2026-10-02) landed on a 20 ms
#: multiple: 12200, 12300, 12380. The API does not document this, so it is an
#: observation from the raw events rather than a stated guarantee.
#:
#: It matters because a 20 ms gap between a measured step and a claimed step is one
#: quantisation step, not a disagreement about behaviour. Without this, a
#: reproduction within one step would be reported as a failure to reproduce.
TIMESTAMP_QUANTUM_MS = 20.0

#: How far a measured step may sit from a claimed step and still count as matching.
#:
#: One quantum, so a measurement that lands on the neighbouring tick is called a
#: match rather than a refutation. Half a quantum would not do: the true step is
#: only known to within a tick, so the nearest representable measurements of two
#: identical steps can already differ by a full tick.
_CLAIM_EPSILON_MS = TIMESTAMP_QUANTUM_MS

#: Largest manual-control delta still called "does not accumulate".
#:
#: One quantum, matching the resolution the API actually returns. Anything the
#: server can distinguish is a real movement; anything smaller is not a step.
_ACCUMULATION_EPSILON_MS = TIMESTAMP_QUANTUM_MS


@dataclass(frozen=True)
class ReporterClaims:
    """The original report's figures, kept exactly as reported.

    `claimed_offset_ms` holds the report's own offsets from the marker's clip
    insertion point. Those are *not* comparable to what this project measures,
    which is anchor-relative; only their steps are, and `claimed_step_ms` is the
    one place that conversion happens.

    The zero-commit floor is kept rather than dropped. A constant shift with no
    preceding commit is a different finding from per-commit drift, so discarding it
    would lose the distinction the report was making.
    """

    source: str
    filed: str
    claimed_offset_ms: Mapping[str, float]
    claimed_manual_offset_ms: float
    claimed_manual_condition_id: str
    zero_commit_condition_id: str

    def claimed_step_ms(self, condition_id: str) -> float | None:
        """The report's figure expressed as a step away from its own zero-commit case.

        The report states offsets, this project measures differences. Converting
        here means the arithmetic happens once, in one place, rather than being
        re-done by whoever reads the output.
        """
        floor = self.claimed_offset_ms.get(self.zero_commit_condition_id)
        claimed = self.claimed_offset_ms.get(condition_id)
        if floor is None or claimed is None:
            return None
        return claimed - floor

    def claimed_manual_step_ms(self) -> float:
        """The report's manual figure, also expressed as a step.

        The report gives ~+10 ms across three manual commits against a +9 ms floor,
        i.e. a step of about +1 ms: manual commits were not observed to accumulate.
        That is the comparison the manual control exists to check.
        """
        floor = self.claimed_offset_ms.get(self.zero_commit_condition_id, 0.0)
        return self.claimed_manual_offset_ms - floor


REPORTER_CLAIMS = ReporterClaims(
    source=REPORTER_SOURCE,
    filed=REPORTER_FILED,
    claimed_offset_ms={"vad_0": 9.0, "vad_1": 109.0, "vad_2": 209.0},
    claimed_manual_offset_ms=10.0,
    claimed_manual_condition_id="manual_2",
    zero_commit_condition_id="vad_0",
)


@dataclass(frozen=True)
class ConditionSummary:
    """Everything measured for one condition, across all of its repeats.

    `prior_segment_count` is how many speech segments the fixture placed before the
    marker -- the intended number of preceding commits. `observed_commit_count` is
    how many timestamped commits the run actually returned.

    They are kept separate on purpose. The first is what the experiment asked for;
    only the second is evidence of what happened, and a condition that returned a
    different number of commits is not the condition it claims to be.
    """

    condition_id: str
    commit_strategy: CommitStrategy
    prior_segment_count: int
    """Speech segments the fixture placed before the marker -- the intended number
    of preceding commits, which for the manual control is the number of boundaries
    the runner asked for."""

    observed_commit_count: tuple[int, ...]
    """Timestamped commits each repeat actually returned.

    Kept per repeat rather than as one total, because a condition whose repeats
    disagreed with each other would be hidden by an average.
    """
    repeat_count: int
    marker_start_ms: tuple[float, ...]
    median_marker_ms: float
    spread_ms: float
    is_anchor: bool
    delta_vs_anchor_ms: float | None
    delta_spread_ms: float | None
    delta_interval_ms: tuple[float, float] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "commit_strategy": self.commit_strategy,
            "prior_segment_count": self.prior_segment_count,
            "observed_commit_count": list(self.observed_commit_count),
            "repeat_count": self.repeat_count,
            "marker_start_ms": list(self.marker_start_ms),
            "median_marker_ms": self.median_marker_ms,
            "spread_ms": self.spread_ms,
            "is_anchor": self.is_anchor,
            "delta_vs_anchor_ms": self.delta_vs_anchor_ms,
            "delta_spread_ms": self.delta_spread_ms,
            "delta_interval_ms": (
                list(self.delta_interval_ms) if self.delta_interval_ms else None
            ),
        }


@dataclass(frozen=True)
class ClaimComparison:
    """One condition's measured step against the reporter's claimed step."""

    condition_id: str
    measured_step_ms: float
    measured_interval_ms: tuple[float, float]
    claimed_step_ms: float
    difference_ms: float
    reproduces: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "measured_step_ms": self.measured_step_ms,
            "measured_interval_ms": list(self.measured_interval_ms),
            "claimed_step_ms": self.claimed_step_ms,
            "difference_ms": self.difference_ms,
            "reproduces": self.reproduces,
        }


@dataclass(frozen=True)
class ConditionContext:
    """Per-condition figures that must not be mistaken for the measurement.

    `insertion_point_gap_ms` is the marker's returned timestamp minus the sample its
    clip was inserted at. It is recorded because the original report states its
    figures in exactly those terms, so a reader comparing the two needs to see this
    number and see why the two are not comparable.

    It is not a measured offset and it is not "fixture padding": the padding
    (`CLIP_SILENCE_MS`, 120 ms) is one component, but the gap also contains the
    word's onset within its clip, which is not precisely known. For the anchor,
    where nothing precedes the marker, the gap is padding plus onset. For every
    other condition it additionally contains whatever the preceding commits did to
    the timeline -- which is the effect under study, not a constant.

    So it is reported per condition, described as what it is, and never used in a
    delta. The floor is stated separately, because only the anchor's gap is a floor.
    """

    condition_id: str
    insertion_point_gap_ms: float
    clip_padding_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "insertion_point_gap_ms": self.insertion_point_gap_ms,
            "clip_padding_ms": self.clip_padding_ms,
            "note": (
                "Marker timestamp minus the sample its clip was inserted at. Not a "
                "measured offset, and excluded from every delta. Contains the "
                f"clip's {self.clip_padding_ms:.0f} ms of leading silence plus the "
                "word's onset within the clip, which is not precisely known; for "
                "any condition with preceding commits it also contains their "
                "effect."
            ),
        }


@dataclass(frozen=True)
class ComparisonContext:
    """Everything kept deliberately out of the measurement."""

    conditions: tuple[ConditionContext, ...]

    @property
    def insertion_point_gap_ms(self) -> Mapping[str, float]:
        return {c.condition_id: c.insertion_point_gap_ms for c in self.conditions}

    def to_dict(self) -> dict[str, Any]:
        return {
            "conditions": [c.to_dict() for c in self.conditions],
            "note": (
                "These figures are not the measurement. Every delta in this report "
                "is one condition's median marker timestamp minus the anchor's."
            ),
        }


@dataclass(frozen=True)
class ControlConclusion:
    """What the manual control concluded about accumulation.

    The control's one job is to say whether offsets accumulate under manual
    commits. Producing a table row and leaving the reader to infer that is not a
    conclusion, so it is stated here as a boolean with its own evidence attached.
    """

    condition_id: str
    manual_condition_id: str
    compares_to_condition_id: str
    manual_delta_ms: float
    vad_delta_ms: float
    claimed_manual_step_ms: float
    manual_accumulates: bool
    manual_matches_claim: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "manual_condition_id": self.manual_condition_id,
            "compares_to_condition_id": self.compares_to_condition_id,
            "manual_delta_ms": self.manual_delta_ms,
            "vad_delta_ms": self.vad_delta_ms,
            "claimed_manual_step_ms": self.claimed_manual_step_ms,
            "manual_accumulates": self.manual_accumulates,
            "manual_matches_claim": self.manual_matches_claim,
            "interpretation": (
                f"{self.manual_condition_id} played byte-identical audio to "
                f"{self.compares_to_condition_id} with the same number of preceding "
                "commits, differing only in who chose the commit boundaries. "
                + (
                    "Its marker timestamp did not move, so offsets do not accumulate "
                    "under manual commits and the drift seen under VAD is attributable "
                    "to VAD's commit triggering."
                    if not self.manual_accumulates
                    else "Its marker timestamp moved, so the offset accumulates under "
                    "manual commits too, and commit count rather than the VAD "
                    "trigger is the operative variable."
                )
            ),
        }


@dataclass(frozen=True)
class ComparisonReport:
    """The full comparison: every condition, the anchor, and the verdicts."""

    marker_text: str
    anchor_condition_id: str
    conditions: tuple[ConditionSummary, ...]
    claims: tuple[ClaimComparison, ...]
    control: ControlConclusion | None
    context: ComparisonContext
    drift_detected: bool
    claims_source: str
    claims_filed: str
    timestamp_quantum_ms: float
    reporter_claimed_offset_ms: Mapping[str, float]
    reporter_claimed_manual_offset_ms: float

    def condition(self, condition_id: str) -> ConditionSummary:
        for summary in self.conditions:
            if summary.condition_id == condition_id:
                return summary
        raise KeyError(condition_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "marker_text": self.marker_text,
            "anchor_condition_id": self.anchor_condition_id,
            "match_rule_note": (
                "Every delta differences one condition's median marker timestamp "
                "against the anchor's. No timestamp is compared to a clip "
                "insertion point."
            ),
            "drift_detected": self.drift_detected,
            "conditions": [c.to_dict() for c in self.conditions],
            "claims": [c.to_dict() for c in self.claims],
            "control": self.control.to_dict() if self.control else None,
            "context": self.context.to_dict(),
            "timestamp_quantum_ms": self.timestamp_quantum_ms,
            "claims_source": self.claims_source,
            "claims_filed": self.claims_filed,
            "reporter_claimed_offset_ms": dict(self.reporter_claimed_offset_ms),
            "reporter_claimed_manual_offset_ms": self.reporter_claimed_manual_offset_ms,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def render_text(self) -> str:
        """The same numbers, for a reader rather than a parser."""
        lines: list[str] = []
        anchor = self.condition(self.anchor_condition_id)

        lines.append(f"marker            {self.marker_text!r}")
        lines.append(f"anchor            {anchor.condition_id} "
                     f"({anchor.commit_strategy}, no preceding commits)")
        counts = ", ".join(
            f"{c.condition_id}={len(c.marker_start_ms)}" for c in self.conditions
        )
        lines.append(f"repeats           {counts}")
        lines.append("")
        lines.append("Each delta is that condition's median marker timestamp minus the")
        lines.append(
            f"anchor's ({anchor.median_marker_ms:,.1f} ms). No timestamp is compared to a"
        )
        lines.append("clip's insertion point.")
        lines.append("")
        header = (
            f"{'condition':<10} {'strategy':<8} {'prior':>5} {'commits':>8} {'n':>2} "
            f"{'median ms':>11} {'spread':>7} {'delta':>9} {'delta spread':>13}"
        )
        lines.append(header)
        lines.append("-" * len(header))
        for summary in self.conditions:
            delta = (
                "--" if summary.delta_vs_anchor_ms is None
                else f"{summary.delta_vs_anchor_ms:+.1f}"
            )
            delta_spread = (
                "--" if summary.delta_spread_ms is None
                else f"{summary.delta_spread_ms:.1f}"
            )
            observed = (
                "/".join(str(n) for n in summary.observed_commit_count)
            )
            lines.append(
                f"{summary.condition_id:<10} {summary.commit_strategy:<8} "
                f"{summary.prior_segment_count:>5} "
                f"{observed:>8} "
                f"{len(summary.marker_start_ms):>2} "
                f"{summary.median_marker_ms:>11,.1f} "
                f"{summary.spread_ms:>7.1f} "
                f"{delta:>9} {delta_spread:>13}"
            )
        lines.append("")

        lines.append(
            f"Against the reported claim ({self.claims_source}, filed "
            f"{self.claims_filed})"
        )
        lines.append("The report gives offsets from the insertion point; only the step")
        lines.append("between conditions is comparable, so that is what is compared.")
        lines.append(
            f"Returned timestamps land on {self.timestamp_quantum_ms:.0f} ms steps, so a"
        )
        lines.append(
            f"difference within {self.timestamp_quantum_ms:.0f} ms of a claim counts as"
        )
        lines.append("matching it rather than disagreeing with it.")
        lines.append("")
        claim_header = (
            f"{'condition':<10} {'claimed':>9} {'measured':>10} "
            f"{'difference':>11}  verdict"
        )
        lines.append(claim_header)
        lines.append("-" * len(claim_header))
        for comparison in self.claims:
            low, high = comparison.measured_interval_ms
            measured = (
                f"{comparison.measured_step_ms:+.1f}"
                if low == high
                else f"{low:+.1f}..{high:+.1f}"
            )
            verdict = "reproduces" if comparison.reproduces else "does not reproduce"
            lines.append(
                f"{comparison.condition_id:<10} "
                f"{comparison.claimed_step_ms:>+9.1f} "
                f"{measured:>10} "
                f"{comparison.difference_ms:>+11.1f}  {verdict}"
            )
        lines.append("")

        lines.append("Verdict")
        if self.drift_detected:
            lines.append(
                "  A delta excludes zero across its repeats, so the marker's returned"
            )
            lines.append(
                "  timestamp does change with the number of preceding commits."
            )
        else:
            lines.append(
                "  No delta excludes zero across its repeats. On this evidence the"
            )
            lines.append("  marker's returned timestamp does not move with commit count.")
        if self.claims and all(c.reproduces for c in self.claims):
            lines.append("  Every compared condition reproduces the reported step.")
        elif self.claims:
            lines.append("  At least one condition does not reproduce the reported step.")
        lines.append("")

        self._render_control(lines)
        self._render_context(lines)
        return "\n".join(lines)

    def _render_control(self, lines: list[str]) -> None:
        """State the control's conclusion.

        The control exists to answer one question -- do offsets accumulate without
        VAD? -- so it gets a stated answer rather than a table row the reader has
        to interpret.
        """
        lines.append("Manual control")
        if self.control is None:
            lines.append("  Not present, so this report says nothing about whether")
            lines.append("  offsets accumulate under manual commits.")
            lines.append("")
            return

        control = self.control
        lines.append(f"  {control.manual_condition_id} played byte-identical audio to")
        lines.append(
            f"  {control.compares_to_condition_id} with the same number of preceding"
        )
        lines.append(
            f"  commits ({control.manual_delta_ms:+.1f} ms vs "
            f"{control.vad_delta_ms:+.1f} ms), differing only in who chose"
        )
        lines.append("  the commit boundaries.")
        moved = f"{control.manual_delta_ms:+.1f} ms"
        if control.manual_accumulates:
            lines.append(f"  Offsets DO accumulate under manual commits ({moved}),")
            lines.append(
                "  so commit count rather than the VAD trigger is the operative"
            )
            lines.append("  variable.")
        else:
            lines.append(f"  Offsets do NOT accumulate under manual commits ({moved}).")
            lines.append(
                "  The drift seen under VAD is therefore attributable to VAD's commit"
            )
            lines.append("  triggering, not to the number of commits.")
        agreement = "matches" if control.manual_matches_claim else "does not match"
        claimed = f"{control.claimed_manual_step_ms:+.1f} ms"
        lines.append(f"  The report claimed ~{claimed} here; that {agreement}.")
        lines.append("")

    def _render_context(self, lines: list[str]) -> None:
        """Figures that look like offsets and are not.

        The original report states its numbers against the clip's insertion point,
        so a reader comparing the two needs these -- and needs them clearly marked
        as a different quantity, or they would read as the measurement.
        """
        lines.append("Context, not a measurement")
        lines.append("  Each condition's marker timestamp minus the sample its clip")
        lines.append("  was inserted at. The original report's figures are stated in")
        lines.append("  exactly these terms, which is why they are not directly")
        lines.append("  comparable to the deltas above. This figure contains the clip's")
        lines.append("  own leading silence plus the word's onset within the clip --")
        lines.append("  neither of which is precisely known -- and for any condition")
        lines.append("  with preceding commits, their effect too. Excluded from every delta.")
        for entry in self.context.conditions:
            lines.append(
                f"    {entry.condition_id:<10} {entry.insertion_point_gap_ms:>+8.1f} ms"
            )
        lines.append("")
        lines.append(
            "The original reporter is credited as the originator of the finding in"
        )
        lines.append(f"{self.claims_source}, filed {self.claims_filed}.")


def marker_timestamp_ms(record: RunRecord, marker_text: str) -> float:
    """The marker timestamp in one run record, in milliseconds.

    Raises if the marker is absent: a run that lost its marker supports no
    measurement, and scoring it as zero would report an offset that was never
    observed.
    """
    return locate_marker(
        [
            {"text": word.text, "start": word.start_ms, "end": word.end_ms}
            for word in record.words
        ],
        marker_text,
    ).start_ms


def _insertion_point_ms(record: RunRecord, marker_text: str) -> float:
    sample = record.manifest.markers[marker_text]
    return sample * 1000 / record.manifest.sample_rate


def observed_commit_count(record: RunRecord) -> int:
    """How many timestamped commits the server actually returned for this run.

    The number of preceding commits is the experiment's independent variable, so
    it is worth checking that the runs really delivered it. A condition that
    returned a different count than intended is not the condition its id claims,
    and reading its delta as if it were would attribute the wrong cause.
    """
    return sum(1 for event in record.events if event.type == TIMESTAMPED_EVENT)


def _find_anchor(records: Sequence[RunRecord], marker_text: str) -> str:
    anchors = sorted(
        {
            record.condition_id
            for record in records
            if prior_segment_count(record.manifest, marker_text) == 0
            and record.commit_strategy == "vad"
        }
    )
    if not anchors:
        raise ValueError(
            "no anchor condition: the comparison needs a VAD run with no preceding "
            "commits to measure everything else against"
        )
    if len(anchors) > 1:
        raise ValueError(
            f"more than one anchor condition ({', '.join(anchors)}); refusing to pick "
            "between baselines, since which one is chosen decides every delta"
        )
    return anchors[0]


def compare_runs(
    records: Sequence[RunRecord],
    *,
    marker_text: str,
    claims: ReporterClaims = REPORTER_CLAIMS,
) -> ComparisonReport:
    """Measure every condition against the zero-preceding-commit anchor."""
    if not records:
        raise ValueError("no run records to compare")

    anchor_id = _find_anchor(records, marker_text)

    grouped: dict[str, list[RunRecord]] = {}
    for record in records:
        grouped.setdefault(record.condition_id, []).append(record)

    # Ordered once and reused, so a repeat's position never depends on which
    # field happens to be read first.
    ordered = {
        condition_id: sorted(group, key=lambda r: r.repeat_index)
        for condition_id, group in grouped.items()
    }
    times = {
        condition_id: tuple(marker_timestamp_ms(r, marker_text) for r in group)
        for condition_id, group in ordered.items()
    }

    anchor_median = median(times[anchor_id])

    summaries: list[ConditionSummary] = []
    for condition_id, group in grouped.items():
        samples = times[condition_id]
        is_anchor = condition_id == anchor_id
        # The anchor has no delta: a delta of zero against itself would read as
        # "no drift" where the truth is "this is the baseline everything rests on".
        deltas = () if is_anchor else tuple(v - anchor_median for v in samples)

        summaries.append(
            ConditionSummary(
                condition_id=condition_id,
                commit_strategy=group[0].commit_strategy,
                prior_segment_count=prior_segment_count(group[0].manifest, marker_text),
                observed_commit_count=tuple(
                    observed_commit_count(record) for record in group
                ),
                repeat_count=len(samples),
                marker_start_ms=samples,
                median_marker_ms=median(samples),
                spread_ms=max(samples) - min(samples),
                is_anchor=is_anchor,
                delta_vs_anchor_ms=median(deltas) if deltas else None,
                delta_spread_ms=(max(deltas) - min(deltas)) if deltas else None,
                delta_interval_ms=(min(deltas), max(deltas)) if deltas else None,
            )
        )

    summaries.sort(key=lambda s: (s.commit_strategy != "vad", s.prior_segment_count))

    # The anchor's own spread is folded into every interval below. A baseline that
    # wobbles by 30 ms would otherwise manufacture drift in every condition
    # measured against its median, and attribute it to the commits that followed.
    anchor_spread = max(times[anchor_id]) - min(times[anchor_id])

    comparisons: list[ClaimComparison] = []
    for summary in summaries:
        if summary.delta_vs_anchor_ms is None or summary.delta_interval_ms is None:
            continue
        claimed = claims.claimed_step_ms(summary.condition_id)
        if claimed is None:
            continue
        low, high = summary.delta_interval_ms
        # Widened by the anchor's own spread: a claim bracketed only because the
        # baseline happened to sit high is not evidence for the condition.
        low -= anchor_spread
        high += anchor_spread
        comparisons.append(
            ClaimComparison(
                condition_id=summary.condition_id,
                measured_step_ms=summary.delta_vs_anchor_ms,
                measured_interval_ms=(low, high),
                claimed_step_ms=claimed,
                difference_ms=summary.delta_vs_anchor_ms - claimed,
                reproduces=low - _CLAIM_EPSILON_MS <= claimed <= high + _CLAIM_EPSILON_MS,
            )
        )

    drift_detected = any(
        summary.delta_interval_ms is not None
        and (
            summary.delta_interval_ms[0] - anchor_spread > 0
            or summary.delta_interval_ms[1] + anchor_spread < 0
        )
        for summary in summaries
    )

    # Recomputed from the records rather than from `times`, so this context block
    # stays independent of the delta arithmetic it must never influence.
    contexts = tuple(
        ConditionContext(
            condition_id=condition_id,
            insertion_point_gap_ms=median(
                [
                    marker_timestamp_ms(record, marker_text)
                    - _insertion_point_ms(record, marker_text)
                    for record in group
                ]
            ),
            clip_padding_ms=CLIP_SILENCE_MS,
        )
        for condition_id, group in ordered.items()
    )

    control = _control_conclusion(summaries, claims)

    return ComparisonReport(
        marker_text=marker_text,
        anchor_condition_id=anchor_id,
        conditions=tuple(summaries),
        claims=tuple(comparisons),
        control=control,
        context=ComparisonContext(conditions=contexts),
        drift_detected=drift_detected,
        claims_source=claims.source,
        claims_filed=claims.filed,
        timestamp_quantum_ms=TIMESTAMP_QUANTUM_MS,
        reporter_claimed_offset_ms=dict(claims.claimed_offset_ms),
        reporter_claimed_manual_offset_ms=claims.claimed_manual_offset_ms,
    )


def _control_conclusion(
    summaries: Sequence[ConditionSummary], claims: ReporterClaims
) -> ControlConclusion | None:
    """Compare the manual control against the VAD condition it mirrors.

    The control is only meaningful against its counterpart: same audio, same
    number of preceding commits, different trigger. Compared against anything else
    it would confound the strategy with the commit count, which is the one thing
    it exists to separate.

    Returns `None` when either side is missing, which the report states as an
    absence rather than quietly omitting the section.
    """
    manual = next(
        (s for s in summaries if s.condition_id == claims.claimed_manual_condition_id),
        None,
    )
    if manual is None or manual.delta_vs_anchor_ms is None:
        return None

    # Its counterpart is the VAD condition with the same preceding-commit count.
    counterpart = next(
        (
            s
            for s in summaries
            if s.commit_strategy == "vad"
            and s.prior_segment_count == manual.prior_segment_count
        ),
        None,
    )
    if counterpart is None or counterpart.delta_vs_anchor_ms is None:
        return None

    manual_delta = manual.delta_vs_anchor_ms
    vad_delta = counterpart.delta_vs_anchor_ms
    claimed_manual_step = claims.claimed_manual_step_ms()
    return ControlConclusion(
        condition_id=manual.condition_id,
        manual_condition_id=manual.condition_id,
        compares_to_condition_id=counterpart.condition_id,
        manual_delta_ms=manual_delta,
        vad_delta_ms=vad_delta,
        claimed_manual_step_ms=claimed_manual_step,
        # "Accumulates" is a statement about zero, not a threshold: the control's
        # whole claim is that manual commits do not move the timestamp at all.
        manual_accumulates=abs(manual_delta) > _ACCUMULATION_EPSILON_MS,
        manual_matches_claim=abs(manual_delta - claimed_manual_step) <= _CLAIM_EPSILON_MS,
    )


__all__ = [
    "CLIP_SILENCE_MS",
    "REPORTER_CLAIMS",
    "REPORTER_FILED",
    "REPORTER_SOURCE",
    "TIMESTAMP_QUANTUM_MS",
    "ClaimComparison",
    "ComparisonContext",
    "ComparisonReport",
    "ConditionContext",
    "ConditionSummary",
    "ControlConclusion",
    "MarkerNotFound",
    "ReporterClaims",
    "compare_runs",
    "marker_timestamp_ms",
    "observed_commit_count",
]
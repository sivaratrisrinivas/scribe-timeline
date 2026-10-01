"""The fixture family: one timeline per number of preceding speech segments.

Every condition holds the final marker's bytes and sample position identical and
varies only what precedes it. That is what makes the resulting comparison sound:
any difference in the returned timestamps cannot be explained by a difference in
what was played.

One condition is produced per prefix of `earlier_starts`, so the family is
`vad_0` (no preceding segment) through `vad_n`. `vad_0` is the anchor against
which every other condition's delta is measured.

Only the final marker is the measurement target. Earlier segments are unmarked:
they exist to induce a VAD commit, and nothing needs to match them. Marking them
would make exact-text matching ambiguous, which `compose` rejects outright.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from scribe_timeline.audio.timeline import Clip, Placement, TimelineSpec

MARKER_TEXT = "Zorblax"
"""The distinctive word whose returned timestamp is measured.

Chosen to be lexically unusual so a speech recogniser returns it verbatim
instead of normalising it into a common word.
"""

EARLIER_CLIP_ID = "earlier"
MARKER_CLIP_ID = "marker"


@dataclass(frozen=True)
class Condition:
    """One timeline, plus how many speech segments precede the measured marker."""

    id: str
    prior_segment_count: int
    spec: TimelineSpec


def build_vad_family(
    *,
    clips: Mapping[str, Clip],
    sample_rate: int,
    total_samples: int,
    earlier_starts: Sequence[int],
    final_marker_start: int,
    marker_text: str = MARKER_TEXT,
) -> tuple[Condition, ...]:
    """Build one condition per prefix of `earlier_starts`, marker pinned.

    `earlier_starts` are the sample positions available to preceding segments;
    a condition with *n* prior segments uses the first *n* of them, so the
    family grows by adding earlier speech without moving the final marker.
    """
    for required in (EARLIER_CLIP_ID, MARKER_CLIP_ID):
        if required not in clips:
            raise ValueError(f"clip {required!r} is required to build the family")

    conditions: list[Condition] = []
    for prior_count in range(len(earlier_starts) + 1):
        placements = [
            Placement(clip_id=EARLIER_CLIP_ID, start_sample=start)
            for start in earlier_starts[:prior_count]
        ]
        placements.append(
            Placement(
                clip_id=MARKER_CLIP_ID,
                start_sample=final_marker_start,
                marker_text=marker_text,
            )
        )
        conditions.append(
            Condition(
                id=f"vad_{prior_count}",
                prior_segment_count=prior_count,
                spec=TimelineSpec(
                    sample_rate=sample_rate,
                    total_samples=total_samples,
                    placements=tuple(placements),
                ),
            )
        )
    return tuple(conditions)


def manual_control(condition: Condition) -> Condition:
    """The same timeline and commit count as `condition`, triggered manually.

    This is the control. It plays byte-identical audio with the same number of
    preceding commits, and differs only in *who* asked for the cut points: the
    runner, at known samples, rather than the server's voice activity detection.

    Sharing the timeline with the VAD condition it is compared against is the
    whole point. A control built from different audio could differ for reasons
    that have nothing to do with the commit strategy.

    Renamed rather than reused, because a run record's `condition_id` and
    `commit_strategy` are read together; leaving both as `vad_2` would make a
    manual run look like a VAD one in a saved report.
    """
    return replace(condition, id=f"manual_{condition.prior_segment_count}")

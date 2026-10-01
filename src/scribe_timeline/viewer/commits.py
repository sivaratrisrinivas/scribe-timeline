"""Where each commit fell on the audio clock, so a boundary can be marked.

The measurement is a timestamp on the input sample clock, so a commit boundary
drawn against it has to be on that clock too. The run record already keeps the
commits where the server put them: one `committed_transcript_with_timestamps` event
each, carrying that commit's words in whatever unit the API actually used. This
module reads those into milliseconds once, so the viewer can place a boundary
without converting anything itself.

It is deliberately a reader rather than a second measurement. Nothing here
subtracts, medians, or compares two runs -- that is `analysis.compare`, and
duplicating any of it here would be two implementations free to disagree by one
quantum.

Two things are recorded rather than smoothed over:

* **A commit with no placeable word keeps its place in the sequence.** The number
  of commits the server returned is itself a cross-checked figure, so dropping an
  empty commit would renumber every commit after it.
* **A word that cannot be placed is `None`, never `0`.** Zero is a
  legitimate-looking position on an eighteen-second track, and a boundary drawn
  there would read as an observation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from scribe_timeline.capture.completion import TIMESTAMPED_EVENT
from scribe_timeline.records import RunRecord

__all__ = [
    "MS_PER_SOURCE_UNIT",
    "CommitExtent",
    "UnknownSourceUnit",
    "commit_extents",
]

MS_PER_SOURCE_UNIT: Mapping[str, float] = {
    "seconds": 1_000.0,
    "milliseconds": 1.0,
    "ms": 1.0,
}
"""Conversion from the unit a record says the API used, to milliseconds.

Keys are units the realtime API has actually been observed to return, plus
`ms` because a record produced by another tool could legitimately say so. The
mapping is exhaustive on purpose: a unit not in here is refused by
`UnknownSourceUnit` rather than read as seconds, because guessing scales every
boundary by a factor and a scaled timeline still looks like a timeline.
"""


class UnknownSourceUnit(ValueError):
    """The record names a timestamp unit this project cannot convert.

    Raised rather than assumed. Every word timestamp this project has seen came
    back in seconds against a field named `start`, so defaulting to seconds would
    be right by luck and wrong by 1000x the moment the API changed -- and the
    result would be a plausible timeline rather than an error.
    """


@dataclass(frozen=True)
class CommitExtent:
    """One commit, and the span of audio it claimed.

    The span is the first returned word's start to the last one's end. It is not
    the commit *boundary*: the boundary is a cut in the silence between one
    commit's last word and the next one's first, and the record does not say where
    in that gap the server chose to cut. Keeping the extent separate from any
    boundary is what stops the viewer from claiming a precision it does not have.

    `first_word_ms` or `last_word_ms` is `None` when that word could not be
    placed -- absent or a non-numeric timing. `None` is not `0`: a commit at zero
    is a statement, and a wrong one.
    """

    commit_index: int
    """1-based, in the order the commits arrived."""

    word_count: int
    """Words the commit carried, whether or not any of them could be placed."""

    first_word_ms: float | None
    last_word_ms: float | None


def _timing_ms(word: Mapping[str, Any], key: str, scale: float) -> float | None:
    """One timing field in milliseconds, or `None` if it is not a number.

    `bool` is excluded explicitly: it is an `int` in Python, and `True` becoming
    1 ms would be a timestamp nobody sent.
    """
    value = word.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) * scale


def _scale_for(record: RunRecord) -> float:
    unit = record.source_timestamp_unit
    try:
        return MS_PER_SOURCE_UNIT[unit]
    except KeyError:
        known = ", ".join(sorted(MS_PER_SOURCE_UNIT))
        raise UnknownSourceUnit(
            f"run record {record.run_id!r} says its timestamps are in {unit!r}, which is "
            f"not a unit this project converts (known: {known}). Refusing to guess, because "
            f"assuming seconds would scale every commit boundary by a factor and the result "
            f"would still look like a timeline."
        ) from None


def _words_of(event_payload: Mapping[str, Any]) -> list[Any]:
    raw = event_payload.get("words")
    if not isinstance(raw, list):
        return []
    return raw


def commit_extents(record: RunRecord) -> tuple[CommitExtent, ...]:
    """Every timestamped commit in a record, in order, placed on the audio clock.

    Only `committed_transcript_with_timestamps` is read. Partial transcripts carry
    the marker's *text* about a second before its timings do, so treating one as a
    commit would mark a boundary where none was cut.
    """
    scale = _scale_for(record)
    extents: list[CommitExtent] = []

    for event in record.events:
        if event.type != TIMESTAMPED_EVENT:
            continue
        raw_words = _words_of(event.payload)
        placed = [word for word in raw_words if isinstance(word, Mapping)]
        extents.append(
            CommitExtent(
                commit_index=len(extents) + 1,
                word_count=len(raw_words),
                first_word_ms=(
                    _timing_ms(placed[0], "start", scale) if placed else None
                ),
                last_word_ms=(
                    _timing_ms(placed[-1], "end", scale) if placed else None
                ),
            )
        )

    return tuple(extents)

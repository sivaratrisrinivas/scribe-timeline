"""Deciding when a streaming capture has collected enough to become a record.

This exists because "the first timestamped commit arrived" is not the same thing
as "the measurement is possible". The fixture family puts one to two unmarked
speech segments in front of the marker, and under VAD those segments commit
first. A first-commit completion condition would therefore stop on a commit that
contains no marker.

Today that mistake is masked: the send loop streams the whole timeline before the
runner waits for anything, and `SessionCapture.all_words` pools words from every
commit. But those are consequences of ordering, not guarantees. A timeline too
short to give VAD its trailing silence would commit only via the explicit flush,
and a first-commit condition would have closed the socket before that arrived.

So completion means *the marker is present*. A commit cap and a timeout bound the
wait, and both are reported as outcomes distinct from finding the marker: a
capture that ended any other way is incomplete evidence, and the project fails
loudly on incomplete evidence rather than publishing it.

Only `committed_transcript_with_timestamps` is inspected. Partial transcripts
carry the marker's text roughly a second before its timestamps do, so treating one
as the marker would discard the only event the measurement reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from scribe_timeline.analysis.matching import MarkerNotFound, locate_marker

#: The only event that carries word timestamps, and so the only one that can
#: satisfy a capture or support a measurement.
#:
#: Named here because two modules need it and one definition of it is the point:
#: the runner's completion rule and the recorder's word extraction have to agree
#: about which event matters, and two string literals would be free to disagree.
TIMESTAMPED_EVENT = "committed_transcript_with_timestamps"

CompletionOutcome = Literal["listening", "marker-found", "commit-cap-reached"]
"""Why the condition is or is not finished.

`commit-cap-reached` is deliberately not `marker-found`. The caller needs to
distinguish a capture that holds the marker from one that merely ran out of
patience, because only the first supports a measurement.
"""


class CompletionCondition:
    """Tracks whether the measured marker has arrived, within bounds.

    Fed the event type and payload of every observed event. Holds no socket and
    performs no I/O, so the rule can be exercised offline.
    """

    def __init__(self, *, marker_text: str, max_commits: int) -> None:
        if max_commits < 0:
            raise ValueError("max_commits must not be negative")
        self._marker_text = marker_text
        self._max_commits = max_commits
        self._commits_seen = 0
        self._marker_seen = False

    def observe(self, event_type: str, payload: Mapping[str, Any]) -> None:
        """Record one event. Non-timestamped events are ignored."""
        if event_type != TIMESTAMPED_EVENT:
            return

        self._commits_seen += 1
        if not self._marker_seen and self._contains_marker(payload):
            self._marker_seen = True

    @property
    def commits_seen(self) -> int:
        return self._commits_seen

    @property
    def marker_seen(self) -> bool:
        return self._marker_seen

    @property
    def satisfied(self) -> bool:
        """Whether the runner may stop listening."""
        return self._marker_seen or self._commits_seen >= self._max_commits

    @property
    def outcome(self) -> CompletionOutcome:
        if self._marker_seen:
            return "marker-found"
        if self._commits_seen >= self._max_commits:
            return "commit-cap-reached"
        return "listening"

    def _contains_marker(self, payload: Mapping[str, Any]) -> bool:
        raw = payload.get("words")
        if not isinstance(raw, list):
            return False
        words = [word for word in raw if isinstance(word, Mapping)]
        try:
            locate_marker(words, self._marker_text)  # type: ignore[arg-type]
        except MarkerNotFound:
            return False
        return True

    def describe(self) -> str:
        """One line naming why the capture stopped, for an error message."""
        if self._marker_seen:
            return f"{self._marker_text!r} found in {self._commits_seen} timestamped commit(s)"
        return (
            f"{self._marker_text!r} absent from all {self._commits_seen} "
            f"timestamped commit(s) (cap {self._max_commits})"
        )
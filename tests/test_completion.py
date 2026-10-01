"""When does a capture have enough to become a run record?

The runner used to stop listening at the *first* timestamped commit. That looks
obviously wrong once the fixture family has more than one speech segment: under
VAD the earlier segments commit before the marker does, so the first commit
carries no marker at all. Today it happens to be harmless, because the send loop
runs to the end of the timeline before the runner waits, and because
`SessionCapture.all_words` pools words from every commit. Those are accidents of
ordering, not guarantees.

The day the marker stops self-committing -- a timeline too short to give VAD its
trailing silence, say -- the first commit would be an *earlier* segment, the
socket would close, and the marker's commit would be lost. The run would then
fail on a condition that has nothing to do with what it was measuring.

So completion is decided by the marker being present, bounded by a commit cap
and a timeout as backstops. The cap and the timeout are recorded as *distinct*
outcomes from "the marker was found", because a run that stopped for any other
reason is incomplete evidence and has to fail loudly rather than be published.

No socket here: the condition is fed the same event payloads the handler
receives, so it can be exercised offline at zero API cost.
"""

from __future__ import annotations

from typing import Any

from scribe_timeline.audio.family import MARKER_TEXT
from scribe_timeline.capture.completion import (
    TIMESTAMPED_EVENT,
    CompletionCondition,
)

PARTIAL_EVENT = "partial_transcript"


def _commit(*texts: str) -> dict[str, Any]:
    """A `committed_transcript_with_timestamps` payload carrying these words."""
    return {
        "words": [
            {"text": text, "start": 12.2, "end": 12.78, "logprob": -1.3} for text in texts
        ]
    }


def _partial(*texts: str) -> dict[str, Any]:
    """A `partial_transcript` payload, which carries text but no timestamps."""
    return {"text": " ".join(texts)}


def _condition(*, max_commits: int = 8) -> CompletionCondition:
    return CompletionCondition(marker_text=MARKER_TEXT, max_commits=max_commits)


def test_a_commit_without_the_marker_does_not_finish_the_capture() -> None:
    """The bug this replaces: the earlier segments commit before the marker."""
    condition = _condition()

    condition.observe(TIMESTAMPED_EVENT, _commit("Kolvig."))

    assert not condition.satisfied
    assert condition.outcome == "listening"
    assert condition.commits_seen == 1


def test_the_commit_carrying_the_marker_finishes_the_capture() -> None:
    condition = _condition()

    condition.observe(TIMESTAMPED_EVENT, _commit("Kolvig."))
    condition.observe(TIMESTAMPED_EVENT, _commit(MARKER_TEXT + "."))

    assert condition.satisfied
    assert condition.outcome == "marker-found"
    assert condition.commits_seen == 2


def test_a_partial_transcript_mentioning_the_marker_does_not_finish_the_capture() -> None:
    """Partial transcripts carry the text before the timestamps exist.

    Treating one as the marker would close the socket roughly a second before
    the timestamped commit arrived, discarding the only event the measurement
    actually reads.
    """
    condition = _condition()

    condition.observe(PARTIAL_EVENT, _partial(MARKER_TEXT))

    assert not condition.satisfied
    assert condition.commits_seen == 0


def test_a_near_miss_word_does_not_finish_the_capture() -> None:
    """A different word is not the marker, however plausible it looks."""
    condition = _condition()

    condition.observe(TIMESTAMPED_EVENT, _commit("Zorblaxx."))

    assert not condition.satisfied


def test_the_marker_is_found_among_several_words_in_one_commit() -> None:
    condition = _condition()

    condition.observe(TIMESTAMPED_EVENT, _commit("Kolvig.", MARKER_TEXT + ".", "trailing."))

    assert condition.satisfied


def test_a_commit_with_no_words_does_not_finish_the_capture() -> None:
    """A commit can come back empty; that is not the marker."""
    condition = _condition()

    condition.observe(TIMESTAMPED_EVENT, {"words": []})

    assert not condition.satisfied
    assert condition.commits_seen == 1


def test_hitting_the_commit_cap_stops_listening_but_is_not_the_marker() -> None:
    """The cap is a backstop, and stopping for it is incomplete evidence.

    The runner has to be able to tell this apart from finding the marker,
    because a truncated capture must fail loudly instead of being published.
    """
    condition = _condition(max_commits=2)

    condition.observe(TIMESTAMPED_EVENT, _commit("Kolvig."))
    condition.observe(TIMESTAMPED_EVENT, _commit("Kolvig."))

    assert condition.satisfied
    assert condition.outcome == "commit-cap-reached"
    assert not condition.marker_seen


def test_a_commit_with_a_malformed_words_field_is_not_a_crash() -> None:
    """The server owns this payload's shape; a surprise must not kill the run."""
    condition = _condition()

    condition.observe(TIMESTAMPED_EVENT, {"words": "not-a-list"})
    condition.observe(TIMESTAMPED_EVENT, {})

    assert not condition.satisfied
    assert condition.commits_seen == 2


def test_non_commit_events_are_ignored_entirely() -> None:
    condition = _condition()

    condition.observe("session_started", {"config": {"model_id": "scribe_v2_realtime"}})

    assert condition.commits_seen == 0
    assert not condition.satisfied


def test_a_non_string_word_is_not_the_marker() -> None:
    """Matching is exact-text on strings; anything else is simply not a match."""
    condition = _condition()

    condition.observe(TIMESTAMPED_EVENT, {"words": [{"text": None, "start": 1.0, "end": 1.2}]})

    assert not condition.satisfied


def test_a_zero_commit_cap_finishes_immediately_rather_than_never() -> None:
    """A misconfigured cap must not produce a capture that waits forever."""
    condition = _condition(max_commits=0)

    assert condition.satisfied
    assert condition.outcome == "commit-cap-reached"
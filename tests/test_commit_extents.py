"""Where each commit fell on the audio clock.

The measurement is a timestamp on the input sample clock, so anything drawn
against it has to be on that clock too. The run record keeps the commits where the
server put them -- one `committed_transcript_with_timestamps` event each, holding
that commit's words in the unit the API actually used -- and this module reads
them into milliseconds so the viewer can mark a commit boundary without converting
anything itself.

The rules are the ones the rest of the project holds to:

* **The unit is read from the record, not assumed.** Every returned timestamp in
  this project was observed in seconds against a field named `start`, and storing
  12.2 in a `ms` field would be wrong by 1000x while looking entirely plausible.
  An unrecognised unit is refused rather than guessed at.
* **A commit that carried no placeable word keeps its place in the sequence.**
  The number of commits is itself a recorded, checked figure, so dropping an
  empty one would renumber every commit after it and misdescribe the run.
* **A word that cannot be placed is missing, not zero.** `None` is the only
  honest answer, and it is distinct from a word at 0 ms.
"""

from __future__ import annotations

import pytest

from scribe_timeline.audio.family import MARKER_TEXT
from scribe_timeline.capture.completion import TIMESTAMPED_EVENT
from scribe_timeline.records import (
    EchoedSessionConfig,
    Manifest,
    RawEvent,
    RunRecord,
    WordTiming,
)
from scribe_timeline.viewer.commits import UnknownSourceUnit, commit_extents

SAMPLE_RATE = 16_000


def _commit(*words: object, received_at_ms: float = 0.0) -> RawEvent:
    return RawEvent(
        type=TIMESTAMPED_EVENT,
        received_at_ms=received_at_ms,
        payload={"words": list(words)},
    )


def _word(text: str, start: float, end: float) -> dict[str, object]:
    return {"text": text, "start": start, "end": end}


def _record(
    events: tuple[RawEvent, ...], *, source_timestamp_unit: str = "seconds"
) -> RunRecord:
    return RunRecord(
        schema_version=1,
        run_id="run-a",
        condition_id="vad_2",
        commit_strategy="vad",
        repeat_index=0,
        manifest=Manifest(
            condition_id="vad_2",
            sample_rate=SAMPLE_RATE,
            sample_count=SAMPLE_RATE * 18,
            markers={MARKER_TEXT: 192_000},
        ),
        echoed_config=EchoedSessionConfig(
            model_id="scribe_v2_realtime",
            sample_rate=SAMPLE_RATE,
            include_timestamps=True,
        ),
        events=events,
        words=(),
        source_timestamp_unit=source_timestamp_unit,
    )


def test_each_commit_is_numbered_in_arrival_order() -> None:
    extents = commit_extents(
        _record(
            (
                _commit(_word("Kolvig.", 0.22, 0.64)),
                _commit(_word("Kolvig.", 6.32, 6.72)),
                _commit(_word("Zorblax.", 12.38, 12.98)),
            )
        )
    )

    assert [extent.commit_index for extent in extents] == [1, 2, 3]


def test_a_commit_is_placed_from_its_first_and_last_word() -> None:
    # The marker's returned position is the observation the whole project rests on,
    # so it has to survive the trip from the raw event to the timeline unchanged.
    extents = commit_extents(_record((_commit(_word("Zorblax.", 12.38, 12.98)),)))

    assert extents[0].first_word_ms == pytest.approx(12_380.0)
    assert extents[0].last_word_ms == pytest.approx(12_980.0)
    assert extents[0].word_count == 1


def test_a_commit_spanning_several_words_reaches_from_its_first_to_its_last() -> None:
    extents = commit_extents(
        _record((_commit(_word("Kolvig.", 0.22, 0.64), _word("Tarn", 0.7, 1.1)),))
    )

    assert extents[0].first_word_ms == pytest.approx(220.0)
    assert extents[0].last_word_ms == pytest.approx(1_100.0)
    assert extents[0].word_count == 2


def test_timestamps_are_converted_from_the_unit_the_record_names() -> None:
    """Seconds are what the API returned; milliseconds are handled without guessing."""
    seconds = commit_extents(_record((_commit(_word("Zorblax.", 12.38, 12.98)),)))
    millis = commit_extents(
        _record(
            (_commit(_word("Zorblax.", 12_380.0, 12_980.0)),),
            source_timestamp_unit="milliseconds",
        )
    )

    assert seconds[0].first_word_ms == pytest.approx(millis[0].first_word_ms)


def test_an_unrecognised_unit_is_refused_rather_than_assumed() -> None:
    # Assuming seconds for a record in some other unit would scale every boundary
    # by a factor, and a scaled timeline still looks like a timeline.
    with pytest.raises(UnknownSourceUnit, match="fortnights"):
        commit_extents(
            _record(
                (_commit(_word("Zorblax.", 12.38, 12.98)),),
                source_timestamp_unit="fortnights",
            )
        )


def test_a_commit_with_no_words_keeps_its_place_in_the_sequence() -> None:
    """The commit count is a recorded, cross-checked figure.

    Dropping an empty commit would renumber the ones after it, so a run that
    returned three commits would be drawn as having two.
    """
    extents = commit_extents(
        _record(
            (
                _commit(_word("Kolvig.", 0.22, 0.64)),
                _commit(),
                _commit(_word("Zorblax.", 12.38, 12.98)),
            )
        )
    )

    assert [extent.commit_index for extent in extents] == [1, 2, 3]
    assert extents[1].word_count == 0
    assert extents[1].first_word_ms is None
    assert extents[1].last_word_ms is None


def test_a_commit_whose_payload_has_no_word_list_is_still_a_commit() -> None:
    extents = commit_extents(
        _record(
            (
                RawEvent(type=TIMESTAMPED_EVENT, received_at_ms=0.0, payload={"text": ""}),
                _commit(_word("Zorblax.", 12.38, 12.98)),
            )
        )
    )

    assert [extent.commit_index for extent in extents] == [1, 2]
    assert extents[0].word_count == 0


def test_a_word_with_an_unusable_timing_is_missing_rather_than_zero() -> None:
    # Zero is a legitimate-looking position. Substituting it would draw a commit
    # boundary at the very start of the audio and read as a real observation.
    extents = commit_extents(
        _record((_commit({"text": "Zorblax.", "start": None, "end": 12.98}),))
    )

    assert extents[0].first_word_ms is None
    assert extents[0].last_word_ms == pytest.approx(12_980.0)
    assert extents[0].word_count == 1


def test_a_first_or_last_word_that_is_not_an_object_is_missing() -> None:
    extents = commit_extents(
        _record((_commit("not a word", _word("Zorblax.", 12.38, 12.98)),))
    )

    assert extents[0].first_word_ms == pytest.approx(12_380.0)
    assert extents[0].word_count == 2


def test_events_that_are_not_commits_are_ignored() -> None:
    # `partial_transcript` carries the marker's text a second before its timings
    # do. Counting one as a commit would put a boundary where none was cut.
    record = _record(
        (
            RawEvent(type="partial_transcript", received_at_ms=0.1, payload={"words": []}),
            RawEvent(type="session_started", received_at_ms=0.2, payload={}),
            _commit(_word("Zorblax.", 12.38, 12.98)),
        )
    )

    assert [extent.commit_index for extent in commit_extents(record)] == [1]


def test_a_run_that_returned_no_commits_reports_none_rather_than_one_at_zero() -> None:
    assert commit_extents(_record(())) == ()


def test_the_extent_of_a_committed_marker_agrees_with_the_record_s_own_word() -> None:
    """Two independent paths to the same number must not be able to disagree.

    The record's `words` field is the pooled, converted transcript; the commit's
    extent is read from the raw event. A disagreement would mean the conversion
    happened twice, differently.
    """
    record = _record(
        (_commit(_word("Zorblax.", 12.38, 12.98)),),
    )
    pooled = record.model_copy(
        update={"words": (WordTiming(text="Zorblax.", start_ms=12_380.0, end_ms=12_980.0),)}
    )

    assert commit_extents(pooled)[0].first_word_ms == pytest.approx(
        pooled.words[0].start_ms
    )

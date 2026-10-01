"""Locating the measured marker in a returned transcript.

Matching is exact and fails loudly. A fuzzy fallback would return a plausible
timestamp for the wrong word, which is the single worst outcome this project can
produce: a number that looks measured but is not.

The rule is recorded in the run record, so a later reader can tell which word was
actually measured rather than inferring it.
"""

from __future__ import annotations

import pytest

from scribe_timeline.analysis.matching import (
    MATCH_RULE,
    MarkerNotFound,
    locate_marker,
)


def _word(text: str, start_ms: float, end_ms: float) -> dict[str, object]:
    return {"text": text, "start": start_ms, "end": end_ms}


def test_finds_the_marker_and_its_timing() -> None:
    words = [_word("Kalvik", 100.0, 400.0), _word("Zorblax", 11_984.0, 12_093.0)]

    found = locate_marker(words, "Zorblax")

    assert found.text == "Zorblax"
    assert found.start_ms == 11_984.0
    assert found.end_ms == 12_093.0


def test_matching_is_case_insensitive() -> None:
    """A recogniser may normalise capitalisation, which is not a different word."""
    words = [_word("zorblax", 11_984.0, 12_093.0)]

    assert locate_marker(words, "Zorblax").text == "zorblax"


def test_surrounding_punctuation_is_ignored() -> None:
    """Punctuation is transcription formatting, not a different word."""
    words = [_word("Zorblax.", 11_984.0, 12_093.0), _word("\"Zorblax,\"", 0.0, 1.0)]

    found = locate_marker(words, "Zorblax")

    assert found.start_ms == 11_984.0


def test_a_missing_marker_is_an_error_not_a_zero() -> None:
    words = [_word("Kalvik", 100.0, 400.0)]

    with pytest.raises(MarkerNotFound) as caught:
        locate_marker(words, "Zorblax")

    assert "Zorblax" in str(caught.value)
    assert "Kalvik" in str(caught.value), "the error should show what was actually returned"


def test_an_empty_transcript_is_an_error() -> None:
    with pytest.raises(MarkerNotFound):
        locate_marker([], "Zorblax")


def test_a_fuzzy_match_is_never_substituted() -> None:
    """A near-miss must fail, not resolve to the closest word.

    If this ever passed, the reported offset could be measured against a word the
    fixture never played.
    """
    words = [_word("Zorblaxx", 11_984.0, 12_093.0), _word("Zorbla", 500.0, 700.0)]

    with pytest.raises(MarkerNotFound):
        locate_marker(words, "Zorblax")


def test_a_partial_overlap_does_not_match() -> None:
    words = [_word("Zorblaxily", 0.0, 1.0)]

    with pytest.raises(MarkerNotFound):
        locate_marker(words, "Zorblax")


def test_extra_whitespace_does_not_prevent_a_match() -> None:
    words = [_word("  Zorblax  ", 11_984.0, 12_093.0)]

    assert locate_marker(words, "Zorblax").start_ms == 11_984.0


def test_the_match_rule_is_reported_for_the_record() -> None:
    """The rule travels with the result, so a reader knows what was compared."""
    assert MATCH_RULE == "exact-text-case-and-punctuation-insensitive"


def test_matched_word_reports_logprob_when_present() -> None:
    words = [{"text": "Zorblax", "start": 11_984.0, "end": 12_093.0, "logprob": 0.97}]

    assert locate_marker(words, "Zorblax").logprob == 0.97


def test_matched_word_tolerates_a_missing_logprob() -> None:
    words = [_word("Zorblax", 11_984.0, 12_093.0)]

    assert locate_marker(words, "Zorblax").logprob is None

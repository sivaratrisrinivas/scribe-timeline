"""Locating the measured marker in a returned transcript.

Matching is exact, and a miss is an error rather than a fallback. This is the
single place where a wrong answer would be invisible: a fuzzy match returns a
real word's real timestamp, and the resulting offset looks entirely plausible.

Normalisation is limited to case, surrounding whitespace, and wrapping
punctuation. Those are transcription formatting. A near-miss like `Zorblaxx` is a
*different word* and must not match, or the reported offset could be measured
against a word the fixture never played.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MATCH_RULE = "exact-text-case-and-punctuation-insensitive"
"""Recorded in every run record, so a reader knows what was compared."""

#: Stripped from both ends of a token. Interior characters are left alone, so
#: `Zorblaxx` still fails to match `Zorblax`.
_EDGE_PUNCTUATION = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)


class MarkerNotFound(LookupError):
    """The marker word was not present in the returned transcript.

    Raised rather than degraded. A run without its marker cannot support a
    measurement, and quietly returning a nearby word would corrupt the result.
    """


@dataclass(frozen=True)
class MatchedWord:
    text: str
    start_ms: float
    end_ms: float
    logprob: float | None = None


def _normalise(text: str) -> str:
    return _EDGE_PUNCTUATION.sub("", text).casefold()


def _timing(word: dict[str, object], key: str) -> float:
    """Read a timing field, or refuse the word.

    A word whose text matches but whose timing is missing or non-numeric is not a
    measurement. Substituting zero would invent a timestamp the server never sent
    and it would still look entirely plausible, so the word is skipped and the
    miss is reported as a miss.
    """
    value = word.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"word {word.get('text', '')!r} has no usable {key!r}: {value!r}")
    return float(value)


def locate_marker(words: list[dict[str, object]], marker_text: str) -> MatchedWord:
    """Find `marker_text` in the returned words and return its timing.

    Raises `MarkerNotFound` if it is absent or unusable, listing what was actually
    returned so the failure is diagnosable from the error alone.
    """
    wanted = _normalise(marker_text)
    for word in words:
        text = str(word.get("text", ""))
        if _normalise(text) != wanted:
            continue
        try:
            start_ms = _timing(word, "start")
            end_ms = _timing(word, "end")
        except ValueError:
            continue
        raw_logprob = word.get("logprob")
        return MatchedWord(
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            logprob=float(raw_logprob) if isinstance(raw_logprob, int | float) else None,
        )

    returned = ", ".join(str(word.get("text", "")) for word in words) or "(no words returned)"
    raise MarkerNotFound(
        f"marker {marker_text!r} not found in transcript; received: {returned}"
    )

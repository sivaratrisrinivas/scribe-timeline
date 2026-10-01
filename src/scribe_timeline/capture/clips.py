"""Generating the speech clips the fixtures are built from.

The marker word has to be a word a recogniser will return verbatim, which means it
has to actually be spoken. A synthetic tone proves sample accounting but returns
an empty transcript, so the clips are generated once with TTS and cached as raw
PCM16 in the repository.

Generated once, committed, and reused: the measurement compares the same bytes
across every condition, so regenerating them between runs would quietly change
the experiment. `make fixtures` refreshes them deliberately.

The API key is read from the environment here and nowhere else.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from elevenlabs.client import ElevenLabs

from scribe_timeline.audio.family import EARLIER_CLIP_ID, MARKER_CLIP_ID, MARKER_TEXT
from scribe_timeline.audio.speech import assert_speakable
from scribe_timeline.audio.timeline import Clip

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"

VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # Rachel: a stable, widely available default
MODEL_ID = "eleven_multilingual_v2"
OUTPUT_FORMAT = "pcm_16000"
SAMPLE_RATE = 16_000

#: Each clip is one word plus surrounding silence, so the composer's placement
#: arithmetic is not fighting a clip with long leading or trailing silence in it.
CLIP_SILENCE_MS = 120

#: The word spoken before the marker. Distinct from the marker so exact matching
#: has exactly one candidate to find.
EARLIER_TEXT = "Kalvik"

CLIP_TEXTS = {
    MARKER_CLIP_ID: MARKER_TEXT,
    EARLIER_CLIP_ID: EARLIER_TEXT,
}


class FixtureError(RuntimeError):
    """A fixture clip could not be produced or validated."""


def clip_path(clip_id: str) -> Path:
    return FIXTURE_DIR / f"{clip_id}.pcm"


def _require_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise FixtureError(
            "ELEVENLABS_API_KEY is not set; it is needed once to generate the speech "
            "clips. Everything else in this project runs without it."
        )
    return key


def synthesise_clip(text: str) -> bytes:
    """Render one word to raw PCM16, padded with silence.

    The padding is trimmed to a fixed short window so the clip's own duration says
    nothing about where the word sits. Word onset within the clip is *not* known
    precisely, which is exactly why no reported offset is measured against the
    clip's insertion point.
    """
    client = ElevenLabs(api_key=_require_key())
    # The SDK's `convert` yields chunks rather than returning one buffer.
    chunks = client.text_to_speech.convert(
        voice_id=VOICE_ID,
        model_id=MODEL_ID,
        output_format=OUTPUT_FORMAT,
        text=text,
    )
    audio = b"".join(chunk for chunk in chunks if chunk)
    if not audio:
        raise FixtureError(f"TTS returned no audio for {text!r}")

    silence = b"\x00" * (SAMPLE_RATE * CLIP_SILENCE_MS // 1000 * 2)
    return silence + audio + silence


def generate_clips(*, force: bool = False) -> dict[str, Path]:
    """Write any missing clip to the fixture directory. Returns the paths."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for clip_id, text in CLIP_TEXTS.items():
        path = clip_path(clip_id)
        if path.exists() and not force:
            written[clip_id] = path
            continue
        pcm = synthesise_clip(text)
        assert_speakable(pcm, label=clip_id, sample_rate=SAMPLE_RATE)
        path.write_bytes(pcm)
        written[clip_id] = path
    return written


def load_clips() -> dict[str, Clip]:
    """Load the committed clips, failing loudly if any are missing.

    A missing clip is an error rather than a silent substitution: swapping in a
    different word would change which word the measurement is about.
    """
    clips: dict[str, Clip] = {}
    missing: list[str] = []
    for clip_id in CLIP_TEXTS:
        path = clip_path(clip_id)
        if not path.exists():
            missing.append(clip_id)
            continue
        pcm = path.read_bytes()
        assert_speakable(pcm, label=clip_id, sample_rate=SAMPLE_RATE)
        clips[clip_id] = Clip(id=clip_id, pcm=pcm, sample_rate=SAMPLE_RATE)

    if missing:
        raise FixtureError(
            f"missing fixture clip(s): {', '.join(missing)}. "
            "Run `make fixtures` to generate them."
        )
    return clips


def main() -> int:
    """Generate any missing clip, then report what is on disk."""
    force = "--force" in sys.argv
    try:
        written = generate_clips(force=force)
    except FixtureError as exc:
        print(f"fixture error: {exc}")
        return 2

    for clip_id, path in sorted(written.items()):
        pcm = path.read_bytes()
        seconds = (len(pcm) // 2) / SAMPLE_RATE
        action = "regenerated" if force else "present"
        print(f"{clip_id:<8} {action:<12} {seconds:.2f}s  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

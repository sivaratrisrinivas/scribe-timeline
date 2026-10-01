"""Rebuilding a run record's audio, so the viewer has something to play.

A run record carries no audio. It carries the *layout* -- which clip sat at which
sample -- and that is enough to rebuild the exact bytes that were streamed, because
the clips are committed to the repository. This module does that rebuild.

It exists as its own module because the rebuild is where a viewer's credibility
is won or lost. Every function here composes from the record's own manifest and
verifies the result against the record before returning it. The failure modes are
all the same shape: a plausible wrong picture rather than an error.

* **A clip that no longer matches what was captured.** `make fixtures` regenerates
  speech deliberately, so the committed clips can change after a run was captured.
  Replaying new audio under old timestamps would attribute the difference to the
  server. Refused.
* **A marker the segments do not place.** The manifest's marker positions come from
  the composition, so a disagreement means the record is internally inconsistent.
  Refused rather than rendered, because a mislabelled marker biases every offset
  shown against it.
* **A sample rate the server disagreed with.** Returned word timestamps are on the
  server's sample clock. Composing at the runner's rate instead would misplace every
  word by a factor, which no reader could detect in the result.
* **A clip longer than the sample count it was recorded with.** Refused, so the WAV
  header can never claim more audio than the record describes.

The composition itself is `scribe_timeline.audio.timeline.compose`, the same
function the capture path used. It is not reimplemented here: two implementations
of sample-exact placement are two chances to disagree by one sample.
"""

from __future__ import annotations

import io
import wave
from collections.abc import Mapping

from scribe_timeline.audio.timeline import (
    Clip,
    ComposedTimeline,
    Placement,
    TimelineSpec,
    compose,
)
from scribe_timeline.records import Manifest, RunRecord

__all__ = ["RecordAudioMismatch", "wav_for_record"]


class RecordAudioMismatch(RuntimeError):
    """The committed clips cannot reproduce what a run record says was played.

    Raised rather than rendered around. Each of these means the audio a reader
    would hear is not the audio the record's numbers describe, and every offset
    shown against it would be wrong in a way that looks correct.
    """


def _require_lengths_match(
    run_id: str, manifest: Manifest, clips: Mapping[str, Clip]
) -> None:
    """Every placed clip must be the length the record was captured against.

    Checked per *placement* rather than per clip id, because one clip can be
    placed more than once, and the record's own `sample_count` per segment is what
    the audio at that position is known to have been.
    """
    for segment in manifest.segments:
        clip = clips.get(segment.clip_id)
        if clip is None:
            raise RecordAudioMismatch(
                f"record {run_id!r} placed clip {segment.clip_id!r} at sample "
                f"{segment.start_sample}, but no such clip is available; a missing clip cannot "
                f"be rendered as silence, because silence there reads as a result"
            )
        if clip.sample_count != segment.sample_count:
            raise RecordAudioMismatch(
                f"record {run_id!r} recorded clip {segment.clip_id!r} as "
                f"{segment.sample_count} samples, but the committed clip is "
                f"{clip.sample_count}. The clips were regenerated after this run was captured, "
                f"so playing them against this record's timestamps would blame the server for "
                f"the difference. Re-run `make matrix`, or restore the clips this run used."
            )


def _require_rates_agree(record: RunRecord) -> None:
    """The runner's sample rate and the server's echoed one must be the same.

    The returned word timestamps are on the server's clock. If the two rates
    differ, every word would be placed against audio of a different length, and the
    error would scale with position rather than being a constant offset -- so it
    would not look like a bug in the result.
    """
    manifest_rate = record.manifest.sample_rate
    echoed_rate = record.echoed_config.sample_rate
    if manifest_rate != echoed_rate:
        raise RecordAudioMismatch(
            f"record {record.run_id!r} composed its audio at {manifest_rate} Hz but the server "
            f"reported {echoed_rate} Hz. Its word timestamps are on the server's clock, so "
            f"they cannot be played against this audio."
        )


def _compose_record(record: RunRecord, clips: Mapping[str, Clip]) -> ComposedTimeline:
    """Compose the record's own layout, reporting every failure the same way.

    `compose` raises `ValueError` for the cases it detects itself -- a clip past the
    timeline end, an overlap, two placements claiming one marker text. Those are the
    same class of problem as the checks above: this record's audio cannot be
    rebuilt. They are translated here rather than left to escape, because a caller
    exporting twelve records needs to fail the one that is broken, not abort the
    whole bundle with a traceback from a helper it never asked about.
    """
    placements = tuple(
        Placement(
            clip_id=segment.clip_id,
            start_sample=segment.start_sample,
            marker_text=segment.marker_text,
        )
        for segment in record.manifest.segments
    )
    try:
        return compose(
            TimelineSpec(
                sample_rate=record.manifest.sample_rate,
                total_samples=record.manifest.sample_count,
                placements=placements,
            ),
            clips,
        )
    except ValueError as exc:
        raise RecordAudioMismatch(
            f"record {record.run_id!r} cannot be composed from its own manifest: {exc}"
        ) from exc


def wav_for_record(record: RunRecord, clips: Mapping[str, Clip]) -> bytes:
    """The record's audio as a WAV file, playable by a browser with no decoding step.

    A WAV rather than raw PCM because `<audio src>` cannot play bare PCM16, and
    decoding it in the browser would mean trusting JavaScript's arithmetic on the
    one thing this project is measuring.

    Raises `RecordAudioMismatch` if the committed clips cannot reproduce the record,
    and nothing else: every way this can go wrong means the audio a reader would hear
    is not the audio the record's numbers describe.
    """
    _require_rates_agree(record)
    _require_lengths_match(record.run_id, record.manifest, clips)

    composed = _compose_record(record, clips)

    # `compose` bounds each clip by its own length and derives markers from the
    # placements. Comparing both against the record closes the remaining gap: a
    # marker the segments do not place, or audio running past the recorded end, would
    # otherwise be exported as a WAV that looks playable and is not this run's audio.
    realised = dict(composed.markers)
    if realised != dict(record.manifest.markers):
        raise RecordAudioMismatch(
            f"record {record.run_id!r} records markers at {dict(record.manifest.markers)}, but "
            f"composing its own audio places them at {realised}. Every offset measured against "
            f"the marker would be wrong by the difference."
        )
    if composed.sample_count != record.manifest.sample_count:
        raise RecordAudioMismatch(
            f"record {record.run_id!r} describes {record.manifest.sample_count} samples but the "
            f"rebuilt audio holds {composed.sample_count}."
        )

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(record.manifest.sample_rate)
        handle.writeframes(composed.pcm)
    return buffer.getvalue()

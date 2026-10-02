"""The viewer's audio must be exactly the audio its run record describes.

The record carries no audio. It carries the *layout* -- which clip sat at which
sample -- so a viewer that wants to play something has to rebuild it. That makes
this module the only thing standing between a reader and a plausible wrong
picture: a mis-rebuilt timeline still plays, still lines up with the words, and
would quietly invalidate every offset shown against it.

So every way the rebuild can drift from the record fails loudly instead:

* a clip that no longer matches the length the record was captured against, which
  happens if `make fixtures` regenerates a clip after capture;
* a marker the record places somewhere its own segments do not;
* a sample rate the server and the runner disagree about, which would place every
  word against audio of a different length.
"""

from __future__ import annotations

import io
import wave

import pytest

from pcm_fixtures import SAMPLE_RATE, ramp_pcm, sample_at
from scribe_timeline.analysis.matching import MATCH_RULE
from scribe_timeline.audio.timeline import Clip
from scribe_timeline.records import EchoedSessionConfig, Manifest, RunRecord, Segment
from scribe_timeline.viewer.audio import RecordAudioMismatch, wav_for_record

MARKER = "Zorblax"
EARLIER = "Kalvik"


def clip(clip_id: str, samples: int) -> Clip:
    return Clip(id=clip_id, pcm=ramp_pcm(samples), sample_rate=SAMPLE_RATE)


def record(
    *,
    segments: tuple[Segment, ...],
    markers: dict[str, int],
    sample_count: int = SAMPLE_RATE * 2,
    manifest_rate: int = SAMPLE_RATE,
    echoed_rate: int = SAMPLE_RATE,
) -> RunRecord:
    return RunRecord(
        schema_version=1,
        run_id="2026-10-02T00-00-00Z__vad_1__rep0",
        condition_id="vad_1",
        commit_strategy="vad",
        repeat_index=0,
        manifest=Manifest(
            condition_id="vad_1",
            sample_rate=manifest_rate,
            sample_count=sample_count,
            markers=markers,
            segments=segments,
        ),
        echoed_config=EchoedSessionConfig(
            model_id="scribe_v2_realtime",
            language_code="en",
            sample_rate=echoed_rate,
            include_timestamps=True,
        ),
        match_rule=MATCH_RULE,
        source_timestamp_unit="seconds",
    )


def marker_segment() -> Segment:
    return Segment(clip_id="marker", start_sample=8000, sample_count=1600, marker_text=MARKER)


def frames(wav: bytes) -> bytes:
    with wave.open(io.BytesIO(wav), "rb") as handle:
        assert handle.getsampwidth() == 2, "audio must be 16-bit, or browsers resample it"
        assert handle.getnchannels() == 1
        assert handle.getframerate() == SAMPLE_RATE
        return handle.readframes(handle.getnframes())


def test_the_audio_is_the_record_s_own_layout() -> None:
    marker = clip("marker", 1600)

    wav = wav_for_record(record(segments=(marker_segment(),), markers={MARKER: 8000}), {
        "marker": marker
    })

    pcm = frames(wav)
    # Silence before the clip, the clip exactly where the record says, silence after.
    assert sample_at(pcm, 7999) == 0
    assert sample_at(pcm, 8000) == sample_at(marker.pcm, 0)
    assert sample_at(pcm, 8000 + 1599) == sample_at(marker.pcm, 1599)
    assert sample_at(pcm, 8000 + 1600) == 0


def test_a_preceding_segment_lands_at_its_declared_sample() -> None:
    earlier = clip("earlier", 1200)
    marker = clip("marker", 1600)
    segments = (
        Segment(clip_id="earlier", start_sample=0, sample_count=1200),
        marker_segment(),
    )

    wav = wav_for_record(record(segments=segments, markers={MARKER: 8000}), {
        "earlier": earlier,
        "marker": marker,
    })

    pcm = frames(wav)
    assert sample_at(pcm, 0) == sample_at(earlier.pcm, 0)
    assert sample_at(pcm, 1199) == sample_at(earlier.pcm, 1199)
    assert sample_at(pcm, 1200) == 0


def test_the_wav_is_exactly_as_long_as_the_record_s_sample_count() -> None:
    wav = wav_for_record(
        record(segments=(marker_segment(),), markers={MARKER: 8000}, sample_count=12_345),
        {"marker": clip("marker", 1600)},
    )

    with wave.open(io.BytesIO(wav), "rb") as handle:
        assert handle.getnframes() == 12_345


def test_a_clip_that_no_longer_matches_the_record_is_refused() -> None:
    # The record was captured against a 1600-sample marker. If the committed clip
    # is later regenerated shorter, the rebuild would place a *different* stretch
    # of audio under the marker's timestamps, and the viewer would still play.
    record_ = record(segments=(marker_segment(),), markers={MARKER: 8000})

    with pytest.raises(RecordAudioMismatch, match="1600"):
        wav_for_record(record_, {"marker": clip("marker", 900)})


def test_a_missing_clip_is_refused_rather_than_rendered_as_silence() -> None:
    # Silence in the marker's place would render as a real result: a timeline with
    # a hole exactly where the measured word should be.
    with pytest.raises(RecordAudioMismatch, match="marker"):
        wav_for_record(record(segments=(marker_segment(),), markers={MARKER: 8000}), {})


def test_a_marker_its_segments_do_not_place_is_refused() -> None:
    # A record claiming the marker is at 9000 while its segment starts at 8000 is
    # internally inconsistent. Rendering the segment and labelling it 9000 would
    # put a 1000-sample error into every offset shown against the marker.
    with pytest.raises(RecordAudioMismatch, match="9000"):
        wav_for_record(
            record(segments=(marker_segment(),), markers={MARKER: 9000}),
            {"marker": clip("marker", 1600)},
        )


@pytest.mark.parametrize(
    ("expected", "sample_count", "segments", "markers", "clips"),
    [
        (
            "overlaps",
            SAMPLE_RATE * 2,
            (
                Segment(clip_id="marker", start_sample=8000, sample_count=1600, marker_text=MARKER),
                Segment(clip_id="marker", start_sample=9000, sample_count=1600),
            ),
            {MARKER: 8000},
            {"marker": clip("marker", 1600)},
        ),
        (
            # The clip starts at 29_000 and is 1600 samples long, so it ends at
            # 30_600 -- past a 30_000-sample timeline.
            "past the timeline end",
            30_000,
            (
                Segment(
                    clip_id="marker",
                    start_sample=29_000,
                    sample_count=1600,
                    marker_text=MARKER,
                ),
            ),
            {MARKER: 29_000},
            {"marker": clip("marker", 1600)},
        ),
        (
            "but the timeline is",
            SAMPLE_RATE * 2,
            (marker_segment(),),
            {MARKER: 8000},
            {"marker": Clip(id="marker", pcm=ramp_pcm(1600), sample_rate=8_000)},
        ),
    ],
)
def test_a_failure_the_composer_finds_is_reported_like_any_other(
    expected: str,
    sample_count: int,
    segments: tuple[Segment, ...],
    markers: dict[str, int],
    clips: dict[str, Clip],
) -> None:
    """Every way this can fail arrives as `RecordAudioMismatch`, never a `ValueError`.

    The composer's own checks -- overlap, bounds, sample-rate disagreement -- are the
    same class of problem as the ones checked here: this record's audio cannot be
    rebuilt. An exporter catching one kind and not the other would abort the whole
    bundle with a traceback over a single bad record.
    """
    with pytest.raises(RecordAudioMismatch, match=expected):
        wav_for_record(
            record(segments=segments, markers=markers, sample_count=sample_count), clips
        )


def test_a_sample_rate_the_server_disagrees_with_is_refused() -> None:
    # The returned word timestamps are on the server's sample clock. Playing them
    # against audio composed at a different rate would misplace every word by a
    # factor rather than by an offset, which no reader could detect.
    with pytest.raises(RecordAudioMismatch, match="24000"):
        wav_for_record(
            record(
                segments=(marker_segment(),),
                markers={MARKER: 8000},
                manifest_rate=SAMPLE_RATE,
                echoed_rate=24_000,
            ),
            {"marker": clip("marker", 1600)},
        )


def test_a_record_whose_audio_would_run_past_its_own_sample_count_is_refused() -> None:
    # `compose` checks the *clip's* length against the timeline end, not the length
    # the record recorded for it. A longer clip would silently overwrite the sample
    # after the timeline, so the WAV header would claim more audio than the record
    # describes.
    with pytest.raises(RecordAudioMismatch):
        wav_for_record(
            record(
                segments=(marker_segment(),),
                markers={MARKER: 8000},
                sample_count=8000 + 1600,
            ),
            {"marker": clip("marker", 3200)},
        )


def test_a_marker_with_no_segment_placing_it_is_refused() -> None:
    # A record can name a marker and place no audio for it: `Manifest.segments`
    # defaults to empty while `markers` requires at least one entry. There is
    # nothing to play at that position, so the marker cannot be shown -- and
    # rendering the silence there would put a word where the audio holds none.
    with pytest.raises(RecordAudioMismatch, match=MARKER):
        wav_for_record(record(segments=(), markers={MARKER: 0}, sample_count=1000), {})

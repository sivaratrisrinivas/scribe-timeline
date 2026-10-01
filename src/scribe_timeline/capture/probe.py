"""Run one condition end to end and report what came back.

    make capture

The smallest honest question this project can ask the API: stream a single
timeline, then print what the server returned for the measured marker. One
condition, no matrix, no claim about drift -- that is the next ticket.

Deliberately chatty. The first live run should show its own working: the echoed
configuration next to what was sent, the marker's position on the audio sample
clock, and the timestamp the server assigned it. A reader should be able to see
that those are two different numbers measured on two different clocks.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from scribe_timeline.analysis.matching import MarkerNotFound, locate_marker
from scribe_timeline.audio.family import MARKER_TEXT, Condition, build_vad_family
from scribe_timeline.audio.timeline import Clip
from scribe_timeline.capture.clips import FixtureError, load_clips
from scribe_timeline.capture.runner import (
    LANGUAGE_CODE,
    MODEL_ID,
    VAD_OPTIONS,
    CaptureError,
    new_run_id,
    run_condition,
)
from scribe_timeline.records import RunRecord

SAMPLE_RATE = 16_000
TOTAL_SECONDS = 18
SLOT_SECONDS = 6
MARKER_SECOND = 12

#: Trailing silence must exceed `vad_silence_threshold_secs` or the server never
#: commits on its own. The marker clip runs ~1.2s, so a 12s marker ends at ~13.2s
#: and needs silence out to at least 14.7s. 18s leaves room without being wasteful.
MIN_TRAILING_SILENCE_SECONDS = 3.0

RUNS_DIR = Path("runs")


def build_probe_condition(clips: dict[str, Clip]) -> tuple[Condition, tuple[Condition, ...]]:
    """The anchor condition: one timeline, no preceding speech segments.

    Zero prior VAD commits makes this the baseline every other condition is
    measured against, so it has to run first and be the simplest possible thing.
    """
    family = build_vad_family(
        clips=clips,
        sample_rate=SAMPLE_RATE,
        total_samples=SAMPLE_RATE * TOTAL_SECONDS,
        earlier_starts=(0, SAMPLE_RATE * SLOT_SECONDS),
        final_marker_start=SAMPLE_RATE * MARKER_SECOND,
    )
    return family[0], family


def report(record: RunRecord) -> None:
    manifest = record.manifest
    marker_sample = manifest.markers[MARKER_TEXT]
    marker_expected_ms = marker_sample * 1000 / manifest.sample_rate

    print(f"run              {record.run_id}")
    print(f"condition        {record.condition_id} (prior segments: 0)")
    print(f"commit strategy  {record.commit_strategy}")
    print()
    print("requested")
    print(f"  model          {MODEL_ID}")
    print(f"  language       {LANGUAGE_CODE}")
    print("  include ts     true")
    for key, value in VAD_OPTIONS.items():
        print(f"  {key:<28} {value}")
    print()
    echoed = record.echoed_config
    print("echoed by the server")
    print(f"  model          {echoed.model_id}")
    print(f"  language       {echoed.language_code}")
    print(f"  sample rate    {echoed.sample_rate}")
    print(f"  commit         {echoed.commit_strategy}")
    for key in VAD_OPTIONS:
        print(f"  {key:<28} {getattr(echoed, key)}")
    print()

    drift = [
        f"{key}: requested {value} vs echoed {getattr(echoed, key)}"
        for key, value in VAD_OPTIONS.items()
        if getattr(echoed, key) is not None and getattr(echoed, key) != value
    ]
    if drift:
        print("  the server echoed different VAD values than were sent:")
        for line in drift:
            print(f"    {line}")
        print()

    print(f"audio            {manifest.sample_count} samples "
          f"({manifest.sample_count / manifest.sample_rate:.1f}s at {manifest.sample_rate} Hz)")
    print(f"marker word      {MARKER_TEXT!r} at sample {marker_sample}")
    print(f"  on audio clock {marker_expected_ms:.1f} ms  (where the clip starts)")
    print()

    try:
        matched = locate_marker(
            [w.model_dump() | {"start": w.start_ms, "end": w.end_ms} for w in record.words],
            MARKER_TEXT,
        )
    except MarkerNotFound as exc:
        print(f"MARKER NOT FOUND  {exc}")
        print()
        print("This run supports no measurement. Words returned:")
        for word in record.words:
            print(f"  {word.text!r} {word.start_ms:.1f}-{word.end_ms:.1f} ms")
        return

    print(f"marker returned  {matched.text!r}")
    print(f"  start          {matched.start_ms:.1f} ms  (server, audio sample clock)")
    print(f"  end            {matched.end_ms:.1f} ms")
    if matched.logprob is not None:
        print(f"  logprob        {matched.logprob:.3f}")
    print()
    print(f"  clip starts at {marker_expected_ms:.1f} ms; the server says the word starts at "
          f"{matched.start_ms:.1f} ms")
    print("  (that gap is NOT a measured offset -- see below)")
    print()
    print(f"timestamp unit  the API returned {record.source_timestamp_unit}; "
          f"converted to ms on ingest")
    print()
    print(f"match rule       {record.match_rule}")
    print(f"events captured  {len(record.events)}")
    for event in record.events:
        summary = str(event.payload.get("text", ""))[:48]
        print(f"  {event.received_at_ms:9.1f} ms  {event.type:<38} {summary}")
    print()
    print("This is the anchor condition only. No drift claim is made or implied:")
    print("a measured offset requires comparing the same word across conditions.")


async def _main() -> int:
    try:
        clips = load_clips()
    except FixtureError as exc:
        print(f"fixture error: {exc}", file=sys.stderr)
        return 2

    for clip_id, clip in clips.items():
        print(f"clip {clip_id:<8} {clip.sample_count} samples "
              f"({clip.sample_count / clip.sample_rate:.2f}s)")

    condition, family = build_probe_condition(clips)
    print(f"family           {[c.id for c in family]}")
    print()

    try:
        record = await run_condition(
            condition=condition,
            clips=clips,
            marker_text=MARKER_TEXT,
            run_id=new_run_id(condition.id, 0),
            repeat_index=0,
        )
    except CaptureError as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 1

    report(record)

    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"{record.run_id}.json"
    path.write_text(record.model_dump_json(indent=2))
    print(f"saved            {path}")
    return 0


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())

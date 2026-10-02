"""Exporting the viewer's bundle must fail one record, not the whole bundle.

The bundle is what a reader opens instead of re-running the capture. Two properties
matter more than the export working:

* **One unreplayable record must not cost the reader the others.** Every run is
  exported independently; a failure is reported on stderr and marked in the index
  rather than raised. A half-built bundle a reader cannot open is worse than a
  complete one that lists a record with no audio.
* **The served run record must be the file the comparison read.** It is copied
  verbatim, not re-serialised, so a figure in the README can be checked against the
  bytes the site served.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import wave
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from pcm_fixtures import SAMPLE_RATE, ramp_pcm
from scribe_timeline.analysis.compare import compare_runs
from scribe_timeline.analysis.matching import MATCH_RULE
from scribe_timeline.audio.timeline import Clip
from scribe_timeline.capture.completion import TIMESTAMPED_EVENT
from scribe_timeline.records import (
    EchoedSessionConfig,
    Manifest,
    RawEvent,
    RunRecord,
    Segment,
    WordTiming,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKER = "Zorblax"


def _load_export_module() -> ModuleType:
    """Import `scripts/export_viewer.py`, which is a script rather than a package member.

    Loaded by path so the exporter keeps working as `python3 scripts/export_viewer.py`
    while still being testable. `scripts/` is on `mypy`'s file list, so it is typed
    like the rest of the tree.

    Returned as `ModuleType`, so the tests below are type-checked against a plain
    module: the attributes they monkeypatch and call are resolved at runtime, and
    annotating them would mean restating the exporter's own signatures here.
    """
    spec = importlib.util.spec_from_file_location(
        "export_viewer", REPO_ROOT / "scripts" / "export_viewer.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


export_viewer = _load_export_module()


def clip(clip_id: str, samples: int) -> Clip:
    return Clip(id=clip_id, pcm=ramp_pcm(samples), sample_rate=SAMPLE_RATE)


def make_record(
    run_id: str,
    *,
    clip_id: str = "marker",
    condition_id: str = "vad_1",
    prior_segments: int = 0,
    commits: tuple[tuple[dict[str, object], ...], ...] = (),
) -> RunRecord:
    return RunRecord(
        schema_version=1,
        run_id=run_id,
        condition_id=condition_id,
        commit_strategy="vad",
        repeat_index=0,
        manifest=Manifest(
            condition_id=condition_id,
            sample_rate=SAMPLE_RATE,
            sample_count=SAMPLE_RATE * 2,
            markers={MARKER: 8000},
            segments=(
                *(
                    Segment(clip_id="earlier", start_sample=index * 4000, sample_count=1600)
                    for index in range(prior_segments)
                ),
                Segment(clip_id=clip_id, start_sample=8000, sample_count=1600, marker_text=MARKER),
            ),
        ),
        echoed_config=EchoedSessionConfig(
            model_id="scribe_v2_realtime",
            language_code="en",
            sample_rate=SAMPLE_RATE,
            include_timestamps=True,
        ),
        events=tuple(
            RawEvent(
                type=TIMESTAMPED_EVENT,
                received_at_ms=float(index),
                payload={"words": list(words)},
            )
            for index, words in enumerate(commits)
        ),
        # Pooled from the commits and converted, the way the recorder does it, so
        # the record's own words agree with the events beside them.
        words=tuple(
            WordTiming(
                text=str(word["text"]),
                start_ms=float(word["start"]) * 1000.0,  # type: ignore[arg-type]
                end_ms=float(word["end"]) * 1000.0,  # type: ignore[arg-type]
            )
            for payload in commits
            for word in payload
        ),
        # Both required by the model, so the fixture states them. `seconds` because
        # the events above hold seconds, and the test asserting the export converts
        # them is the point of the module.
        match_rule=MATCH_RULE,
        source_timestamp_unit="seconds",
    )


def commit(*words: tuple[str, float, float]) -> tuple[dict[str, object], ...]:
    """One timestamped commit's words, in the seconds the API returns."""
    return tuple(
        {"text": text, "start": start, "end": end} for text, start, end in words
    )


def write_record(
    directory: Path,
    record: RunRecord,
    run_id: str | None = None,
    *,
    source_timestamp_unit: str | None = None,
) -> Path:
    """Write a record the way a runner does: one JSON file, indented.

    `run_id` and `source_timestamp_unit` override fields on the serialised
    document, bypassing the models -- so a test can produce the corrupt record the
    exporter is supposed to refuse, which a well-formed model would not let it
    build.
    """
    document = json.loads(record.model_dump_json())
    if run_id is not None:
        document["run_id"] = run_id
    if source_timestamp_unit is not None:
        document["source_timestamp_unit"] = source_timestamp_unit
    path = directory / f"{run_id or record.run_id}.json"
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path


@pytest.fixture
def public(tmp_path: Path) -> Path:
    return tmp_path / "public"


def test_the_run_record_is_served_byte_for_byte(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The site must serve the same bytes the comparison read, not a re-serialisation."""
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    source = write_record(tmp_path, make_record("run-a"))

    export_viewer.export([source], public_dir=public)

    served = (public / "runs" / "run-a.json").read_text()
    assert served == source.read_text()


def wav_frames(path: Path) -> int:
    with wave.open(io.BytesIO(path.read_bytes()), "rb") as handle:
        return handle.getnframes()


def test_each_run_gets_its_own_wav(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    paths = [write_record(tmp_path, make_record(name)) for name in ("run-a", "run-b")]

    export_viewer.export(paths, public_dir=public)

    for name in ("run-a", "run-b"):
        assert wav_frames(public / "audio" / f"{name}.wav") == SAMPLE_RATE * 2


def test_the_index_lists_every_run_with_its_strategy(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A VAD run listed without its strategy would be indistinguishable from the
    # manual control, which is the one thing the comparison depends on.
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    path = write_record(tmp_path, make_record("run-a"))

    export_viewer.export([path], public_dir=public)

    index = json.loads((public / "runs.json").read_text())
    assert index["runs"] == [
        {
            "run_id": "run-a",
            "condition_id": "vad_1",
            "commit_strategy": "vad",
            "repeat_index": 0,
            "audio_path": "audio/run-a.wav",
            "has_audio": True,
            "commits": [],
        }
    ]


# --- Commit boundaries, in the unit the viewer draws in -----------------------


def test_the_index_carries_each_run_s_commit_boundaries_in_milliseconds(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The viewer draws boundaries on the audio clock and converts nothing itself.

    Putting the boundaries in the index rather than leaving the viewer to read raw
    events is what keeps that true: the events hold seconds in a field named
    `start`, and a viewer treating 0.22 as 0.22 ms would scale the whole track.
    """
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    path = write_record(
        tmp_path,
        make_record(
            "run-a",
            commits=(
                commit(("Kolvig.", 0.22, 0.64)),
                commit(("Zorblax.", 0.82, 0.98)),
            ),
        ),
    )

    entries = export_viewer.export([path], public_dir=public)

    assert entries[0]["commits"] == [
        {"commit_index": 1, "word_count": 1, "first_word_ms": 220.0, "last_word_ms": 640.0},
        {"commit_index": 2, "word_count": 1, "first_word_ms": 820.0, "last_word_ms": 980.0},
    ]


def test_a_commit_that_returned_no_words_is_listed_rather_than_dropped(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The commit count is cross-checked against the condition, so a bundle that
    # quietly renumbered its commits would disagree with the report beside it.
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    path = write_record(
        tmp_path,
        make_record("run-a", commits=(commit(("Zorblax.", 0.82, 0.98)), commit())),
    )

    entries = export_viewer.export([path], public_dir=public)

    assert entries[0]["commits"] == [
        {"commit_index": 1, "word_count": 1, "first_word_ms": 820.0, "last_word_ms": 980.0},
        {"commit_index": 2, "word_count": 0, "first_word_ms": None, "last_word_ms": None},
    ]


def test_an_unconvertible_timestamp_unit_fails_the_export_rather_than_exporting_wrong_boundaries(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    record = make_record("run-a", commits=(commit(("Zorblax.", 0.82, 0.98)),))
    path = write_record(
        tmp_path,
        record.model_copy(update={"source_timestamp_unit": "fortnights"}),
    )

    with pytest.raises(SystemExit, match="fortnights"):
        export_viewer.export([path], public_dir=public)

    assert not (public / "runs.json").exists()


def test_one_unreplayable_record_does_not_cost_the_reader_the_others(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record naming a clip that is not available is listed without audio; the rest export.

    A regenerated-clip mismatch is global -- every record was captured against the same
    clips -- so it fails every record at once, which is the correct answer. This is the
    per-record case: one run referencing something absent, while its neighbours are
    perfectly replayable. Rendering silence in its place would read as a result, so it
    is listed without a player instead.
    """
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    paths = [
        write_record(tmp_path, make_record("broken", clip_id="regenerated")),
        write_record(tmp_path, make_record("fine")),
    ]

    entries = export_viewer.export(paths, public_dir=public)

    by_run = {entry["run_id"]: entry for entry in entries}
    assert by_run["broken"]["has_audio"] is False
    assert by_run["fine"]["has_audio"] is True
    assert not (public / "audio" / "broken.wav").exists()
    assert (public / "audio" / "fine.wav").exists()
    # The broken record is still served, so a reader can read what it says.
    assert (public / "runs" / "broken.json").exists()


def test_a_run_that_lost_its_audio_leaves_no_wav_behind_from_a__earlier_export(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that stops being replayable must not keep serving the audio it had.

    The inverse of the stale-file case, and the one a reader would actually be
    misled by: `has_audio: false` says "there is nothing to play", the page honours
    it and offers no player -- and a WAV left over from a previous export would sit
    there anyway, readable by anyone who guessed the URL, describing audio for a
    record that no longer matches it.
    """
    clips = {"marker": clip("marker", 1600)}
    monkeypatch.setattr(export_viewer, "load_clips", lambda: clips)
    good = write_record(tmp_path, make_record("run-a"))
    export_viewer.export([good], public_dir=public)
    assert (public / "audio" / "run-a.wav").exists()

    # Same record, but the clip it names is no longer the one it was captured with.
    broken = write_record(tmp_path, make_record("run-a", clip_id="regenerated"))
    entries = export_viewer.export([broken], public_dir=public)

    assert entries[0]["has_audio"] is False
    assert not (public / "audio" / "run-a.wav").exists()


def test_a_rerun_replaces_its_own_audio_rather_than_leaving_the_old_bytes(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replaced file is gone, and the index cannot disagree with the directory.

    Trivially true given the clear, so asserted together with a WAV that is present
    and correct for the record that *was* rebuilt -- otherwise "nothing is there"
    would pass just as well for a bundle that was never written at all.
    """
    clips = {"marker": clip("marker", 1600)}
    monkeypatch.setattr(export_viewer, "load_clips", lambda: clips)
    paths = [
        write_record(tmp_path, make_record("run-a")),
        write_record(tmp_path, make_record("run-b", clip_id="regenerated")),
    ]

    entries = export_viewer.export(paths, public_dir=public)

    assert {entry["run_id"]: entry["has_audio"] for entry in entries} == {
        "run-a": True,
        "run-b": False,
    }
    on_disk = {path.name for path in (public / "audio").glob("*.wav")}
    # Exactly the runs the index says have audio, and nothing else -- which is the
    # property a published bundle rests on, and what a build would otherwise carry
    # forward from an earlier export.
    assert on_disk == {f"{entry['run_id']}.wav" for entry in entries if entry["has_audio"]}


def test_regenerated_clips_fail_every_record_rather_than_misreporting_one(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clips are shared, so a change to them is not one record's problem.

    Playing new audio under old timestamps would blame the server for a difference
    this project introduced. Failing loudly on every record is the honest outcome,
    even though it means an empty bundle until the clips are restored.
    """
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 900)})
    paths = [write_record(tmp_path, make_record(name)) for name in ("run-a", "run-b")]

    entries = export_viewer.export(paths, public_dir=public)

    assert [entry["has_audio"] for entry in entries] == [False, False]
    assert not (public / "audio" / "run-a.wav").exists()
    # Nothing was written for either run, so the bundle cannot serve a WAV that is
    # not the audio its record was captured against.
    assert not (public / "audio" / "run-b.wav").exists()


def test_the_failure_is_reported_rather_than_only_recorded(
    public: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    path = write_record(tmp_path, make_record("broken", clip_id="regenerated"))

    export_viewer.export([path], public_dir=public)

    # The index says a record has no audio; stderr says why. A silent degradation
    # would leave a reader wondering what went wrong.
    assert "no audio" in capsys.readouterr().err


def test_an_empty_bundle_is_refused(public: Path) -> None:
    # An index listing nothing renders as a viewer with nothing in it, which looks
    # like a broken page rather than an empty evidence set.
    with pytest.raises(SystemExit, match="no run records"):
        export_viewer.export([], public_dir=public)


def test_a_run_dropped_from_the_evidence_leaves_nothing_behind(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-exporting a smaller set must not leave the larger set's files in place.

    The bundle a reader is served is whatever is in the directory, and the index is
    what the page trusts: it is authoritative about which runs exist. A record left
    from an earlier, larger export is therefore invisible to the page and still
    published to anyone reading the bytes -- evidence the README does not mention
    and nothing on the site accounts for.
    """
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    first = [write_record(tmp_path, make_record(name)) for name in ("run-a", "run-b")]
    export_viewer.export(first, public_dir=public)
    assert (public / "runs" / "run-b.json").exists()

    export_viewer.export([first[0]], public_dir=public)

    assert (public / "runs" / "run-a.json").exists()
    assert not (public / "runs" / "run-b.json").exists()
    assert not (public / "audio" / "run-b.wav").exists()
    index = json.loads((public / "runs.json").read_text())
    assert [entry["run_id"] for entry in index["runs"]] == ["run-a"]


@pytest.mark.parametrize(
    "when", ["while-moving-aside", "while-creating", "while-writing"]
)
def test_an_interrupted_export_leaves_the_previous_bundle_intact(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    """A crash at any point in the swap leaves a working bundle.

    The export renames the old directories aside, creates new ones, and fills them.
    Each of those steps is interruptible, and the failures worth guarding are the
    ones that *strand* state rather than raise cleanly:

    * interrupted while moving the old directories aside leaves some of them renamed
      and some not -- a bundle with half of itself gone, and no handler to put it
      back, because the interruption happened before there was one;
    * interrupted while creating leaves the old directories renamed away and none in
      their place, so the bundle is simply absent;
    * interrupted while writing leaves a half-written new bundle sitting over the old
      index, which then describes files that were never finished.

    All three must land in the same place: what the reader had, and nothing else. The
    last point matters as much as the first -- a renamed copy left inside `public/`
    would be copied into the published output by the next build, since Vite copies
    that directory wholesale.
    """
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    good = write_record(tmp_path, make_record("run-a"))
    export_viewer.export([good], public_dir=public)

    # The real implementations, captured before patching: the stubs below have to
    # reach the originals, and calling the patched attribute would recurse.
    real_rename, real_mkdir, real_write_text = Path.rename, Path.mkdir, Path.write_text

    # One counter per operation. Keyed on the operation rather than a single running
    # total so each interruption point reads as "the Nth rename" or "the Nth mkdir",
    # which stays meaningful if the export grows a third directory.
    counts = {"rename": 0, "mkdir": 0}
    #
    # Both points are chosen to land *after* some state has changed, because an
    # interruption before the first one is trivially harmless and would pass against
    # a rollback that did nothing at all:
    #
    # * `while-moving-aside` fires on the second rename, so the first directory has
    #   already been moved out of the way and only the rollback can put it back;
    # * `while-creating` fires on the second mkdir, so both old directories are
    #   displaced and one of the new ones exists.
    fires_on = {"while-moving-aside": ("rename", 2), "while-creating": ("mkdir", 2)}

    def counting_rename(self: Path, target: object) -> Path:
        counts["rename"] += 1
        if fires_on.get(when) == ("rename", counts["rename"]):
            raise KeyboardInterrupt
        return real_rename(self, target)  # type: ignore[arg-type]

    def counting_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        counts["mkdir"] += 1
        if fires_on.get(when) == ("mkdir", counts["mkdir"]):
            raise KeyboardInterrupt
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    def counting_write_text(self: Path, *args: object, **kwargs: object) -> int:
        if when == "while-writing" and self.parent == public / "runs":
            raise KeyboardInterrupt
        return real_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "rename", counting_rename)
    monkeypatch.setattr(Path, "mkdir", counting_mkdir)
    monkeypatch.setattr(Path, "write_text", counting_write_text)

    with pytest.raises(KeyboardInterrupt):
        export_viewer.export([good], public_dir=public)

    monkeypatch.undo()

    index = json.loads((public / "runs.json").read_text())
    assert [entry["run_id"] for entry in index["runs"]] == ["run-a"]
    assert (public / "runs" / "run-a.json").exists()
    assert (public / "audio" / "run-a.wav").exists()
    assert sorted(path.name for path in public.iterdir()) == [
        "audio",
        "comparison.json",
        "runs",
        "runs.json",
    ], "the interrupted export left something behind inside public/"


def _write_unreadable(path: Path) -> None:
    path.write_text("{ not json")


def _write_unconvertible(path: Path) -> None:
    """A record whose timestamps are in a unit this project cannot convert.

    Written past the model on purpose: `source_timestamp_unit` is a free string, so
    pydantic will hold it, and the refusal has to come from the conversion rather
    than from validation. That is the whole point -- a unit nobody can convert is a
    corrupt record, and the boundaries it would contribute are wrong by a factor that
    still looks like a timeline.
    """
    write_record(path.parent, make_record("broken"), source_timestamp_unit="fortnights")


#: A way to write one corrupt record. Two, because the exporter can refuse at two
#: different depths and only the later one is reached after the bundle is disturbed.
CORRUPTIONS: tuple[Callable[[Path], None], ...] = (_write_unreadable, _write_unconvertible)


@pytest.mark.parametrize("corruption", CORRUPTIONS, ids=lambda fn: fn.__name__)
def test_a_refused_export_leaves_the_working_bundle_intact(
    public: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: Callable[[Path], None],
) -> None:
    """Whatever refuses, the previous bundle survives it.

    The export clears its directories so nothing stale is published, and a refusal
    part way through would leave the old index pointing at files that had just been
    deleted: a bundle that was working, replaced by one that is broken, over a record
    the reader never asked about. So the pass that can refuse has to finish before
    anything is cleared.

    Two refusals, because they happen at different depths. An unreadable record fails
    while the records are being read; an unconvertible timestamp unit fails later,
    while the commits are being derived -- and a fix that only guarded the first
    would leave the second clearing the bundle on its way to the same outcome.
    """
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    good = write_record(tmp_path, make_record("run-a"))
    export_viewer.export([good], public_dir=public)

    broken = tmp_path / "broken.json"
    corruption(broken)
    with pytest.raises(SystemExit, match=re.escape(broken.name)):
        export_viewer.export([good, broken], public_dir=public)

    # The bundle a reader had is still a working one: the index, the record, and the
    # audio it names.
    index = json.loads((public / "runs.json").read_text())
    assert [entry["run_id"] for entry in index["runs"]] == ["run-a"]
    assert (public / "runs" / "run-a.json").exists()
    assert (public / "audio" / "run-a.wav").exists()


# --- The comparison travels with the bundle -----------------------------------


def test_the_comparison_is_exported_alongside_the_records_it_was_built_from(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The viewer must not re-derive the measurement.

    `analysis.compare` is the only place the deltas exist, and reimplementing its
    arithmetic in the viewer would be two calculations free to disagree by one
    quantisation step. So the report is exported, not recomputed.
    """
    monkeypatch.setattr(
        export_viewer,
        "load_clips",
        lambda: {"marker": clip("marker", 1600), "earlier": clip("earlier", 1600)},
    )
    paths = [
        write_record(
            tmp_path,
            make_record(
                "run-anchor",
                condition_id="vad_0",
                commits=(commit((MARKER + ".", 0.82, 0.98)),),
            ),
        ),
        write_record(
            tmp_path,
            make_record(
                "run-drift",
                condition_id="vad_1",
                prior_segments=1,
                commits=(
                    commit(("Kolvig.", 0.22, 0.64)),
                    commit((MARKER + ".", 0.92, 1.08)),
                ),
            ),
        ),
    ]

    export_viewer.export(paths, public_dir=public)

    exported = json.loads((public / "comparison.json").read_text())
    records = [RunRecord.model_validate_json(path.read_text()) for path in paths]
    assert exported["report"] == compare_runs(records, marker_text=MARKER).to_dict()


def test_the_comparison_is_built_from_the_records_in_this_bundle(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exporting a subset must describe that subset.

    Otherwise a bundle built from six of the twelve runs would quote the deltas of
    all twelve, and a reader checking them against the runs beside them would find
    rows for conditions the bundle does not contain.
    """
    monkeypatch.setattr(export_viewer, "load_clips", lambda: {"marker": clip("marker", 1600)})
    path = write_record(
        tmp_path,
        make_record(
            "run-anchor",
            condition_id="vad_0",
            commits=(commit((MARKER + ".", 0.82, 0.98)),),
        ),
    )

    export_viewer.export([path], public_dir=public)

    report = json.loads((public / "comparison.json").read_text())["report"]
    assert [c["condition_id"] for c in report["conditions"]] == ["vad_0"]


def test_a_bundle_with_no_anchor_says_so_rather_than_omitting_the_comparison(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing comparison file is indistinguishable from a broken deployment.

    The file is always written, carrying the reason it could not be built, so the
    viewer can state the absence instead of rendering an empty table that reads as
    "no drift".
    """
    monkeypatch.setattr(
        export_viewer,
        "load_clips",
        lambda: {"marker": clip("marker", 1600), "earlier": clip("earlier", 1600)},
    )
    path = write_record(
        tmp_path,
        make_record(
            "run-drift",
            condition_id="vad_1",
            prior_segments=1,
            commits=(commit((MARKER + ".", 0.92, 1.08)),),
        ),
    )

    export_viewer.export([path], public_dir=public)

    exported = json.loads((public / "comparison.json").read_text())
    assert "report" not in exported
    assert "anchor" in exported["unavailable_reason"]


def test_the_unavailable_reason_is_reported_rather_than_only_recorded(
    public: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        export_viewer,
        "load_clips",
        lambda: {"marker": clip("marker", 1600), "earlier": clip("earlier", 1600)},
    )
    path = write_record(
        tmp_path,
        make_record(
            "run-drift",
            condition_id="vad_1",
            prior_segments=1,
            commits=(commit((MARKER + ".", 0.92, 1.08)),),
        ),
    )

    export_viewer.export([path], public_dir=public)

    assert "no comparison" in capsys.readouterr().err


def test_a_run_that_lost_its_marker_withholds_the_whole_comparison(
    public: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opposite of the audio rule, and deliberately so.

    A run whose marker never came back has no figure for its condition's column.
    Dropping it would leave a table of the remaining conditions looking exactly like
    the matrix -- the other runs' deltas presented as this experiment's finding. So
    the bundle refuses to publish a comparison at all, and names what stopped it.
    """
    monkeypatch.setattr(
        export_viewer,
        "load_clips",
        lambda: {"marker": clip("marker", 1600), "earlier": clip("earlier", 1600)},
    )
    good = make_record(
        "run-anchor",
        condition_id="vad_0",
        commits=(commit((MARKER + ".", 0.82, 0.98)),),
    )
    lost = make_record(
        "run-drift",
        condition_id="vad_1",
        prior_segments=1,
        # The run came back with words -- just not the marker.
        commits=(commit(("Kolvig.", 0.22, 0.64)),),
    )
    paths = [write_record(tmp_path, good), write_record(tmp_path, lost)]

    export_viewer.export(paths, public_dir=public)

    exported = json.loads((public / "comparison.json").read_text())
    assert "report" not in exported
    assert MARKER in exported["unavailable_reason"]


def test_an_unreadable_record_names_the_file(tmp_path: Path) -> None:

    bad = tmp_path / "not-a-record.json"
    bad.write_text("{ not json")

    with pytest.raises(SystemExit, match=re.escape("not-a-record.json")):
        export_viewer.export([bad])


def test_the_committed_evidence_exports_with_audio(tmp_path: Path, public: Path) -> None:
    """The real bundle: all twelve published records, each with playable audio.

    The strongest check available that the export path works on the actual evidence
    rather than on fixtures shaped like it. Skipped rather than failed if the evidence
    is absent, so a clone that has not copied it in still runs the suite.
    """
    sources = sorted((REPO_ROOT / "evidence" / "runs").glob("*.json"))
    if not sources:
        pytest.skip("no published run records on disk")

    entries = export_viewer.export(sources, public_dir=public)

    assert len(entries) == len(sources)
    assert all(entry["has_audio"] is True for entry in entries), [
        entry["run_id"] for entry in entries if not entry["has_audio"]
    ]
    # 18 s at 16 kHz, which is the fixture geometry the manifest describes.
    first = public / "audio" / f"{entries[0]['run_id']}.wav"
    with wave.open(io.BytesIO(first.read_bytes()), "rb") as handle:
        assert handle.getframerate() == SAMPLE_RATE
        assert handle.getnframes() == SAMPLE_RATE * 18


def test_the_exported_comparison_is_the_published_one(
    tmp_path: Path, public: Path
) -> None:
    """The bundle must quote the same figures as the report, not a second opinion.

    `evidence/comparison.json` is what the README's table was written from. If the
    bundle derived its own, a reader comparing the two would be comparing two
    calculations of the same measurement -- and would have no way to tell which
    one the page was showing.
    """
    sources = sorted((REPO_ROOT / "evidence" / "runs").glob("*.json"))
    if not sources:
        pytest.skip("no published run records on disk")
    published_path = REPO_ROOT / "evidence" / "comparison.json"
    if not published_path.exists():
        pytest.skip("no published comparison on disk")

    export_viewer.export(sources, public_dir=public)

    assert json.loads((public / "comparison.json").read_text())["report"] == json.loads(
        published_path.read_text()
    )


def test_the_published_evidence_s_commits_are_marked_at_the_samples_they_occurred_at(
    public: Path,
) -> None:
    """The real bundle: each run's commits land where the fixture put the speech.

    Read against the published records rather than a fixture shaped like them, so
    this fails if either the derivation or the evidence moves.
    """
    sources = sorted((REPO_ROOT / "evidence" / "runs").glob("*.json"))
    if not sources:
        pytest.skip("no published run records on disk")

    entries = {e["run_id"]: e for e in export_viewer.export(sources, public_dir=public)}
    vad_2 = next(e for e in entries.values() if e["condition_id"] == "vad_2")

    # Two preceding commits plus the one carrying the marker, in order, and the
    # marker's own commit is where the drift was measured: 12,380 ms.
    assert [c["commit_index"] for c in vad_2["commits"]] == [1, 2, 3]
    assert vad_2["commits"][2]["first_word_ms"] == pytest.approx(12_380.0)

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
from pathlib import Path
from types import ModuleType

import pytest

from pcm_fixtures import SAMPLE_RATE, ramp_pcm
from scribe_timeline.audio.timeline import Clip
from scribe_timeline.records import EchoedSessionConfig, Manifest, RunRecord, Segment

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


def make_record(run_id: str, *, clip_id: str = "marker") -> RunRecord:
    return RunRecord(
        schema_version=1,
        run_id=run_id,
        condition_id="vad_1",
        commit_strategy="vad",
        repeat_index=0,
        manifest=Manifest(
            condition_id="vad_1",
            sample_rate=SAMPLE_RATE,
            sample_count=SAMPLE_RATE * 2,
            markers={MARKER: 8000},
            segments=(
                Segment(clip_id=clip_id, start_sample=8000, sample_count=1600, marker_text=MARKER),
            ),
        ),
        echoed_config=EchoedSessionConfig(
            model_id="scribe_v2_realtime",
            language_code="en",
            sample_rate=SAMPLE_RATE,
            include_timestamps=True,
        ),
    )


def write_record(directory: Path, record: RunRecord) -> Path:
    """Write a record the way a runner does: one JSON file, indented."""
    path = directory / f"{record.run_id}.json"
    path.write_text(record.model_dump_json(indent=2) + "\n")
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
        }
    ]


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

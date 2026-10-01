"""Export saved run records as a static, credential-free viewer bundle.

Run via `make viewer-export` (or `python3 scripts/export_viewer.py`). Reads saved
run records and the committed clips, and writes the files a static site needs:

    viewer/public/runs/<run_id>.json    the run record, unchanged
    viewer/public/audio/<run_id>.wav    that record's audio, rebuilt from its manifest
    viewer/public/comparison.json       the comparison over exactly these records

No network, no API key, and no capture. Everything written here is derivable from
files already in the repository, which is what makes it safe to publish.

Run records are copied **unmodified**. The comparison, the report, and this viewer
all read the same bytes, so a reader can check any figure in the README against the
file the site served.

The comparison is exported rather than left to the viewer because
`scribe_timeline.analysis.compare` is the only place the deltas exist. A viewer
that recomputed them would be a second implementation of the measurement, free to
disagree with the README by a quantisation step and with no test to say which was
right.

`comparison.json` is always written, even when no comparison is derivable -- a
single run, or a set with no zero-preceding-commit anchor. A missing file would be
indistinguishable from a broken deployment, so an unbuildable comparison is written
as a stated reason instead.

Two derived figures ride along in the index rather than in the records, because the
records are the evidence and are served byte-for-byte:

* each run's commit boundaries, in milliseconds (`viewer.commits`), so the viewer
  places them on the audio clock without converting anything itself;
* whether that run's audio could be rebuilt (`has_audio`).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from scribe_timeline.analysis.compare import compare_runs  # noqa: E402
from scribe_timeline.analysis.matching import MarkerNotFound  # noqa: E402
from scribe_timeline.audio.family import MARKER_TEXT  # noqa: E402
from scribe_timeline.capture.clips import FixtureError, load_clips  # noqa: E402
from scribe_timeline.records import RunRecord  # noqa: E402
from scribe_timeline.viewer.audio import RecordAudioMismatch, wav_for_record  # noqa: E402
from scribe_timeline.viewer.commits import UnknownSourceUnit, commit_extents  # noqa: E402

DEFAULT_RUN_GLOB = "evidence/runs/*.json"
PUBLIC_DIR = REPO_ROOT / "viewer" / "public"
INDEX_FILENAME = "runs.json"
COMPARISON_FILENAME = "comparison.json"


def load_records(paths: Sequence[Path]) -> list[tuple[Path, RunRecord]]:
    """Parse every named run record, naming the file if one is unreadable.

    Paired with its path, because the raw bytes are copied verbatim on export: the
    site must serve the same file the comparison read, not a re-serialisation of it.
    """
    loaded: list[tuple[Path, RunRecord]] = []
    for path in paths:
        try:
            loaded.append((path, RunRecord.model_validate_json(path.read_text())))
        except Exception as exc:  # pydantic's error type is not part of the contract
            raise SystemExit(f"{path}: {exc}") from exc
    return loaded


def commits_for(record: RunRecord) -> list[dict[str, object]]:
    """Where this run's commits fell on the audio clock, in milliseconds.

    Read from the record's own events and converted here, once. The events hold
    seconds in a field named `start`, and a viewer that treated `0.22` as a
    millisecond would scale its whole track by a thousand while still looking like
    a timeline.

    Raises `UnknownSourceUnit` rather than assuming: a unit this project cannot
    convert is a corrupt record, and every boundary it would produce would be
    wrong by a factor nobody could see.
    """
    return [
        {
            "commit_index": extent.commit_index,
            "word_count": extent.word_count,
            "first_word_ms": extent.first_word_ms,
            "last_word_ms": extent.last_word_ms,
        }
        for extent in commit_extents(record)
    ]


def index_entry(
    record: RunRecord, *, has_audio: bool, commits: Sequence[dict[str, object]]
) -> dict[str, object]:
    """One row of the bundle index: what a reader can pick, and how it plays.

    `has_audio` states whether the WAV was actually rebuilt, so the site can show a
    record whose audio could not be rather than pointing at a file that is not
    there. `commits` places the run's commit boundaries; an empty list means the
    run returned no timestamped commit, which is an observation rather than a
    missing value.
    """
    return {
        "run_id": record.run_id,
        "condition_id": record.condition_id,
        "commit_strategy": record.commit_strategy,
        "repeat_index": record.repeat_index,
        "audio_path": f"audio/{record.run_id}.wav",
        "has_audio": has_audio,
        "commits": list(commits),
    }


def comparison_document(records: Sequence[RunRecord]) -> dict[str, object]:
    """The comparison over exactly the records in this bundle.

    Either the report, or the reason one cannot be built. The reason is a value
    rather than a missing file, so a reader is told the bundle holds no comparison
    instead of being shown an empty table that reads as "no drift found".

    **A record that cannot support a measurement withholds the whole comparison**,
    which is the opposite of the audio rule above, and deliberately so. A run whose
    marker never came back has no figure to put in its condition's column, and
    dropping it would leave a table of the *remaining* conditions looking exactly
    like the matrix. The deltas are the finding; a comparison over a subset of the
    runs is a different measurement, and publishing it as this one would be the
    worse mistake. So the bundle says it holds no comparison, and names the record
    that stopped it.

    `compare_runs` signals every way of being unable to measure by raising: a
    record missing its marker raises `MarkerNotFound`, and a set with no anchor --
    or with more than one -- raises `ValueError`. Both are caught and reported.
    Nothing else is, so a genuine bug inside the comparison surfaces rather than
    being published as "this bundle holds no comparison".
    """
    try:
        report = compare_runs(records, marker_text=MARKER_TEXT)
    except (ValueError, MarkerNotFound) as exc:
        return {"unavailable_reason": str(exc)}
    return {"report": report.to_dict()}


def export(paths: Sequence[Path], *, public_dir: Path = PUBLIC_DIR) -> list[dict[str, object]]:
    """Write the bundle. Returns the index entries, so a caller can report on it."""
    try:
        clips = load_clips()
    except FixtureError as exc:
        raise SystemExit(f"fixture error: {exc}") from exc

    sources = load_records(paths)
    if not sources:
        raise SystemExit(f"no run records matched {DEFAULT_RUN_GLOB}")

    (public_dir / "runs").mkdir(parents=True, exist_ok=True)
    (public_dir / "audio").mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    failures: list[str] = []
    for path, record in sources:
        # Copied verbatim, not re-serialised: the site must serve the same bytes the
        # comparison read, so a reader can check any figure against the served file.
        (public_dir / "runs" / f"{record.run_id}.json").write_text(path.read_text())
        try:
            commits = commits_for(record)
        except UnknownSourceUnit as exc:
            # Refusing the whole export rather than this record: a timestamp unit
            # this project cannot convert is a corrupt record, and every boundary it
            # would contribute would be wrong by a factor that reads as a timeline.
            raise SystemExit(f"{path}: {exc}") from exc
        try:
            (public_dir / "audio" / f"{record.run_id}.wav").write_bytes(
                wav_for_record(record, clips)
            )
        except RecordAudioMismatch as exc:
            # One unreplayable record must not cost the reader the other eleven.
            failures.append(f"{record.run_id}: {exc}")
            entries.append(index_entry(record, has_audio=False, commits=commits))
            continue
        entries.append(index_entry(record, has_audio=True, commits=commits))

    index = {
        "note": (
            "Saved evidence, replayed from committed clips. No capture was re-run and no "
            "API call was made to build this."
        ),
        "runs": entries,
    }
    (public_dir / INDEX_FILENAME).write_text(json.dumps(index, indent=2) + "\n")

    comparison = comparison_document([record for _, record in sources])
    (public_dir / COMPARISON_FILENAME).write_text(json.dumps(comparison, indent=2) + "\n")

    for failure in failures:
        print(f"no audio: {failure}", file=sys.stderr)
    if "unavailable_reason" in comparison:
        reason = comparison["unavailable_reason"]
        print(f"no comparison: {reason}", file=sys.stderr)
    return entries


def main() -> int:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    paths = [Path(arg) for arg in args]
    if not paths:
        paths = sorted(REPO_ROOT.glob(DEFAULT_RUN_GLOB))

    written = PUBLIC_DIR
    entries = export(paths, public_dir=written)
    playable = sum(1 for entry in entries if entry["has_audio"])
    # The directory printed is the one written, not the module's default: a caller
    # that redirects the export must not be told the files went somewhere else.
    print(f"exported {len(entries)} run record(s) to {written}")
    print(f"  {playable} with audio, {len(entries) - playable} without")
    print(f"  comparison in {written / COMPARISON_FILENAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

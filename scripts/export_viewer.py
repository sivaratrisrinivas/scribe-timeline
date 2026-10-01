"""Export saved run records as a static, credential-free viewer bundle.

Run via `make viewer-export` (or `python3 scripts/export_viewer.py`). Reads saved
run records and the committed clips, and writes the files a static site needs:

    viewer/public/runs/<run_id>.json    the run record, unchanged
    viewer/public/audio/<run_id>.wav    that record's audio, rebuilt from its manifest

No network, no API key, and no capture. Everything written here is derivable from
files already in the repository, which is what makes it safe to publish.

Run records are copied **unmodified**. The comparison, the report, and this viewer
all read the same bytes, so a reader can check any figure in the README against the
file the site served.

The audio is rebuilt per record rather than per condition because two records with
identical audio would otherwise share a file, and a reader comparing them should be
able to confirm for themselves that the bytes really were identical.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from scribe_timeline.capture.clips import FixtureError, load_clips  # noqa: E402
from scribe_timeline.records import RunRecord  # noqa: E402
from scribe_timeline.viewer.audio import RecordAudioMismatch, wav_for_record  # noqa: E402

DEFAULT_RUN_GLOB = "evidence/runs/*.json"
PUBLIC_DIR = REPO_ROOT / "viewer" / "public"
INDEX_FILENAME = "runs.json"


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


def index_entry(record: RunRecord, *, has_audio: bool) -> dict[str, object]:
    """One row of the bundle index: what a reader can pick, and whether it plays.

    `has_audio` states whether the WAV was actually rebuilt, so the site can show a
    record whose audio could not be rather than pointing at a file that is not there.
    """
    return {
        "run_id": record.run_id,
        "condition_id": record.condition_id,
        "commit_strategy": record.commit_strategy,
        "repeat_index": record.repeat_index,
        "audio_path": f"audio/{record.run_id}.wav",
        "has_audio": has_audio,
    }


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
            (public_dir / "audio" / f"{record.run_id}.wav").write_bytes(
                wav_for_record(record, clips)
            )
        except RecordAudioMismatch as exc:
            # One unreplayable record must not cost the reader the other eleven.
            failures.append(f"{record.run_id}: {exc}")
            entries.append(index_entry(record, has_audio=False))
            continue
        entries.append(index_entry(record, has_audio=True))

    index = {
        "note": (
            "Saved evidence, replayed from committed clips. No capture was re-run and no "
            "API call was made to build this."
        ),
        "runs": entries,
    }
    (public_dir / INDEX_FILENAME).write_text(json.dumps(index, indent=2) + "\n")

    for failure in failures:
        print(f"no audio: {failure}", file=sys.stderr)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

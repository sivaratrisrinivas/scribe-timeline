"""Viewing the published evidence must cost a reader nothing.

Two promises are checked here, and both are about what a reader does *not* pay for:

* **No network.** The bundle, the audio and the comparison are derived from files
  already in the repository, so replaying a run is a local operation. The exporter
  and the re-derivation are run here with every socket call replaced by a failure,
  so a network dependency shows up as a failing test rather than as a working demo
  on the machine that happens to have connectivity.
* **No credential, and therefore no API usage.** `ELEVENLABS_API_KEY` is removed
  from the environment, and a key-shaped value is planted in the environment for
  the test that looks for leaks. Nothing in this path may read it, and nothing it
  writes may carry it.

The tests use the committed evidence rather than fixtures shaped like it. A claim
about the published bundle is only checkable against the published bundle.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import sys
from pathlib import Path
from types import ModuleType

import pytest

from scribe_timeline.analysis.compare import compare_runs
from scribe_timeline.audio.family import MARKER_TEXT
from scribe_timeline.records import RunRecord

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = REPO_ROOT / "evidence"
PUBLISHED_COMPARISON = EVIDENCE / "comparison.json"

#: A key-shaped value, planted rather than read. Nothing should ever see it, so the
#: test that greps for it is falsifiable -- it can fail.
FAKE_KEY = "sk_offline_viewing_test_0123456789abcdef"

#: Anything of that shape, not only the value planted above, so a leak through some
#: other variable is caught too.
KEY_SHAPED = re.compile(r"sk-[A-Za-z0-9_-]{16,}")


def load_export_module() -> ModuleType:
    """Import `scripts/export_viewer.py`, which is a script rather than a package member.

    Loaded by path so the exporter keeps working as `python3 scripts/export_viewer.py`
    while staying testable. Every call gets a fresh module, so one test's
    monkeypatching cannot reach another's -- which is why the exporter is reached
    through this rather than through a module-level constant.
    """
    spec = importlib.util.spec_from_file_location(
        "export_viewer_offline", REPO_ROOT / "scripts" / "export_viewer.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _evidence_records() -> list[Path]:
    records = sorted((EVIDENCE / "runs").glob("*.json"))
    if not records:
        pytest.skip("no published run records on disk")
    return records


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the credential and refuse every outbound connection.

    Both halves matter, and neither alone would do. Without the key removed, a
    dependency on it would pass here and fail for a reader who has none. Without
    the sockets blocked, a network call would pass here on a connected machine and
    cost a reader a failed page.
    """
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            f"this path must not touch the network (called with {args!r}); "
            f"everything a reader is shown comes from files in the repository"
        )

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def test_the_bundle_is_built_with_no_network_and_no_credential(
    offline: None, tmp_path: Path
) -> None:
    """Every file a reader is served is derived, not captured.

    All twelve, with audio, from the committed evidence -- the whole promise in one
    assertion. The literal twelve is the point, not an accident: the README's table
    is four conditions x three repeats, and a bundle that quietly held eleven would
    leave a condition with two repeats and a spread that means nothing. The count is
    also checked against the evidence on disk, so this fails on a dropped run as well
    as on a changed one.
    """
    public = tmp_path / "public"
    records = _evidence_records()

    entries = load_export_module().export(records, public_dir=public)

    assert len(entries) == len(records) == 12, (
        f"the bundle holds {len(entries)} run(s) from {len(records)} published record(s); "
        f"the README's table describes four conditions x three repeats"
    )
    assert all(entry["has_audio"] is True for entry in entries)
    assert (public / "runs.json").exists()
    assert (public / "comparison.json").exists()


def test_every_run_the_index_offers_is_actually_there(offline: None, tmp_path: Path) -> None:
    """An index promising a file the bundle does not hold is a broken page.

    `has_audio` is the exporter's word that the WAV was written, and the page trusts
    it: it offers a player rather than saying the audio is missing. So the two must
    agree, and the file must be non-empty -- a zero-byte WAV loads and plays
    nothing, which is indistinguishable from a run that returned silence.
    """
    public = tmp_path / "public"

    entries = load_export_module().export(_evidence_records(), public_dir=public)

    for entry in entries:
        audio = public / str(entry["audio_path"])
        assert audio.exists(), f"{entry['run_id']} is listed with audio that is not there"
        assert audio.stat().st_size > 44, f"{entry['run_id']}'s wav has no samples in it"
        # The record the index names must be the record the site would serve.
        assert (public / "runs" / f"{entry['run_id']}.json").exists()

    unplayable = [entry for entry in entries if not entry["has_audio"]]
    for entry in unplayable:
        assert not (public / str(entry["audio_path"])).exists()


def test_the_published_figures_are_re_derivable_without_spending_anything(
    offline: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`make matrix --from-saved`, run with no key and no network.

    The whole claim of the project is that a reader can check the published numbers
    rather than take them on trust. That check runs the same arithmetic the live run
    did, over the same committed records, and is compared here against the
    comparison that was published -- so this test fails if either side drifts.

    Run in a temporary working directory because `--from-saved` writes its report
    relative to the cwd, and this repository's own `runs/` is not the place for a
    test's output.
    """
    from scribe_timeline.capture import matrix as matrix_module

    monkeypatch.chdir(tmp_path)
    records = _evidence_records()

    exit_code = matrix_module.main(["--from-saved", *(str(path) for path in records)])

    assert exit_code == 0
    rederived = json.loads((tmp_path / "runs" / "comparison.json").read_text())
    assert rederived == json.loads(PUBLISHED_COMPARISON.read_text())
    # The printed table is what a reader without the JSON would read, so it is
    # checked too: a run that computed the right numbers and printed the wrong ones
    # would still mislead. The figure is the README's own -- the marker's returned
    # timestamp under `vad_2` -- rather than a number written to be easy to change.
    printed = capsys.readouterr().out
    assert "12,380" in printed
    assert "vad_2" in printed


def test_the_published_figures_come_from_the_records_in_this_repository(
    offline: None,
) -> None:
    """The other half of the previous test, without the subprocess detour.

    `evidence/comparison.json` is the report the README's table was written from, and
    it is committed beside the records it was built from. This asserts the two still
    describe the same measurement: a record edited after publication, or a report
    left behind from a different set of runs, is exactly the drift a reader checking
    the published numbers would find and be unable to explain.

    Asserted against the comparison module's own output rather than a hard-coded
    table, so a change to the report's shape does not need this test edited with it.
    """
    records = [RunRecord.model_validate_json(path.read_text()) for path in _evidence_records()]

    published = json.loads(PUBLISHED_COMPARISON.read_text())

    assert published == compare_runs(records, marker_text=MARKER_TEXT).to_dict()


def test_nothing_the_bundle_serves_carries_the_key_that_happened_to_be_in_the_environment(
    offline: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strongest form of "public viewing costs the reader nothing".

    A key is planted in the environment for the whole export, and the bytes written
    are searched for it. The exporter walks payloads for credential-*shaped keys*,
    which cannot see a secret sitting in a value; this test can, and asserts the
    bundle is readable by anyone at all.
    """
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    public = tmp_path / "public"

    load_export_module().export(_evidence_records(), public_dir=public)

    for path in sorted(public.rglob("*")):
        if not path.is_file():
            continue
        # Binary audio cannot carry a key, but reading it as bytes costs nothing
        # and removes the need to decide which files are text.
        assert FAKE_KEY.encode() not in path.read_bytes(), f"{path} carries the key"
        if path.suffix == ".json":
            # Belt and braces: anything key-shaped at all, not only this key, so a
            # leak through a different variable still fails.
            assert not KEY_SHAPED.search(path.read_text()), f"{path} is key-shaped"


def test_the_exporter_will_not_publish_a_record_it_cannot_read(
    offline: None, tmp_path: Path
) -> None:
    """A malformed record is named and refused, not copied through.

    The run record is served byte-for-byte, so a record this code cannot read would
    reach a reader as a file the bundle's own comparison never looked at. Failing
    the export names the file, which is the only thing a maintainer can act on.
    """
    broken = tmp_path / "broken.json"
    broken.write_text('{"schema_version": 1, "run_id": "broken"}\n')

    with pytest.raises(SystemExit, match=re.escape("broken.json")):
        load_export_module().export([broken], public_dir=tmp_path / "public")


def test_a_record_from_another_contract_is_refused_rather_than_read(
    offline: None, tmp_path: Path
) -> None:
    """The version check reaches the export path, not only the model.

    `SchemaVersion` is a literal, so a record declaring another contract cannot
    validate. Asserted through the exporter because that is where a reader would
    meet it: a hand-edited or third-party record in a bundle directory.
    """
    record = json.loads(_evidence_records()[0].read_text())
    record["schema_version"] = 2
    path = tmp_path / "future.json"
    path.write_text(json.dumps(record))

    with pytest.raises(SystemExit, match=re.escape("future.json")):
        load_export_module().export([path], public_dir=tmp_path / "public")


def test_the_export_never_so_much_as_asks_for_the_key(
    offline: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The offline path does not read the key; it does not check whether it can.

    The other tests here delete the key, which proves the path works without one.
    That is weaker than it looks: a future edit could add a read that tolerates
    absence, the tests would still pass, and the first thing a reader without a key
    would meet would be an error about the key. So reading it is made fatal here
    instead, and the whole export runs again.

    Both ways of reading are covered. `os.environ.get` is how the codebase reads the
    key today, but `os.environ["..."]` is equally available and would be the next
    edit someone makes, so the mapping is replaced rather than the function.

    Reading the clips is included, and belongs here: `load_clips` reads the committed
    PCM files, and the SDK it imports is only asked to synthesise when a clip is
    *missing* -- which is the one case where refusing, loudly, is correct.
    """

    def refuse(name: str) -> None:
        if name == "ELEVENLABS_API_KEY":
            raise AssertionError("the offline path must not read the API key")

    class RefusingEnviron(dict[str, str]):
        """An `os.environ` stand-in that raises on the key and passes the rest through.

        Subclasses `dict` rather than wrapping it so both read forms are covered by
        construction: `environ.get(name)` and `environ[name]`.
        """

        def __missing__(self, name: str) -> str:
            refuse(name)
            raise KeyError(name)

        def get(  # type: ignore[override]
            self, name: str, default: str | None = None
        ) -> str | None:
            refuse(name)
            return dict.get(self, name, default)

    monkeypatch.setattr(os, "environ", RefusingEnviron(os.environ))

    load_export_module().export(_evidence_records(), public_dir=tmp_path / "public")

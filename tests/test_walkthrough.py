"""The walkthrough is a claim about the evidence, in a format nothing else checks.

`docs/walkthrough.webm` is a 73-second recording of the real viewer, driven against the
real built bundle by `scripts/record_walkthrough.js`. Every other artifact in this
repository is text that `make check` can read, and the numbers in it can be compared
against `evidence/comparison.json`. A video cannot be: nothing diffs a video, nothing
re-derives it, and a stale one still plays perfectly.

That is the gap this module closes, and it is worth being precise about what it does
and does not buy. It cannot watch the video. It can check that the script which
produced it names each required beat, that the recording exists and is roughly the
length the issue asks for, and that the figures the script's captions assert are the
figures the evidence holds. A video edited by hand after recording would pass all of
that, and nothing here pretends otherwise.

What it does catch is the failure that actually happens: someone changes the evidence,
or the comparison, and the recording is never re-made. The captions are asserted
against `evidence/comparison.json`, so a rerun that changed the deltas makes this fail
with the figure that moved -- which is the signal to re-record.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "record_walkthrough.js"
VIDEO = REPO_ROOT / "docs" / "walkthrough.webm"
COMPARISON = REPO_ROOT / "evidence" / "comparison.json"

#: The length the issue asks for, and the window a recording is accepted in.
#:
#: A tolerance rather than an equality because the exact duration depends on how long
#: the machine took to load twelve run records and a rebuilt WAV. Narrow enough that a
#: truncated or doubled recording fails; wide enough that a slower machine does not.
TARGET_SECONDS = 75
SECONDS_WINDOW = 10


def script() -> str:
    return SCRIPT.read_text()


def comparison() -> dict[str, Any]:
    """The published report, as loaded.

    Typed as `Any` values rather than `object`, because this module reads figures out
    of it and `object` would push an `Any` cast onto every one of them -- which is what
    strict mode is asking for. The alternative, a model per field, is more type-safe and
    more code than a test that only wants `delta_vs_anchor_ms`; the drift this guards
    against is a *value* changing, which no annotation would catch.
    """
    document: dict[str, Any] = json.loads(COMPARISON.read_text())
    return document


def test_the_recording_script_exists_and_the_recording_with_it() -> None:
    """Both halves, because either alone is a promise with nothing behind it.

    The script is what makes the video reproducible -- it drives the real viewer, so a
    re-record needs no re-authoring. The video is what the README links. A script with
    no output means the video is unmaintainable; an output with no script means nobody
    can tell what produced it.
    """
    assert SCRIPT.exists(), f"{SCRIPT.relative_to(REPO_ROOT)} is missing"
    assert VIDEO.exists(), (
        f"{VIDEO.relative_to(REPO_ROOT)} is missing, so the README's walkthrough link "
        "points at nothing. Run `make walkthrough`."
    )


def test_the_recording_is_about_seventy_five_seconds() -> None:
    """Roughly the length the issue specifies, and inside a committed size budget.

    Read with `ffprobe` when it is available and skipped when it is not, rather than
    parsed out of the container here -- the container format is not worth a parser, and
    a Python-only checkout should not fail the gate over a video.
    """
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not available to measure the walkthrough")

    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(VIDEO),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    seconds = float(result.stdout.strip())

    assert abs(seconds - TARGET_SECONDS) <= SECONDS_WINDOW, (
        f"the walkthrough runs {seconds:.0f}s; the issue asks for about "
        f"{TARGET_SECONDS}s (accepted within {SECONDS_WINDOW}s)"
    )


def test_the_recording_drives_the_real_bundle_over_http() -> None:
    """The video shows the published page, not a fixture.

    The script serves `viewer/dist` from a subpath and drives it with Playwright. That
    is the only arrangement in which the recording is evidence: a hand-built HTML mock
    would show a comparison table that never existed, and would keep showing it after
    the page changed.
    """
    body = script()

    assert "viewer" in body and "dist" in body, (
        "the recording script does not read viewer/dist, so it is not recording the "
        "published bundle"
    )
    assert "playwright" in body.lower(), (
        "the recording script does not drive a browser, so it is not recording the "
        "real page"
    )
    # A subpath, because that is how Pages mounts it, and a page that only works at
    # the domain root would record as a blank frame.
    assert "/scribe-timeline/" in body, (
        "the recording script does not mount the bundle under a subpath, so it would "
        "record the page succeeding where Pages would 404 it"
    )


def test_every_required_beat_is_named_in_the_script() -> None:
    """The four beats the issue requires, each asserted on the caption that carries it.

    Checked as caption *content* rather than as a list of steps, because a step that
    shows nothing is not a beat. Each entry is a substring that can only be in the
    recording if the script puts that content on screen.
    """
    body = script()

    beats = {
        "the timing question": "@wujin941005",
        "the timing question (the issue it is about)": "849",
        "the same marker across conditions": "identical bytes and sample position",
        "the measured differences": "vad_1 +100 ms, vad_2 +180 ms",
        "the repeat variation": "spread 0 ms",
        "the control that rules out commit count": "manual_2",
        "the raw evidence": "Raw evidence",
        "the rerun command": "--from-saved evidence/runs/*.json",
    }

    for what, needle in beats.items():
        assert needle in body, f"the recording has no beat for {what}: {needle!r} is absent"


def test_the_captions_quote_the_figures_the_evidence_holds() -> None:
    """The deltas in the captions are the deltas in `evidence/comparison.json`.

    The reason this module exists. A video cannot be diffed, so the way a stale
    recording is caught is by asserting the numbers its captions assert against the
    numbers the repository holds. Change a condition, re-export, and this fails naming
    the figure that moved -- which is the prompt to re-record.
    """
    report = comparison()
    conditions = {entry["condition_id"]: entry for entry in report["conditions"]}

    body = script()

    for condition_id in ("vad_1", "vad_2"):
        delta = conditions[condition_id]["delta_vs_anchor_ms"]
        assert f"{condition_id} +{delta:.0f} ms" in body, (
            f"the caption says something other than {condition_id}'s measured "
            f"{delta:+.0f} ms; the recording would show a figure the evidence does "
            f"not hold"
        )

    control = report["control"]
    assert f"+{control['manual_delta_ms']:.0f} ms" in body, (
        "the caption does not quote the manual control's measured delta of "
        f"{control['manual_delta_ms']:+.0f} ms"
    )


def test_the_anchor_the_captions_quote_is_the_reports_anchor() -> None:
    """`vad_0` is called the anchor in the recording because the report says so.

    A smaller version of the check above, and worth having separately: the captions
    name conditions by id, and an id that stops being the anchor would make every
    delta in the video wrong while each individual figure still matched.
    """
    report = comparison()

    assert report["anchor_condition_id"] == "vad_0", (
        f"the report's anchor is {report['anchor_condition_id']!r}; the walkthrough's "
        "captions and the README both describe vad_0 as the anchor"
    )
    assert "against the anchor" in script(), (
        "the recording never says what the deltas are measured against"
    )


def test_the_recording_states_it_is_saved_evidence_not_a_capture() -> None:
    """The video says it is replaying saved runs.

    A reader who cannot tell a replayed recording from a fresh capture will read the
    page's figures as a live measurement -- which is exactly the confusion the viewer's
    own note exists to prevent, and the video would reintroduce it for anyone who only
    watched the video.
    """
    # Case-insensitively: the caption reads it mid-sentence ("...no capture was re-run
    # and no API call was made"), while the page's own note capitalises it. The claim is
    # the same and the wording is the page's, so matching one case exactly would be a
    # test of capitalisation.
    assert "no capture was re-run" in script().lower(), (
        "the recording never states that no capture was re-run, so a viewer could read "
        "it as a live measurement"
    )


def test_the_script_names_no_credential() -> None:
    """Recording a replay costs nothing and needs no key.

    Wide on purpose, as everywhere else this is checked: any mention of the variable
    at all. A walkthrough that captured live audio would both spend credit and stop
    being evidence for the committed run records.
    """
    assert "ELEVENLABS" not in script(), (
        "the recording script references the API credential; it replays committed "
        "runs and must never capture"
    )


def test_the_committed_video_is_not_gitignored() -> None:
    """The video is committed, which is what makes the README link durable.

    The derived bundle is ignored and rebuilt; this is the opposite. It is an artifact
    rather than a derivation, and a link that only resolves for whoever happens to have
    a checkout is not a public link.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-q", "docs/walkthrough.webm"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    if result.returncode != 0 and result.returncode != 1:
        pytest.skip("git is not available to ask about ignore rules")

    assert result.returncode == 1, (
        "docs/walkthrough.webm is gitignored, so the walkthrough the README links is "
        "in nobody's clone"
    )


def test_no_stray_recordings_are_committed() -> None:
    """One video, at the documented path.

    Playwright names its own output file and the script renames it. A run that failed
    between the two would leave a second `.webm` behind in `docs/`, and a directory of
    near-identical videos is how a reader ends up watching a stale one.
    """
    if not VIDEO.parent.exists():
        pytest.skip("the walkthrough has not been recorded yet")

    recordings = sorted(VIDEO.parent.glob("*.webm"))

    assert recordings == [VIDEO], (
        f"unexpected recordings beside the walkthrough: "
        f"{[p.name for p in recordings]}"
    )


def test_the_walkthrough_is_linked_from_the_readme() -> None:
    """The README points at the video by the path that exists.

    Asserted as a resolved path rather than as the presence of the string "walkthrough",
    because a README that names the file in prose while linking something else is the
    ordinary way a link like this goes stale.
    """
    readme = (REPO_ROOT / "README.md").read_text()
    references = re.findall(r"\]\((docs/[^)]+\.webm)\)", readme)

    assert references, (
        "the README links no walkthrough video; the report's 75-second walkthrough "
        "is a required part of it"
    )
    for reference in references:
        assert (REPO_ROOT / reference).exists(), (
            f"the README links {reference}, which does not exist"
        )

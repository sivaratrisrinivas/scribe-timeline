"""The public artifact must be a directory of files, not a server.

`make build` produces `viewer/dist`, which is what a reader is served and what gets
published. The claim it has to keep is the one this project is built on: opening that
directory costs nothing, touches no network, and needs no credential.

A Vite build satisfies the "no server" half by construction -- the output is static
assets. It does not, on its own, satisfy the rest. `viewer/public/` is copied into
the build, and nothing in the build checks that what landed there is *this* evidence:
a stale directory from an earlier export would be published silently, and the page
would serve a comparison that no longer matches the records beside it. Nor does
anything stop a dependency on an external origin sneaking in, which would make
"free to view" conditional on someone else's uptime.

So the built output is inspected here. The build runs only when `dist/` is missing,
so the suite does not pay for a production build on every run; what it asserts holds
either way, and a stale `dist` fails it just as a wrong one would.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC = REPO_ROOT / "viewer" / "public"
DIST = REPO_ROOT / "viewer" / "dist"
EVIDENCE_RUNS = sorted((REPO_ROOT / "evidence" / "runs").glob("*.json"))

#: The page's own source, read for the URLs it asks the server for.
APP_SOURCE = (REPO_ROOT / "viewer" / "src" / "App.tsx").read_text()


@pytest.fixture(scope="module")
def dist() -> Path:
    """The built bundle, built on demand.

    `dist/` is gitignored, so on a fresh clone this builds it -- which is the case
    worth testing, since that is the one where nothing is stale and everything has to
    be produced. Skipped rather than failed only when npm is genuinely unavailable, a
    Python-only checkout that cannot build a JavaScript bundle at all. Note that the
    test of the page's own fetch URLs needs no build and always runs.
    """
    if not DIST.exists():
        if shutil.which("npm") is None:
            pytest.skip("npm is not available to build viewer/dist")
        subprocess.run(["npm", "run", "build"], cwd=REPO_ROOT / "viewer", check=True)
    return DIST


def test_the_built_bundle_carries_every_record_and_its_audio(dist: Path) -> None:
    """Everything the index names must be in the built output.

    `public/` is copied into the build wholesale, so a file left behind by an earlier
    export -- a run since removed from the evidence, or renamed -- survives and is
    published. The index is authoritative about what exists, so a stale file is
    invisible to the page and merely wasted bytes at best.
    """
    index = json.loads((dist / "runs.json").read_text())

    for entry in index["runs"]:
        assert (dist / "runs" / f"{entry['run_id']}.json").exists(), entry["run_id"]
        audio = dist / entry["audio_path"]
        if entry["has_audio"]:
            assert audio.exists(), entry["run_id"]
            # A WAV header is 44 bytes; a shorter file is a truncated copy, which
            # loads and plays nothing -- indistinguishable from a silent recording.
            assert audio.stat().st_size > 44, entry["run_id"]
        else:
            # The other direction, and the one a reader would be misled by:
            # `has_audio: false` says there is nothing to play, the page honours it
            # and offers no player -- and a leftover WAV would sit there anyway.
            assert not audio.exists(), f"{entry['run_id']} is listed without audio, but has some"


def test_the_built_bundle_contains_nothing_the_evidence_does_not(dist: Path) -> None:
    """The reverse direction, because a stale file is the failure mode here.

    A run removed from the evidence leaves its record and its audio behind unless the
    export clears them. Publishing those would mean the site holds runs the README
    does not mention -- evidence a reader could reach and find unexplained.
    """
    published = {path.name for path in (dist / "runs").glob("*.json")}
    expected = {path.name for path in EVIDENCE_RUNS}

    assert published == expected, f"unexpected or stale run records: {published ^ expected}"


def test_every_audio_path_in_the_index_is_inside_the_bundle(dist: Path) -> None:
    """The index's `audio_path` values are resolved against the bundle, not a host.

    `audio_path` is a string the page hands straight to `<audio src>`, and the page
    checks nothing about it -- it trusts the index. So a value carrying a scheme or a
    leading slash would send a reader's audio request off to another origin, or to a
    path that only resolves on a dev server rooted at the bundle, and every other test
    here would still pass: the file exists on disk either way.

    The check is on the *index*, not on the exporter, because the index is what the
    page reads. An exporter that emitted an absolute path would produce a bundle this
    test refuses, which is the point.
    """
    index = json.loads((dist / "runs.json").read_text())

    for entry in index["runs"]:
        path = str(entry["audio_path"])
        assert "://" not in path, f"{entry['run_id']} loads audio from {path}"
        assert not path.startswith("/"), (
            f"{entry['run_id']} has a site-absolute audio_path ({path}); it works on a "
            f"dev server and 404s wherever the site is mounted"
        )
        # And the path it names is the file that is actually there, resolved the way
        # the browser would resolve it against the page.
        assert (dist / path).exists(), f"{entry['run_id']} names {path}, which is not in the bundle"


def test_the_built_comparison_is_the_published_one(dist: Path) -> None:
    """The numbers on the page are the report's, and the report is the published one.

    The comparison is the only source of every delta on the page, so a build
    carrying a different one would be publishing a second opinion about the
    measurement with no way for a reader to tell.
    """
    built = json.loads((dist / "comparison.json").read_text())
    published = json.loads((REPO_ROOT / "evidence" / "comparison.json").read_text())

    assert built["report"] == published


def test_the_built_page_loads_nothing_from_another_origin(dist: Path) -> None:
    """No CDN, no font host, no analytics endpoint.

    A dependency on an external origin would make "free to view" conditional on
    someone else's uptime, and would tell that host what a reader was looking at.

    Scoped to references the browser *acts on* -- `src`, `href`, `srcset` in the
    markup, and a fetch or an import in a script -- rather than every URL-shaped
    string in the bundle. A bundler embeds plenty of those: React's production
    error messages carry `react.dev` URLs that are printed in a console and never
    requested, and a check that flagged them would be noise dressed as a finding.
    """
    external = re.compile(r"https?://[^\s\"'()<>]+")

    for path in sorted(dist.rglob("*")):
        if not path.is_file() or path.suffix in {".wav", ".json"}:
            continue
        text = path.read_text(errors="ignore")

        if path.suffix in {".html", ".css"}:
            references = re.findall(
                r"(?:src|href|srcset)\s*=\s*[\"']([^\"']+)[\"']", text
            ) + re.findall(r"url\(([^)]+)\)", text)
        else:
            # A script's only outbound requests are its fetches and its dynamic
            # imports; a URL in a string literal is not one.
            references = re.findall(
                r"(?:fetch|import|importScripts)\(\s*[\"'`]([^\"'`]+)", text
            )

        for reference in references:
            reference = reference.strip("'\"` ")
            found = external.search(reference)
            assert found is None, f"{path.name} loads from {found.group(0)}"


def test_the_page_fetches_only_its_own_bundle() -> None:
    """Every URL the page asks for is inside the bundle it was served.

    Read from the source rather than from a test's mock, because the mock is written
    by the same hand as the code and would agree with whatever the code did. The
    arguments are resolved rather than pattern-matched, so both forms the code uses
    are covered: the plain constants and the per-run template literal.

    Two things are refused. A URL with a scheme or host would leave the bundle
    entirely. And a *site-absolute* path -- `/runs/...` rather than `runs/...` --
    works on a dev server rooted at the bundle and 404s wherever the site is
    actually mounted, which is a failure that would only ever show up after
    publishing.
    """
    calls = re.findall(r"getJson\(([^)]+)\)", APP_SOURCE)
    assert len(calls) >= 2, f"App.tsx fetches {len(calls)} document(s); this test needs updating"

    # Constants may carry a type annotation (`const INDEX_URL: string = "..."`), so
    # one is allowed between the name and the `=`. A call this cannot resolve is
    # reported rather than skipped: a guard that quietly checks nothing is worse
    # than no guard, because it reads as coverage.
    constant = re.compile(r'^const (\w+)\s*(?::\s*[\w<>\[\]| ]+)?=\s*"([^"]*)"\s*;', re.MULTILINE)
    constants = dict(constant.findall(APP_SOURCE))

    for call in calls:
        argument = call.strip().strip("`")
        resolved = re.sub(r"\$\{[^}]*\}", "RUN_ID", argument)
        if argument in constants:
            resolved = constants[argument]
        assert resolved, f"App.tsx fetches {call}, which this test cannot resolve"
        assert "://" not in resolved, f"App.tsx fetches an absolute URL: {resolved}"
        assert not resolved.startswith("/"), f"App.tsx fetches a site-absolute path: {resolved}"

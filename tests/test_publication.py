"""The public link is a promise about where the bundle is *mounted*.

`viewer/dist` is a directory of files that any static host can serve, and this
project's claim is that opening it costs nothing and needs no credential. Both hold
on a dev server and both survive being copied anywhere -- until the host mounts the
site somewhere other than the root.

That is the normal case, not an edge case. GitHub Pages serves a project site from
`https://<user>.github.io/<repo>/`, so every request the page makes is relative to
`/<repo>/`, not to `/`. A build that references `/assets/index-*.js` is then asking
for a file at the domain root, where nothing lives: a 404 for the script, a blank
page, and no error the page itself can report, because the page never loaded.

So the bundle is not checked here as a set of files on disk. It is served from a
subpath, over HTTP, and *fetched the way a browser fetches it*: every reference in
the served markup is resolved against the page's own URL and requested. A reference
that 404s fails the test and names itself.

This is the only test in the repository that starts a web server. It is also the only
one that can catch the failure, because the failure does not exist on disk -- the file
is present, correctly named, exactly where the build put it. It exists at the wrong
*URL*, and only a request can tell.
"""

from __future__ import annotations

import functools
import http.server
import json
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "viewer" / "dist"

#: The workflow that publishes the bundle, if there is one. Named here rather than
#: imported so that a missing file is a readable assertion failure rather than a
#: collection error -- which matters, because the first assertion below is that it
#: exists at all.
PAGES_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pages.yml"

#: Where a project site on GitHub Pages mounts the repository. Chosen to be
#: something a real path could not collide with, and to be the same shape as the
#: Pages URL, which is `/<repo>/` under the domain.
MOUNT = "/scribe-timeline/"

#: The loopback address the throwaway server binds. A literal rather than
#: `localhost`, so the URL cannot depend on a resolver.
HOST = "127.0.0.1"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """The stdlib static handler, without the per-request log line.

    `log_message` is silenced because the requests themselves are the assertions, and
    a passing run should print nothing. The name `format` is the stdlib's own.
    """

    def log_message(self, format: str, *args: object) -> None:
        """No request log. See the class docstring."""


class _QuietServer(http.server.ThreadingHTTPServer):
    """The threading server, without the traceback for a client that hung up.

    `urlopen` closes the connection as soon as it reads a 404 status, before this
    handler finishes writing the body -- so the write fails with
    `ConnectionResetError`. That is a normal consequence of the harness asking for a
    file that should not be there, and `ThreadingMixIn` reports it by calling
    `handle_error` on the *server*. Left alone, it prints a traceback to stderr and
    turns four passing tests into a wall of noise.

    Overridden on the class rather than assigned to an instance or to a
    `functools.partial`, because `socketserver` calls `self.handle_error` -- an
    attribute set on either of those would belong to the wrong object and be silently
    ignored.
    """

    def handle_error(self, request: object, client_address: object) -> None:
        """Swallow the disconnect. See the class docstring."""


@pytest.fixture(scope="module")
def served(dist: Path) -> Iterator[str]:
    """The built bundle, served over HTTP from a subpath. Yields the base URL.

    Served with a plain static handler rooted at the *parent* of `dist`, so the
    bundle genuinely lives at a nested path rather than at the server's root. That
    is the whole point: a handler rooted at `dist` would resolve `/assets/...`
    successfully and hide the bug.
    """
    site_root = dist.parent / "_site_for_test"
    if site_root.exists():
        shutil.rmtree(site_root)
    (site_root / MOUNT.strip("/")).mkdir(parents=True)
    for entry in dist.iterdir():
        target = site_root / MOUNT.strip("/") / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)

    server = _QuietServer(
        (HOST, 0),
        functools.partial(_QuietHandler, directory=str(site_root)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # The host is the literal passed above, so it is read from here rather than from
    # `server_address`. That attribute is typed as a bare tuple of `Any`, which makes
    # mypy treat the host as possibly-bytes and render it as `b'127.0.0.1'` -- a test
    # that passed would still be requesting a URL no browser could open. The port is
    # the only part the server chose, and 0 in the constructor means "any free port".
    port = int(server.server_address[1])
    try:
        yield f"http://{HOST}:{port}{MOUNT}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(site_root, ignore_errors=True)


@pytest.fixture(scope="module")
def dist() -> Path:
    """The built bundle, built on demand -- the same contract as the other module.

    `dist/` is gitignored, so on a fresh clone this produces it. That is the case
    worth testing, since it is the one where nothing is stale.
    """
    if not DIST.exists():
        if shutil.which("npm") is None:
            pytest.skip("npm is not available to build viewer/dist")
        subprocess.run(["npm", "run", "build"], cwd=REPO_ROOT / "viewer", check=True)
    return DIST


def fetch(url: str) -> tuple[int, bytes]:
    """The status and body a browser would get.

    Raises rather than returning the error, so a 404 fails at the call site with the
    URL visible in the traceback rather than as a bare assertion. Every URL this
    module opens is one it constructed itself, pointing at its own throwaway server
    on the loopback interface -- there is no user input and no remote host here.
    """
    with urlopen(url, timeout=10) as response:
        return int(response.status), response.read()


def status_of(url: str) -> int:
    """The status code alone, so a failing assertion can name the URL and the code
    rather than dumping a body that is not what went wrong."""
    try:
        with urlopen(url, timeout=10) as response:
            return int(response.status)
    except HTTPError as error:
        return int(error.code)
    except URLError as error:  # pragma: no cover - would mean the server is down
        pytest.fail(f"the test server did not answer {url}: {error}")


def workflow_body() -> str:
    """The deploy workflow with its comments removed.

    The assertions below are about what the workflow *does*, and a comment describing
    what it does is not that. This file is heavily commented on purpose -- the
    reasoning for the relative asset paths and for gating the deploy lives there --
    which means a naive substring search finds `make check` in prose several lines
    before the step that runs it. One of these tests passed for exactly that reason,
    against a workflow with the step deleted.

    So comments go first, and an assertion that fails afterwards is about behaviour.
    """
    return "\n".join(
        line
        for line in PAGES_WORKFLOW.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def references_in(html: str) -> list[str]:
    """Every URL the markup asks the browser to fetch.

    Scoped to what a browser acts on. A string that merely looks like a URL is not
    a request -- `data:,` for the favicon is not fetched from anywhere, and the
    `<noscript>` text is prose.
    """
    found = re.findall(r"(?:src|href)\s*=\s*[\"']([^\"']+)[\"']", html)
    return [value for value in found if not value.startswith(("data:", "#"))]


def test_the_page_loads_when_the_bundle_is_mounted_at_a_subpath(served: str) -> None:
    """The public link actually works, which is the whole of this criterion.

    A GitHub Pages project site is served from `/<repo>/`. If the built markup
    references its script and stylesheet site-absolute, every reader gets a blank
    page -- and nothing in the repository says so, because the files are all present
    and correct on disk. This is the test that would have caught it.
    """
    status, body = fetch(served)
    assert status == 200, f"the mounted page answered {status}"

    html = body.decode()
    references = references_in(html)
    assert references, "the built page references nothing, so this test checks nothing"

    for reference in references:
        # Resolved the way a browser resolves it: against the page's own URL. This is
        # the step that turns a correct file at a wrong URL into a visible failure.
        url = urljoin(served, reference)
        assert status_of(url) == 200, (
            f"the page asks for {reference!r}, which resolves to {urlparse(url).path} "
            f"and answered {status_of(url)}. The file is in the bundle, but the page "
            f"is asking for it at the wrong URL -- it would 404 wherever the site is "
            f"mounted below the domain root, which is how a GitHub Pages project "
            f"site is served."
        )


def test_the_page_markup_names_no_site_absolute_url(served: str) -> None:
    """The failure above, stated as a property, so the diagnosis is in the message.

    A `/`-rooted reference is the *cause*; the 404 is the symptom. Asserting the cause
    separately means a reader who trips this test does not have to work out why a
    present file 404'd.

    The root-relative case is the one that breaks. `/runs.json` would be wrong in the
    same way, and is refused for the same reason.
    """
    _, body = fetch(served)
    html = body.decode()

    for reference in references_in(html):
        assert not reference.startswith("/"), (
            f"the built page references {reference!r}, which is relative to the "
            f"domain root rather than to the page. Correct wherever the site is "
            f"served from `/`, broken everywhere else."
        )


def test_the_page_loads_its_own_data_from_the_mounted_path(served: str) -> None:
    """The three documents the page fetches resolve under the mount too.

    Distinct from the markup check because these URLs are *in the script*, not in the
    HTML, so nothing about `dist/index.html` would reveal them. They are relative
    already -- which is the design working -- and this is what keeps them that way.
    A future `import.meta.env.BASE_URL` or an absolute path in a fetch would pass
    every other test in the repository.
    """
    index_status, index_body = fetch(urljoin(served, "runs.json"))
    assert index_status == 200

    # The run the page opens on, taken from the index it would open on.
    entries: list[dict[str, Any]] = json.loads(index_body.decode())["runs"]
    assert entries, "the bundle's index lists no runs, so this test checks nothing"

    documents = [
        "runs.json",
        "comparison.json",
        f"runs/{entries[0]['run_id']}.json",
        entries[0]["audio_path"],
    ]

    for document in documents:
        url = urljoin(served, document)
        assert status_of(url) == 200, (
            f"the page fetches {document!r}, which resolves to {urlparse(url).path} "
            f"and answered {status_of(url)} under the mount {MOUNT!r}."
        )


def test_the_mount_is_not_a_root_and_the_test_would_notice(served: str) -> None:
    """The harness itself is checked, because a test that cannot fail is worse than
    no test -- it reads as coverage.

    The way this module could go wrong is quietly: if the static handler were rooted
    at `dist` itself, or if a stale copy from a previous run were left at the domain
    root, then `/assets/...` would resolve and every assertion above would pass while
    the real deployment 404s. So the negative case is asserted directly: a site
    absolute path must fail *from this same server*.

    This is the check that makes the other three trustworthy. If it ever goes red,
    the bug is in the harness and the results above mean nothing.
    """
    # The page is fetched so that "the harness works" is established against a real
    # response rather than assumed.
    fetch(served)

    # The markup check above already refuses a site-absolute reference, so re-asserting
    # it here was the same assertion twice in one module. What this test adds is the
    # harness's own teeth: with the markup clean, prove the server would still have
    # rejected a root-relative path. A 404 means the server is checking paths; a 200
    # means it would have rubber-stamped the bug and every other test here is theatre.
    nonexistent = urljoin(served, "assets/definitely-not-a-real-bundle-file.js")
    assert status_of(nonexistent) == 404, (
        "the test server answered 200 for a file that does not exist, so it is not "
        "checking paths and every assertion in this module would pass regardless"
    )


# --- What gets published, and how ----------------------------------------------


def test_the_deploy_workflow_publishes_the_built_bundle() -> None:
    """The workflow uploads `viewer/dist`, which is the only artifact it can.

    Checked as a pair, because either half alone is plausible and wrong together: an
    upload path of `.` would publish the whole repository -- including `evidence/`,
    `fixtures/`, and the git-tracked `runs/` shape -- and a workflow that never runs
    `make build` would upload whatever happened to be on the runner, which is nothing.
    """
    workflow = workflow_body()

    assert PAGES_WORKFLOW.exists(), (
        f"{PAGES_WORKFLOW.relative_to(REPO_ROOT)} is missing, so nothing is published and "
        "the README's public link is a claim with nothing behind it"
    )
    assert "viewer/dist" in workflow, (
        "the workflow does not name viewer/dist as the uploaded path"
    )
    assert "make build" in workflow, (
        "the workflow never runs `make build`, so it has no bundle to upload. The "
        "directory is gitignored and does not exist on a fresh runner."
    )


def test_the_deploy_runs_the_gate_before_it_publishes() -> None:
    """Nothing is published from a commit that fails `make check`.

    The bundle is the first thing a maintainer sees and the README's figures rest on
    it, so publishing it is publishing a claim. The check is asserted to be *before*
    the upload step, not merely present: a workflow with `make check` after the upload
    would publish first and then decide whether it was any good, and `continue-on-error`
    anywhere would defeat it outright.
    """
    workflow = workflow_body()

    assert "make check" in workflow, (
        "the deploy workflow does not run the gate, so a broken build would publish"
    )
    assert workflow.index("make check") < workflow.index("upload-pages-artifact"), (
        "the deploy workflow runs the gate after the upload step"
    )
    assert "continue-on-error" not in workflow, (
        "the deploy workflow tolerates a failing step, which would let it publish "
        "past the gate"
    )


def test_the_deploy_cannot_spend_api_credit() -> None:
    """No credential is available to the workflow, so the published bundle is derived.

    `make build` rebuilds from the committed records and clips, which is what makes
    the public artifact free to produce and free to view. If this workflow gained a
    key, `make matrix` would become reachable from CI and every push would cost money
    on someone's account.

    Wide on purpose: any mention of the variable at all, not just an assignment. The
    deploy workflow should not be touching it under any spelling.
    """
    workflow = workflow_body()

    assert "ELEVENLABS" not in workflow, (
        "the deploy workflow references the API credential; the published bundle is "
        "derived from committed evidence and must never be captured in CI"
    )


def test_the_workflow_caches_a_lockfile_that_exists() -> None:
    """`npm ci` needs `viewer/package-lock.json`, and it is committed.

    `npm ci` fails outright on a missing or stale lockfile, which would fail the
    deploy rather than degrade it -- so the file being tracked is the thing that
    matters, and a pattern typo in the cache path is the quiet version of the same
    mistake.
    """
    workflow = workflow_body()

    if "cache-dependency-path" in workflow:
        declared = re.search(r"cache-dependency-path:\s*(\S+)", workflow)
        assert declared is not None, "cache-dependency-path is set to nothing"
        resolved = REPO_ROOT / declared.group(1)
        assert resolved.exists(), (
            f"the workflow caches against {declared.group(1)}, which does not exist"
        )

    assert "npm ci" in workflow, (
        "the deploy installs with something other than `npm ci`, so a runner could "
        "resolve different dependency versions than the tests ran against"
    )

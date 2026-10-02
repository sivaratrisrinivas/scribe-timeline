"""A fresh clone must reach a working state, and stay free once it does.

The claim this module checks is the one a reader acts on first: `git clone`, `make
setup`, and the project works. It is easy to state in a README and easy to break
quietly, because everything that breaks it -- a missing dev dependency, a target
that reads a file another target builds, an output directory that is not ignored --
only shows up from a clean checkout, and CI usually has a warm one.

So the properties are asserted directly:

* **The gate builds what it needs.** `make check` runs the viewer's tests, which
  read the exported bundle, so it depends on the export. Asserted on the Makefile
  rather than by running it, because running `make check` from inside `make check`
  is not possible and running it here would make the test suite pay for linting and
  typechecking.
* **A paid rerun cannot be committed by accident.** The live capture writes to
  `runs/`, which is gitignored. This is the mechanism behind "paid reruns stay
  local", and it fails the moment someone adds `evidence` to the ignore list or
  unanchors the pattern so it also swallows the published records.
* **Credentials are never committable.** `.env` and its siblings are ignored, with
  the example file explicitly not.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile"
GITIGNORE = REPO_ROOT / ".gitignore"


def makefile() -> str:
    return MAKEFILE.read_text()


def target_recipe(name: str) -> str:
    """The recipe lines of one Make target, tab-indented lines included.

    A recipe can only start on a tab-indented line, so requiring one is what
    distinguishes "these are the commands" from "these are comments about the
    target". Without it a comment above a rule would be read as the rule.
    """
    lines: list[str] = []
    in_target = False
    for raw in makefile().splitlines():
        if re.match(rf"^{re.escape(name)}\s*:", raw):
            in_target = True
            continue
        if not in_target:
            continue
        if not raw.startswith("\t"):
            break
        lines.append(raw)
    assert lines, f"the Makefile has no `{name}` recipe"
    return "\n".join(lines)


def ignored_paths() -> list[str]:
    """Every ignore pattern in the repository's `.gitignore`, comments dropped."""
    return [
        line.strip()
        for line in GITIGNORE.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def is_ignored(path: str) -> bool:
    """Whether git itself considers `path` untracked-and-ignored.

    Asked of git rather than of a hand-rolled matcher, so the answer is the one that
    governs an actual `git add`. A pattern this module's own reasoning called
    ignoring but git does not would be a real leak.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


# --- The gate builds what it reads ---------------------------------------------


def target_prerequisites(name: str) -> list[str]:
    """The prerequisites of one Make target, in the order make will run them.

    Parsed from the source rather than from `make -n`, because a test cannot run
    `make check` from inside `make check` -- and because the question here is about
    the declared order, which is the thing that can be wrong.

    A `\\`-continued rule line is joined first, the way make reads it, so a target
    split across two lines yields the same list make would. Without that, the
    continuation would parse as a prerequisite named `\\` and the comparison below
    would be against nonsense -- which fails loudly, but for the wrong reason.
    """
    lines = makefile().splitlines()
    joined: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        while line.endswith("\\") and index + 1 < len(lines):
            index += 1
            line = f"{line[:-1].rstrip()} {lines[index]}"
        joined.append(line)
        index += 1

    in_target = False
    rule: list[str] = []
    for raw in joined:
        if re.match(rf"^{re.escape(name)}\s*:", raw):
            in_target = True
            rule.append(raw)
            continue
        if in_target:
            if raw.startswith("\t"):
                continue  # a recipe, not a rule; the caller wants the other half
            break
    assert rule, f"the Makefile has no `{name}` target"

    # Only the rule line carries prerequisites. The tab-indented lines that follow are
    # recipe commands, and make passes those to the shell rather than treating them
    # as targets -- so a recipe line naming a target is not a dependency, and
    # including them here would make the order comparison meaningless.
    _, _, prerequisites = rule[0].partition(":")
    return prerequisites.split()


def test_the_gate_builds_the_bundle_before_the_tests_that_read_it() -> None:
    """`make check` on a checkout that has the dependencies but no bundle.

    That is the state `make setup` leaves you in if the bundle is deleted, and the
    one a CI runner that cached `node_modules` but not `viewer/public/` would be in.
    The viewer's `ConditionTable` tests read `viewer/public/comparison.json` and
    `runs.json`, both generated and gitignored, so without the export the gate fails
    for a reason that has nothing to do with the code under test -- which reads as a
    broken project rather than a missing build step.

    Not asserted for a truly fresh clone, which has no `.venv` either and would fail
    at `uv run` long before reaching the viewer. What a fresh clone needs is
    `make setup`, and that is a separate claim.

    Order is the substance of it. Make runs prerequisites left to right, so naming
    `viewer-export` anywhere in the list is not enough: after `viewer-test` it would
    be a declaration of good intentions. A fresh clone is the case that catches it,
    because only there is the bundle missing.
    """
    prerequisites = target_prerequisites("check")

    assert "viewer-export" in prerequisites, (
        f"`check` does not build the bundle its viewer tests read: {prerequisites}"
    )
    assert prerequisites.index("viewer-export") < prerequisites.index("viewer-test"), (
        f"`check` builds the bundle after the tests that read it: {prerequisites}. "
        f"Make runs prerequisites left to right, so a checkout with no bundle fails "
        f"the gate for want of files the gate itself should have built."
    )


def test_setup_runs_the_export_the_gate_also_runs() -> None:
    """One export, two callers, and neither can skip it.

    Asserted on the recipe rather than on a prerequisite, because `setup` invokes it
    through `$(MAKE)`: a `viewer-export` listed as a prerequisite would run in a
    separate make invocation, and a failure in it would not stop the rest of setup.
    """
    recipe = target_recipe("setup")

    assert "$(MAKE) viewer-export" in recipe, (
        f"`setup` does not build the bundle; the viewer would have nothing to serve: {recipe}"
    )


def test_the_export_needs_no_credential() -> None:
    """`viewer-export` is on the path of both `setup` and `check`.

    So if it ever needed a key, the promise in the README -- that a reader can get
    the whole thing working without an account -- would be false, and the failure
    would land on someone who has no key to offer.

    Asserted on the command rather than on the script's internals, because
    `tests/test_offline_viewing.py` proves the real thing: that running the export
    with the key deleted *and reading it made fatal* still produces the bundle. This
    is the cheap guard that the Makefile did not grow a key-setting step in front of
    it, which would defeat that test without failing it.
    """
    recipe = target_recipe("viewer-export")

    assert "scripts/export_viewer.py" in recipe
    # Wide on purpose: any mention of the variable at all, not just an assignment,
    # because a `!ELEVENLABS_API_KEY` or a `--key $(...)` would be a step in front of
    # an export that is supposed to need nothing.
    assert "ELEVENLABS" not in recipe, (
        f"`viewer-export` touches the credential; `check` runs it, so the gate would "
        f"start requiring a key: {recipe}"
    )


# --- A paid rerun stays local ---------------------------------------------------


def test_live_capture_output_is_not_committable() -> None:
    """`runs/` is where a capture writes, and it must be ignored.

    A capture costs money and produces a record whose credential-adjacent details
    have not been reviewed. Committing one by accident would publish unreviewed
    output, so the ignore is the mechanism, and it is checked against git rather
    than against a reading of the pattern.
    """
    assert is_ignored("runs/2026-10-02T00-00-00Z__vad_1__rep0.json"), (
        "a live capture's run record would be committable; a paid rerun has to stay local"
    )


def test_the_ignore_does_not_also_swallow_the_published_evidence() -> None:
    """The opposite failure: an over-broad pattern that hides the evidence.

    A bare `runs/` would match `evidence/runs/` as well, and the published records
    would stop being committed while still being read by the README's numbers and
    the viewer's bundle. The result is a repository whose claims rest on files no
    clone has. So both sides are asserted, and the anchoring is what separates them.
    """
    assert "/runs/" in ignored_paths(), (
        "the ignore pattern for the live capture directory is not anchored to the "
        "repository root, so it also hides evidence/runs/"
    )
    assert not is_ignored("evidence/runs/2026-10-01T21-09-41Z__vad_0__rep0.json"), (
        "a published run record is ignored; the README's numbers would rest on "
        "files no clone has"
    )


def test_the_derived_viewer_bundle_is_not_committed() -> None:
    """`viewer/public/` and `viewer/dist/` are derived and are about 7 MB.

    Derived from committed files, so committing them would add a second copy that
    can disagree with the evidence. The export rebuilds them, and `check` does it
    on the way past.
    """
    assert is_ignored("viewer/public/runs.json")
    assert is_ignored("viewer/dist/index.html")


# --- Credentials are never committable -----------------------------------------


def test_a_local_env_file_is_not_committable() -> None:
    assert is_ignored(".env"), "the file a reader puts their key in would be committable"


def test_the_env_example_is_committable() -> None:
    """The example must stay tracked, or the setup instructions point at nothing.

    This is the other side of the same rule, and the two are asserted together
    because the failure is always a pair: an `.env*` rule broad enough to cover
    `.env.example` is the obvious way to write it, and it silently removes the only
    documentation of the variable name from the repository.
    """
    assert not is_ignored(".env.example"), (
        ".env.example is ignored; the setup instructions would name a file no "
        "clone has"
    )


def test_every_env_file_variant_is_ignored() -> None:
    """`.env` alone is not enough; local variants are the same secret.

    A `.env.local` or `.env.production` holding the same key is committed as
    readily as a `.env`, and the pattern that covers only the exact name leaves it
    exposed.
    """
    for name in (".env.local", ".env.production", ".env.test"):
        assert is_ignored(name), f"{name} would be committable, and it would hold the same key"

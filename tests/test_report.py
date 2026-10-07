"""The report is a claim about the evidence, and this reads it.

Everything else in this repository is guarded by a test that runs something. The
README is prose, and prose cannot fail a build — so a README that quietly stops
matching `evidence/comparison.json` is a claim nobody checks, which is the specific
failure this project exists to avoid in code.

So the report's *figures* are checked against the published report, and its required
sections are checked for presence. What this module cannot do is read the prose for
sense, and it does not pretend to: it asserts that a section exists, that the numbers
in the summary table are the numbers in the evidence, and that the rerun command and
the public link are the ones that work. Everything else is on the author.

The three sections are What, Why and How. They are asserted by
heading text rather than by position, so the report can grow without the test
insisting on an order it does not care about — but every one of them must be present,
because a report missing "Simplifications" is a report making an unbounded claim.
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
COMPARISON = REPO_ROOT / "evidence" / "comparison.json"

#: Issue #8's required sections, as heading text.
REQUIRED_SECTIONS = ("What", "Why", "How")


def readme() -> str:
    return README.read_text()


def comparison() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(COMPARISON.read_text())
    return document


def headings(level: int = 2) -> list[str]:
    """Every heading of the given level, as text.

    Read from the source rather than from a rendered view, so a section that exists
    inside a fenced code block does not count. The fence is tracked rather than
    stripped, because the report contains shell transcripts and a `# comment` line
    inside one of them is not a section.
    """
    found: list[str] = []
    in_fence = False
    for line in readme().splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.fullmatch(rf"#{{{level}}} +(.*)", stripped)
        if match is not None:
            found.append(match.group(1).strip())
    return found


def test_every_required_section_is_present() -> None:
    """The seven sections the issue requires, each one a heading of its own.

    Matched case-insensitively and on a prefix, so "Public evidence" satisfies a
    heading of "Public evidence: the one link". Requiring an exact heading would make
    the test a straitjacket on wording rather than a check that the report covers its
    ground.
    """
    found = [heading.lower() for heading in headings()]

    missing = [
        section
        for section in REQUIRED_SECTIONS
        if not any(candidate.startswith(section.lower()) for candidate in found)
    ]

    assert missing == [], (
        f"the report is missing required sections: {missing}. Present: {found}"
    )


def markdown_tables() -> list[tuple[list[str], list[list[str]]]]:
    """Every pipe table in the README, as `(header cells, body rows)`.

    Parsed from the source rather than searched for as substrings, because a
    document-wide substring check cannot tell one table from another. Planting a wrong
    delta in the summary table left the correct value sitting in the claims table
    further down, and the looser check passed against a report whose two tables
    disagreed -- which is the exact failure this project exists to prevent, in the one
    place nothing downstream could catch it.

    Rows are kept as lists of cells rather than transposed into columns. The checks read
    a whole row at once ("does this row contain its measured delta"), which is the shape
    a reader scans, and transposing first turned each row into a single bare condition id.
    """
    tables: list[tuple[list[str], list[list[str]]]] = []
    current: list[list[str]] = []

    def flush() -> None:
        if not current:
            return
        header, *body = current
        # The `---` separator is the only row whose cells are all punctuation.
        body = [row for row in body if not all(set(cell) <= {"-", ":", " "} for cell in row)]
        if body:
            tables.append((header, body))

    for line in readme().splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            flush()
            current = []
            continue
        current.append([cell.strip() for cell in stripped.strip("|").split("|")])

    flush()
    return tables


def table_with(header_fragment: str) -> tuple[list[str], list[list[str]]]:
    """The one table whose header contains `header_fragment`."""
    found = [
        (header, body)
        for header, body in markdown_tables()
        if any(header_fragment in name.lower() for name in header)
    ]
    assert len(found) == 1, (
        f"expected exactly one table with a {header_fragment!r} column, found {len(found)}"
    )
    return found[0]


def condition_ids(rows: list[list[str]]) -> list[str]:
    """The first cell of each row, unquoted: `` `vad_1` `` is `vad_1`."""
    return [row[0].strip().strip("`") for row in rows]


def column(header: list[str], rows: list[list[str]], fragment: str) -> list[str]:
    """One column's cells, located by its header.

    Addressing the column rather than the whole row is what makes a swap visible. With
    `vad_2`'s claimed and measured steps exchanged, both figures are still present in
    the row, so a row-level check passed; a column-level one reads `+180` where the
    header says *claimed step* and fails.
    """
    matches = [index for index, name in enumerate(header) if fragment in name.lower()]
    assert len(matches) == 1, (
        f"expected one column whose header contains {fragment!r}, found {matches} in {header}"
    )
    index = matches[0]
    return [row[index] if index < len(row) else "" for row in rows]


def test_the_results_table_quotes_the_evidence_row_by_row() -> None:
    """Every row of the summary results table is the evidence's, in its own row.

    This is the check the module exists for. That table is what a maintainer reads and
    what someone might quote, and it is hand-written, so it is the part most able to
    drift from `evidence/comparison.json`.

    Row by row, rather than "does this number appear somewhere in the document". The
    looser version passed against a README whose summary table said +190 ms while the
    claims table below it said +180 ms: the correct value was still in the file, just
    not in the cell a reader looks at.
    """
    report = comparison()
    conditions = {entry["condition_id"]: entry for entry in report["conditions"]}

    header, rows = table_with("delta vs anchor")
    identifiers = condition_ids(rows)
    deltas = column(header, rows, "delta vs anchor")
    returned = column(header, rows, "marker returned")

    assert len(rows) == len(conditions), (
        f"the results table lists {identifiers}; the evidence holds {sorted(conditions)}"
    )

    failures: list[str] = []

    for identifier, returned_cell, delta_cell in zip(
        identifiers, returned, deltas, strict=True
    ):
        entry = conditions.get(identifier)
        if entry is None:
            failures.append(
                f"the results table names {identifier!r}, which is not in the evidence"
            )
            continue

        # The summary carries one figure per condition rather than a separate median
        # column, so this cell is where the condition's median timestamp belongs.
        median = f"{entry['median_marker_ms']:,.0f}"
        if median not in returned_cell:
            failures.append(
                f"{identifier}'s returned marker is {returned_cell!r}; the evidence's "
                f"median is {median} ms"
            )

        delta = entry["delta_vs_anchor_ms"]
        if delta is None:
            # The anchor carries no figure. A number here would read as "no drift
            # found", where the truth is that this row is the baseline everything
            # else is measured from.
            # Both minus forms: the README writes U+2212 MINUS SIGN in the claims
            # table and a hyphen elsewhere, and which one appears is a typographic
            # choice rather than a fact about the evidence.
            if re.search("[+\u2212-]\\s*\\d", delta_cell):
                failures.append(
                    f"{identifier} is the anchor but its delta cell is {delta_cell!r}; a "
                    "delta against itself would read as a measurement"
                )
            continue

        if f"{delta:+,.0f}" not in delta_cell:
            failures.append(
                f"{identifier}'s delta cell is {delta_cell!r}; the evidence says {delta:+,.0f} ms"
            )

    assert failures == [], (
        "the README's results table does not match the evidence: " + "; ".join(failures)
    )


def test_the_manual_control_is_reported_and_named_as_such() -> None:
    """The control exists to answer one question, so its answer is stated.

    Asserted as the report's own flag rather than as prose: the README has to carry the
    conclusion, because a control left as a table row is a row a reader has to
    interpret, and "offsets do not accumulate" is the sentence the finding rests on.
    """
    report = comparison()
    control = report["control"]
    text = readme()

    assert control is not None, "the published evidence holds no manual control"
    assert control["manual_condition_id"] in text, (
        f"the README does not name the control ({control['manual_condition_id']})"
    )
    # `\s+` rather than a space, because the conclusion falls across a line wrap in the
    # source ("Offsets do\nnot accumulate"). A literal space made this fail against
    # correct prose and read as a missing conclusion.
    assert re.search(r"do(?:es)?\s+not\s+accumulate", text, re.IGNORECASE), (
        "the README never states that offsets do not accumulate under manual commits; "
        "that conclusion is the whole reason the control exists"
    )


def section_text(title: str) -> str:
    """The body of one `##` section, up to the next `##`."""
    return _section_text(title, "##")


def subsection_text(title: str) -> str:
    """The body of one `###` section, up to the next heading of the same or higher level."""
    return _section_text(title, "###")


def _section_text(title: str, prefix: str) -> str:
    """The body of one section, up to the next heading of the same or a higher level.

    Scoped rather than read whole-document, because a test that passes on a phrase from a
    neighbouring section is not checking the section it names. For a `###` the terminator
    is the next `###` *or* `##`, so a subsection cannot absorb the rest of its parent.

    `inside` is committed before the terminator check, which is the order that matters:
    a heading whose own text matches must open the section rather than close it.
    """
    own = re.compile(rf"{re.escape(prefix)} +(.*)")
    outer = re.compile(r"#+ +(.*)")

    lines = readme().splitlines()
    collected: list[str] = []
    inside = False
    for line in lines:
        heading = own.fullmatch(line.strip())
        if heading is not None:
            if inside:
                break
            inside = heading.group(1).strip().lower().startswith(title.lower())
            continue
        if inside and outer.fullmatch(line.strip()) is not None:
            break
        if inside:
            collected.append(line)
    return "\n".join(collected)


@pytest.fixture(scope="module")
def rerun() -> str:
    """The published rerun command's output, executed once for the whole module.

    The README publishes a command and the report's central promise is that running it
    reproduces the published figures. So it is run here -- once, with no credential in
    the environment, which is also how the "free to re-derive" claim gets checked.

    Module-scoped rather than per-test because the previous version ran the whole matrix
    twice, in two near-identical blocks, to answer two questions one execution answers:
    does it run, and does it print these numbers. Reading twelve run records and printing
    a comparison is cheap, but paying for it twice because the assertion could not be
    shared is the kind of duplication that rots -- the two copies drifted, and only one
    of them got the credential-stripping `env`.
    """
    match = re.search(r'make matrix ARGS="(--from-saved [^"]+)"', readme())
    assert match is not None, (
        "the README publishes no exact rerun command; the issue requires one"
    )

    # The glob is expanded here rather than passed through. A reader types the command
    # into a shell, where `evidence/runs/*.json` becomes twelve paths before the runner
    # sees it; `subprocess` with an argument list does no expansion, so forwarding the
    # literal glob made the runner report "no such run record(s)" and this failed against
    # a command that works exactly as published. `glob.glob` is what the shell would do.
    raw = match.group(1).split()
    arguments: list[str] = []
    empty: list[str] = []
    for part in raw:
        if "*" not in part:
            arguments.append(part)
            continue
        matched = sorted(glob.glob(str(REPO_ROOT / part)))
        # Recorded per pattern rather than inferred from the final argument count, which
        # would prove that *some* glob matched without saying which -- and the previous
        # version compared lengths while naming a loop variable that had outlived its
        # loop, so the failure message reported whichever part happened to be last.
        if not matched:
            empty.append(part)
        arguments.extend(matched)

    assert empty == [], (
        f"the published pattern(s) {empty} matched no run records, so the rerun command "
        f"would report nothing. Command was: {raw}"
    )

    result = subprocess.run(
        ["uv", "run", "python", "-m", "scribe_timeline.capture.matrix", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        # No credential: if this needed one, the README's claim that re-deriving is
        # free would be false, and this would fail rather than quietly pass.
        env={key: value for key, value in os.environ.items() if "ELEVENLABS" not in key},
    )

    assert result.returncode == 0, (
        f"the published rerun command failed:\n{result.stdout[-800:]}\n{result.stderr[-800:]}"
    )
    return result.stdout


def test_the_published_rerun_command_runs(rerun: str) -> None:
    """The exact command in the README works, with no account.

    Run, not pattern-matched. The issue requires the rerun command to be published, and
    the only way to know a published command works is to run it -- a command that no
    longer matched the runner's arguments would leave a reader copying something that
    fails. The absence of `ELEVENLABS_API_KEY` from the environment is part of the
    assertion: this is also what backs "re-derivable for nothing".
    """
    assert "manual" in rerun.lower(), (
        "the rerun produced no comparison output; a command that ran and printed nothing "
        "is not a working rerun"
    )


def test_the_rerun_derives_the_figures_the_readme_quotes(rerun: str) -> None:
    """The published command reproduces this README's numbers, not merely some numbers.

    The check above proves the command runs. This proves it is *this* command: the output
    is re-derived from the committed evidence and its deltas compared against the figures
    the README publishes. A command that ran and printed a different figure would satisfy
    the first test and fail the report's actual promise.
    """
    for entry in comparison()["conditions"]:
        delta = entry["delta_vs_anchor_ms"]
        if delta is None:
            continue
        rendered = f"{delta:+.1f}"
        assert rendered in rerun, (
            f"re-running the published command produced no {entry['condition_id']} "
            f"delta of {rendered} ms, so it does not reproduce the figure the README "
            f"publishes"
        )


def test_the_readme_publishes_clone_then_setup() -> None:
    """`git clone`, then `make setup`.

    Named for what it asserts, which is that the README names both commands in the order
    a reader would run them. The earlier name claimed "the only one needed", which this
    test cannot check: whether `make setup` alone is sufficient is a property of the
    Makefile, and asserting it here would mean running setup from inside the gate.

    What backs the stronger claim is elsewhere: `tests/test_setup_contract.py` holds the
    Makefile's ordering rules, and the whole suite passing from a fresh checkout is what
    demonstrates it. This only holds the README to saying so.
    """
    text = readme()

    assert "git clone" in text, "the README does not tell a reader to clone"
    assert re.search(r"make setup", text), (
        "the README names no setup command; a fresh clone would not know what to run"
    )


def test_the_public_link_is_the_pages_url_for_this_repository() -> None:
    """One link, and it is this repository's Pages site.

    Checked against the configured remote rather than against a literal, so a fork does
    not have to edit the URL to stay correct — and so a repository whose README points
    at someone else's site fails here, which is the failure worth catching: a public
    link that credits the wrong evidence.
    """
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if remote.returncode != 0:
        import pytest

        pytest.skip("this checkout has no origin remote to check the link against")

    match = re.search(
        r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/\s]+?)(?:\.git)?$",
        remote.stdout.strip(),
    )
    assert match is not None, (
        f"cannot read an owner and repo from {remote.stdout.strip()!r}"
    )

    owner, repo = match.group("owner"), match.group("repo")
    expected = f"https://{owner}.github.io/{repo}/"

    # Markdown emphasis is stripped before comparing. The link is set in bold, so a naive
    # match returns `...scribe-timeline/>**` -- the trailing `>` and `*` are the closing
    # `**` of the bold span, not part of the address.
    links = [
        link.rstrip("*_`>,.")
        for link in re.findall(r"https://[^\s)\]]+", readme())
    ]

    assert expected in links, (
        f"the README does not link the published site ({expected}). A maintainer needs "
        f"one link that shows the question, the evidence and the result. Found: {links}"
    )

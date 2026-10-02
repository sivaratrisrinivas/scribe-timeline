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

The seven sections come from issue #8's acceptance criteria. They are asserted by
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
REQUIRED_SECTIONS = (
    "Problem",
    "Public evidence",
    "What I built",
    "Reproduce",
    "Results",
    "Simplifications",
    "Next experiment",
)


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


def test_the_claimed_steps_table_quotes_the_reported_claim() -> None:
    """The table comparing measured against claimed steps agrees with the report.

    The companion to the check above, and separately asserted because the two tables can
    drift independently: one is written from the measured deltas, the other from the
    report's own claim comparison.
    """
    report = comparison()
    expected = {claim["condition_id"]: claim for claim in report["claims"]}

    header, rows = table_with("claimed step")
    identifiers = condition_ids(rows)
    claimed = column(header, rows, "claimed step")
    measured = column(header, rows, "measured step")

    failures: list[str] = []
    for identifier, claimed_cell, measured_cell in zip(
        identifiers, claimed, measured, strict=True
    ):
        claim = expected.get(identifier)
        if claim is None:
            failures.append(
                f"the claims table names {identifier!r}, which the report does not compare"
            )
            continue
        for description, cell, value in (
            ("claimed", claimed_cell, claim["claimed_step_ms"]),
            ("measured", measured_cell, claim["measured_step_ms"]),
        ):
            if f"{value:+,.0f}" not in cell:
                failures.append(
                    f"{identifier}'s {description} step is {cell!r}; the report says "
                    f"{value:+,.0f} ms"
                )

    assert failures == [], (
        "the README's claims table does not match the evidence: " + "; ".join(failures)
    )


def test_the_reported_claim_is_quoted_as_the_report_holder_states_it() -> None:
    """The original report's own figures appear, and are attributed to it.

    Both halves. A report that states its own numbers without the reported ones leaves
    a reader unable to tell whether this confirms, refutes, or is about something else —
    and the comparison's whole value is that it is a check of a *specific* claim.
    """
    report = comparison()
    text = readme()

    assert report["claims_source"] in text, (
        f"the report does not name its source ({report['claims_source']}); the figures "
        "are presented as this project's own rather than as a check of a claim"
    )
    assert str(report["claims_filed"]) in text, (
        f"the report does not date the claim ({report['claims_filed']}), so a reader "
        "cannot tell how current the finding it is checking is"
    )

    # The claim's own offsets, which are a different quantity from the deltas and are
    # stated in the README's prose rather than its table.
    for condition_id, offset in report["reporter_claimed_offset_ms"].items():
        rendered = f"{offset:+,.0f}"
        assert rendered in text, (
            f"the reported claim's own {condition_id} offset ({rendered} ms) is not in "
            "the README; the two quantities are stated differently on purpose, and "
            "omitting the reported one leaves the comparison unexplained"
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


def test_the_simplifications_are_the_ones_the_issue_names() -> None:
    """Five boundaries of the claim, each named explicitly.

    Issue #8 requires one model, one language, synthetic audio, controlled streaming,
    and no claim about production prevalence. Each is checked for the phrase that
    would carry it, so a section that said "narrow scope" without saying what is
    narrow would fail.

    This is the criterion most easily satisfied by accident — a report can mention each
    of these in passing across five paragraphs and still leave a reader unsure what is
    being claimed. Hence one test, five assertions.
    """
    section = section_text("Simplifications")

    expectations = {
        # The model is named with its underscore (`scribe_v2_realtime`) because that is
        # the identifier the API and the code both use; a pattern written with a space
        # would only match prose that spelled it differently from every other document
        # in the repository.
        "one model": r"scribe[_ ]v2[_ ]realtime",
        "one language": r"\benglish\b",
        "synthetic audio": r"synthetic",
        "controlled streaming": r"controlled|real-time pacing|not production throughput",
        "no claim about production prevalence": r"no claim|not a measurement of|cannot support",
    }

    missing = [
        description
        for description, pattern in expectations.items()
        # Case-insensitive throughout: these are headings in a bulleted list, so
        # "No claim" is capitalised and a lowercase-only pattern misses it.
        if re.search(pattern, section, re.IGNORECASE) is None
    ]

    assert missing == [], (
        f"the Simplifications section does not state: {missing}. A report that bounds "
        "its claim nowhere is an unbounded claim."
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


def test_the_report_says_what_would_falsify_it() -> None:
    """A claim with no stated falsifier is not a claim, it is an assertion.

    Issue #8 requires the report to say what would falsify the finding. Checked for the
    section and for at least one concrete condition — a section headed "what would
    falsify this" that listed no observations would satisfy a heading check alone, which
    is the failure this is guarding against.
    """
    assert any(
        heading.lower().startswith("what would falsify") for heading in headings()
    ), f"the report has no 'What would falsify this' section: {headings()}"

    section = section_text("What would falsify").lower()

    assert len(section.split()) > 80, (
        "the falsification section is too short to state a condition; a heading alone "
        "is not a falsifier"
    )
    # At least two concrete observations that would count against the finding.
    assert len(re.findall(r"^- ", section, re.MULTILINE)) >= 2, (
        "the falsification section lists fewer than two concrete observations that "
        "would count against the finding"
    )


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


def test_the_report_states_the_one_figure_it_could_not_reproduce() -> None:
    """The +9 ms floor is named, and the disagreement with it is not hidden.

    The spec behind this work singles the reported +9 ms zero-commit floor out as the
    figure that matters most — a constant baseline shift is a different and stronger
    finding than per-commit drift. This rerun measures +200 ms for the same quantity, and
    that disagreement is unresolved.

    A report that reproduces a finding cleanly has no such passage. One that does not, and
    does not say so, is the failure mode this project is built against: a missing value
    quietly rendered as agreement. So the README is required to name both figures, and to
    say plainly that they disagree.
    """
    report = comparison()
    context = report["context"]
    anchor_id = report["anchor_condition_id"]
    anchor = next(
        entry for entry in context["conditions"] if entry["condition_id"] == anchor_id
    )
    measured_floor = anchor["insertion_point_gap_ms"]
    reported_floor = report["reporter_claimed_offset_ms"]["vad_0"]

    assert reported_floor != measured_floor, (
        "this test is about an unresolved disagreement, and the two floors now agree — "
        "if that is real, the README's discussion of it needs rewriting"
    )

    section = section_text("The +9 ms floor").lower()
    if not section:
        # `###` rather than `##`: this passage is a subsection of Results, and reading it
        # at the wrong level would return the whole document and quietly pass every
        # assertion below.
        section = subsection_text("The +9 ms floor").lower()
    assert section, (
        f"the README has no section on the reported {reported_floor:+,.0f} ms floor. "
        f"This run measured {measured_floor:+,.0f} ms for the same quantity and does not "
        "reconcile the two."
    )
    assert f"{measured_floor:+,.0f}" in section, (
        f"the section does not state this project's own figure ({measured_floor:+,.0f} ms)"
    )
    assert re.search(r"disagree|does not reproduce|not reproduce", section), (
        "the section states both numbers without saying they disagree"
    )
    assert re.search(r"open|not resolved|not obviously a contradiction", section), (
        "the section must not present the disagreement as settled"
    )


def test_the_report_does_not_overstate_the_per_commit_step() -> None:
    """No figure is claimed as the per-commit step that no condition measures.

    The evidence measures +100 ms over one preceding commit and +180 ms over two. Neither
    is a measurement of a per-commit step; dividing one by its commit count is arithmetic
    on a measurement, and the two give different answers -- 100 ms and 90 ms.

    So a bare "100 ms per commit" is not a figure this evidence holds, and asserting
    "about 100 ms per preceding commit" without saying where it comes from overstates
    what was measured. Required instead: the two measured deltas, and the inference marked
    as one.
    """
    report = comparison()
    deltas = {
        entry["condition_id"]: entry["delta_vs_anchor_ms"]
        for entry in report["conditions"]
        if entry["delta_vs_anchor_ms"] is not None and entry["commit_strategy"] == "vad"
    }

    opening = readme().split("## Problem", 1)[0]

    assert re.search(r"per commit|per preceding", opening, re.IGNORECASE), (
        "the opening paragraph no longer describes the finding per commit at all"
    )

    # Per sentence, not whole-paragraph. A whole-document hedge check passes on any
    # hedging word anywhere in the opening — and "re-derived from the saved run records"
    # was doing exactly that, which left "roughly 100 ms per preceding commit" able to
    # stand unqualified in the sentence before it.
    hedge = re.compile(r"suggest|infer|arithmetic|divide|per-commit step", re.IGNORECASE)
    overstated: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(opening.split())):
        per_commit = r"\b(?:roughly|about|approximately)\s+[\d.]*\s*ms\s+per\b"
        if not re.search(per_commit, sentence, re.IGNORECASE):
            continue
        if hedge.search(sentence) is None:
            overstated.append(sentence.strip())

    assert overstated == [], (
        "the opening states a per-commit figure without marking it as arithmetic on the "
        f"measured deltas: {overstated}. The evidence holds +100 ms over one preceding "
        "commit and +180 ms over two; neither is a measurement of a per-commit step."
    )

    # And the measured deltas it rests on must be stated outright, not only implied.
    for condition_id, delta in deltas.items():
        assert f"{delta:+,.0f} ms" in opening, (
            f"the opening does not state {condition_id}'s measured {delta:+,.0f} ms"
        )


def test_the_report_does_not_claim_setup_needs_no_network() -> None:
    """`make setup` fetches dependencies, so the report must not say it needs no network.

    An earlier version of this README claimed `make setup` reached a working state with
    "no account, no key, no network" — and `make setup` runs `uv sync` and `npm install`,
    both of which resolve over the network. Every other offline claim in the report is
    true; this one was not, and a reader following it on a plane would have found out at
    the first command.

    The accurate claim is narrower and still worth making: no account and no API key,
    and offline from then on.
    """
    # Whitespace collapsed, because the README is hard-wrapped and "No\naccount" is the
    # same sentence as "No account". Matching across a line break is not a distinction
    # worth making; the phrase either is or is not in the section.
    section = " ".join(section_text("Reproduce").lower().split())

    # "no network" is fine where it qualifies a *specific* offline command. It is not
    # fine where it qualifies setup, which installs dependencies. Judged per sentence,
    # since the README is wrapped and a clause can straddle a line break.
    for sentence in re.split(r"(?<=[.!?])\s+", section):
        if "no network" not in sentence and "offline" not in sentence:
            continue
        assert not re.search(r"\bmake setup\b|\bsetup\b", sentence), (
            f"the Reproduce section claims no network in a sentence about setup: "
            f"{sentence!r}. `uv sync` and `npm install` both fetch over the network."
        )

    assert re.search(r"no account", section), (
        "the Reproduce section no longer says setup needs no account, which is true and "
        "is the half of the claim worth making"
    )
    assert re.search(r"needs? network|fetch|resolve", section), (
        "the Reproduce section never says setup needs network at all. Silently dropping "
        "the claim is not the same as correcting it."
    )


def test_the_report_does_not_claim_frequency_severity_or_scope() -> None:
    """No claim about how often this happens, how bad it is, or who it affects.

    The spec's scope boundary, asserted on the document rather than trusted to the
    author. Twelve runs of one synthetic word on one day cannot support a prevalence
    claim.

    The subtlety is that the report is *required* to discuss these things in order to
    deny them — "No claim about production prevalence", "no claim about how often this
    happens, how severe it is". Scanning the whole document for the vocabulary therefore
    fails against exactly the sentence the issue asks for. So units carrying an explicit
    disclaimer are removed first, and what remains is scanned for the claim itself.

    A deliberate asymmetry: it can be defeated by burying a claim in a sentence that also
    contains the word "no", but it is far better than a check that forbids the required
    disclaimer, which would push an author toward omitting it.
    """
    disclaimers = re.compile(
        r"no claim|cannot support|not a measurement|does not claim|nothing is claimed",
        re.IGNORECASE,
    )
    # Split on sentence enders and bullet starts, so a disclaimer about prevalence does
    # not silently excuse whatever sentence happens to follow it.
    units = re.split(r"(?<=[.!?])\s+|\n(?=[-*#])", readme())
    text = " ".join(unit for unit in units if disclaimers.search(unit) is None)

    forbidden = {
        "customer impact": r"(?:impacts?|affects?|breaks?)\s+(?:customers?|users?|your)\b",
        "platform reliability": r"\bunreliable\b|not reliable|degrades reliability",
        "frequency": r"\b(?:commonly|frequently|usually|most of the time|affects most)\b",
        "severity": r"\b(?:severe|severely|significantly degrades|major problem)\b",
        "scope": r"\bmost (?:users|customers|traffic)\b|widespread across",
    }

    found = [
        description
        for description, pattern in forbidden.items()
        if re.search(pattern, text, re.IGNORECASE) is not None
    ]

    assert found == [], (
        f"the report makes claims outside what was measured: {found}. Twelve runs of "
        "one synthetic word cannot support a claim about frequency, severity or scope."
    )


def test_the_report_credits_the_reporter_as_the_originator() -> None:
    """The finding is someone else's; this adds runnable materials and fresh evidence.

    Checked for the word that carries the attribution rather than for the issue number
    alone — naming the issue is necessary but not sufficient, since a report can cite a
    source and still present the finding as its own.
    """
    text = readme().lower()

    assert re.search(r"originator|credited as the origin", text), (
        "the report never credits the original reporter as the originator of the finding"
    )
    assert re.search(r"independent (diagnostic|rerun|measurement|reproduction)", text), (
        "the report does not describe itself as an independent check; without that, the "
        "credit reads as a formality"
    )

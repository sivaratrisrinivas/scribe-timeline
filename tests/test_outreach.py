"""The outreach copy may only say what was measured.

`docs/outreach.md` is the one document in this repository whose errors would not be
caught by re-running anything. The README can be checked against the evidence because it
lives beside it; a DM is sent into the world and cannot be un-sent, and a post quoting a
figure the evidence does not support discredits the measured part of the finding along
with it.

So the drafts are parsed and their numbers checked against `evidence/comparison.json`,
the way the README's tables are. A draft that quotes +190 where the evidence holds +180
fails here, before it reaches anyone.

What this cannot do is judge a sentence's *framing*. It verifies that every millisecond
figure, every count, and every figure-for-quantity is one the evidence holds, and that
the drafts carry the credit and avoid the vocabulary of a prevalence claim. Whether "worth
a look" is the right register for a particular reader is not a question a regex can
answer, and the file says so next to the drafts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTREACH = REPO_ROOT / "docs" / "outreach.md"
COMPARISON = REPO_ROOT / "evidence" / "comparison.json"


def outreach() -> str:
    return OUTREACH.read_text()


def comparison() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(COMPARISON.read_text())
    return document


def drafts() -> dict[str, str]:
    """Each block-quoted draft, keyed by the heading it sits under.

    Sections are delimited by ATX headings, and a draft is the run of `>`-quoted lines
    within one. Splitting on headings rather than reading the whole file is deliberate:
    the file's own rules section has to *name* the forbidden vocabulary in order to
    forbid it, so a whole-document scan would fail against the rules themselves.

    `lstrip("# ")` rather than a regex, because the file also contains a `>`-quoted
    markdown table whose rows begin with `#`-free text but whose `#` header row is part
    of the draft — a heading match inside a quote must not end the section.
    """
    sections: dict[str, list[str]] = {}
    heading = ""
    for line in outreach().splitlines():
        if line.startswith("#") and not line.startswith(">"):
            heading = line.lstrip("# ").strip()
            sections.setdefault(heading, [])
            continue
        if heading:
            sections[heading].append(line)

    return {
        title: "\n".join(line for line in body if line.startswith(">"))
        for title, body in sections.items()
    }


def quoted_drafts() -> dict[str, str]:
    found = {title: body for title, body in drafts().items() if body.strip()}
    assert len(found) >= 3, (
        f"expected several drafted messages, found {sorted(found)}. The outreach copy is "
        "meant to be ready to send, not a set of notes."
    )
    return found


def test_the_outreach_copy_exists_and_is_marked_unsent() -> None:
    """The file exists, and says plainly that sending is not this repository's job."""
    assert OUTREACH.exists(), "docs/outreach.md is missing"

    assert re.search(r"not\s+sent", outreach(), re.IGNORECASE), (
        "the outreach copy does not record that these drafts are unsent"
    )


def test_every_millisecond_figure_in_a_draft_is_one_the_evidence_holds() -> None:
    """No draft quotes a measurement that does not exist.

    Every `±N ms` in the copy must be a figure in the published report — a measured
    delta, a median, the reported claim's own offsets, or the tolerance quantum. That
    list is closed on purpose: a figure outside it is a figure nobody measured, which is
    exactly what a DM should not contain.
    """
    report = comparison()

    permitted: set[str] = set()
    for entry in report["conditions"]:
        permitted.add(f"{entry['median_marker_ms']:,.0f}")
        permitted.add(f"{entry['median_marker_ms']:.0f}")
        # The anchor's delta is `null`, and formatting `None` raises rather than
        # producing a figure -- there is no delta for a condition measured against
        # itself, which is exactly why the report withholds one.
        delta = entry["delta_vs_anchor_ms"]
        if delta is not None:
            permitted.add(f"{delta:+,.0f}")
            permitted.add(f"{delta:.0f}")
    for offset in report["reporter_claimed_offset_ms"].values():
        permitted.add(f"{offset:+,.0f}")
        permitted.add(f"{offset:.0f}")
    permitted.add(f"{report['timestamp_quantum_ms']:.0f}")

    # `manual_2`'s +0 is written with an explicit sign; keep both spellings.
    permitted.add("+0")
    permitted.add("0")

    # One figure is permitted that the evidence does not hold: the ~90 ms per-commit
    # step the DM infers from +180 over two commits. It is arithmetic, not a
    # measurement, and no condition measures a per-commit step directly.
    #
    # Allowed only because the draft hedges it in the same sentence — checked below.
    # The point of the closed list is that adding a *second* inferred figure fails here,
    # so an author has to come back and argue for it deliberately rather than slipping
    # an arithmetic result into a DM nobody re-derives.
    inferred = {"90"}
    hedge = re.compile(r"suggest|infer|arithmetic|not .{0,30}measures directly", re.IGNORECASE)

    offenders: list[str] = []
    unhedged: list[str] = []
    for title, body in quoted_drafts().items():
        for match in re.finditer(r"([+\u2212-]?\d[\d,]*)\s*ms", body):
            figure = match.group(1).replace("\u2212", "-")
            digits = figure.lstrip("+-")
            if digits in {value.lstrip("+-") for value in permitted}:
                continue
            if digits in inferred:
                # The sentence containing it must hedge, or the number travels alone.
                sentence = _sentence_around(body, match.end())
                if hedge.search(sentence) is None:
                    unhedged.append(f"{title}: {match.group(0)!r} stated without a hedge")
                continue
            offenders.append(f"{title}: {match.group(0)!r}")

    assert offenders == [], (
        f"the drafts quote millisecond figures the evidence does not hold: {offenders}. "
        f"Permitted: {sorted(permitted)} plus the hedged inferences {sorted(inferred)}"
    )
    assert unhedged == [], (
        f"inferred figures stated as measurements: {unhedged}. An inference has to be "
        "marked as one in the sentence it appears in, or it becomes a quoted result."
    )


def _table_rows(body: str) -> list[list[str]]:
    """Markdown table rows from a draft, with the `---` separator dropped.

    The block-quote marker is stripped first. Every draft in this file is quoted with
    `>`, so a table row arrives as `> | `vad_1` | VAD | ...` — and a reader scanning for
    table rows on the unstripped text finds none, which is how this check came to pass
    against a table whose figures belonged to the wrong conditions.
    """
    unquoted = (line.lstrip("> ").strip() for line in body.splitlines())
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in unquoted
        if line.startswith("|")
    ]
    return [row for row in rows if not all(set(cell) <= {"-", ":", " "} for cell in row)]


def _sentence_around(body: str, index: int) -> str:
    """The sentence containing a character offset.

    Split on sentence enders rather than on newlines, because the drafts are hard-wrapped
    at ~76 columns and a newline-split would put the hedge in a different "sentence" from
    the number purely because of where the line broke.
    """
    start = max(body.rfind(".", 0, index), body.rfind("!", 0, index), body.rfind("?", 0, index))
    candidates = (body.find(".", index), body.find("!", index), body.find("?", index))
    ends = [position for position in candidates if position != -1]
    end = min(ends) if ends else len(body)
    return body[start + 1 : end + 1]


def test_every_condition_figure_in_a_draft_matches_its_row() -> None:
    """A draft naming a condition quotes that condition's figures, not another's.

    The closed-list check above would pass if a draft said "`vad_2` returned 12,200 ms" —
    a real figure, wrong row. This binds the figure to the condition it is attributed to,
    which is the mistake a rushed message actually makes.

    Rows are read from the draft's own markdown tables rather than matched by regex. The
    regex version stopped at the first `|`, so on a table row it matched the condition id
    on its own, found no figures in it, and passed — including against a row carrying
    another condition's figures.
    """
    report = comparison()
    conditions = {entry["condition_id"]: entry for entry in report["conditions"]}

    # Each condition's median and delta, as they would be written.
    by_condition = {
        condition_id: {
            f"{entry['median_marker_ms']:,.0f} ms",
            # The anchor has no delta to render, and `None` formats as neither a sign
            # nor a figure, so it is left as an empty alternative.
            f"{entry['delta_vs_anchor_ms']:+,.0f} ms"
            if entry["delta_vs_anchor_ms"] is not None
            else "",
        }
        for condition_id, entry in conditions.items()
    }

    offenders: list[str] = []
    seen_rows = 0

    for title, body in quoted_drafts().items():
        for row in _table_rows(body):
            identifier = row[0].strip().strip("`")
            if identifier not in conditions:
                continue
            seen_rows += 1
            figures = {
                figure.strip()
                for figure in re.findall(r"[+\u2212-]?\d[\d,]*\s*ms", " ".join(row))
            }
            allowed = {value.strip() for value in by_condition[identifier]}
            wrong = figures - allowed
            if wrong:
                offenders.append(
                    f"{title}: {identifier} shown with {sorted(wrong)}, expected {sorted(allowed)}"
                )

    assert seen_rows > 0, (
        "no draft contains a table row naming a condition, so this test checks nothing"
    )
    assert offenders == [], (
        f"a draft attributes a figure to the wrong condition: {offenders}. Expected per "
        f"condition: {by_condition}"
    )


def test_every_draft_credits_the_original_reporter() -> None:
    """Every draft names the finding's source and attributes it, not just the first one.

    A DM to the reporter and a public post are different audiences, and it is easy to
    carry the credit in one and drop it from the other on the way to the send button. So
    it is required in each, individually.

    The attribution is satisfied either by the handle or by an explicit phrase. The DM
    addresses the reporter as "you" and calls the observation theirs, which is a
    stronger credit than pasting their own handle at them — and requiring the handle
    there would push an author towards the colder, worse version of the message.
    """
    attributed = re.compile(
        r"wujin941005|your finding|you filed|the originator|credited to",
        re.IGNORECASE,
    )

    offenders = [
        f"{title} (no issue reference)"
        for title, body in quoted_drafts().items()
        if "849" not in body
    ] + [
        f"{title} (no attribution)"
        for title, body in quoted_drafts().items()
        if attributed.search(body) is None
    ]

    assert offenders == [], (
        f"these drafts do not credit @wujin941005 and elevenlabs-python#849: {offenders}. "
        "The finding is theirs; every draft says so."
    )


def test_every_draft_carries_the_published_link() -> None:
    """Every draft carries the one link, so a reader can check rather than take it."""
    links = set(re.findall(r"https://[^\s)>\]]+", outreach()))

    offenders = [
        title for title, body in quoted_drafts().items() if not any(link in body for link in links)
    ]

    assert offenders == [], f"these drafts carry no link at all: {offenders}"


def test_no_draft_claims_frequency_severity_or_customer_impact() -> None:
    """The claim the evidence cannot support is the one most natural to make in a DM.

    "Hope this is useful" is harmless. "This probably affects a lot of your users" is
    the sentence that turns a careful diagnostic into a complaint, and it is the sort of
    thing that gets added while making a message fit.

    Scoped to the drafts, so the file's own rules section can name the forbidden
    vocabulary in order to forbid it.
    """
    # The patterns tolerate intervening words. Requiring adjacency — "a lot of users",
    # "affects your users" — missed "affects a lot of your users", which is the same claim
    # with three words in the middle, and that phrasing is the more natural one.
    forbidden = {
        "prevalence": r"\b(?:many|most|lots? of|plenty of|numerous)\b[^.!?\n]{0,30}"
        r"\b(?:users|customers|people)\b|"
        r"\b(?:frequently|commonly|usually|often|most of the time|widespread)\b",
        "production behaviour": r"\bin production\b|\bfor production\b|\bat scale\b|"
        r"\bin the wild\b",
        "customer impact": r"\b(?:impacts?|affects?|breaks?|hurts?)\b[^.!?\n]{0,30}"
        r"\b(?:customers?|users?)\b",
        "severity": r"\b(?:severe|critical|blown out|unusable|devastating)\b",
        "reliability judgement": r"\bunreliable\b|\bnot reliable\b|\bnot production.ready\b",
        "urgency": r"\b(?:urgent|asap|immediately|urgently needed)\b",
        "accusation of neglect": r"\b(?:nobody|no one) (?:has )?(?:responded|replied|looked)\b",
    }

    offenders: list[str] = []
    for title, body in quoted_drafts().items():
        for description, pattern in forbidden.items():
            if re.search(pattern, body, re.IGNORECASE) is not None:
                offenders.append(f"{title}: {description}")

    assert offenders == [], (
        f"the drafts claim more than was measured: {offenders}. Twelve runs of one "
        "synthetic word cannot support a claim about frequency, severity, or impact."
    )


def test_no_draft_accuses_the_platform() -> None:
    """The register is "worth a look", not "bug report ignored".

    Asserted because it is the tone most likely to drift when a draft is shortened to
    fit a character limit, which is the moment someone reaches for a sharper word.
    """
    accusatory = r"\b(?:ignored|neglected|bug report|broken|unfixed|silent(?:ly)? ignor)\b"

    offenders = [
        title
        for title, body in quoted_drafts().items()
        if re.search(accusatory, body, re.IGNORECASE) is not None
    ]

    assert offenders == [], (
        f"these drafts accuse rather than report: {offenders}. This is an experiment "
        "that found something, not a complaint about a company."
    )


def test_the_drafts_state_the_simplifications_where_a_reader_would_see_them() -> None:
    """At least one draft carries the boundaries, not just the file's rules section.

    The X post is 280 characters and cannot hold all five. So the requirement is that the
    set of drafts states them *somewhere a reader sees* — the comment draft, which is
    long enough, plus the file's own rules. This is the compromise that lets the short
    post exist without letting it exist alone.
    """
    bodies = "\n".join(quoted_drafts().values()).lower()

    assert "synthetic" in bodies, (
        "no draft says the audio was synthetic; a reader could otherwise assume real "
        "customer audio, which would be a much larger claim"
    )
    assert re.search(r"one model|scribe[_ ]v2[_ ]realtime", bodies), (
        "no draft names the single model the measurement covers"
    )
    assert re.search(r"production prevalence|prevalence", bodies), (
        "no draft disclaims a production-prevalence claim, which is the claim a reader "
        "is most likely to infer and least entitled to"
    )


def test_the_verification_checklist_covers_the_link_and_the_gate() -> None:
    """The pre-send checklist names the two things that actually break.

    A fresh private window, and `make check`. Both are in the file, so a checklist entry
    referencing them is asserted to exist -- a checklist that dropped them would still
    look like a checklist.
    """
    text = outreach().lower()

    assert re.search(r"private window|logged-out|fresh", text), (
        "the pre-send checklist does not require opening the link in a fresh browser"
    )
    assert "make check" in text, (
        "the pre-send checklist does not require the gate to pass before sending"
    )
    assert text.count("- [ ]") >= 4, (
        "the pre-send checklist is too short to be a gate on its own"
    )


def test_the_unmeasured_inference_in_the_dm_is_flagged_as_such() -> None:
    """The ~90 ms per-commit figure is arithmetic, not a measurement.

    Two measurements divided by two, and no condition in the evidence measures a
    per-commit step directly. It is defensible — but only if it is marked as an inference
    where it appears, and only if the file says it can be removed.

    Both halves matter. Without the inline hedge the number is indistinguishable from
    the measured ones beside it, which is how an inference becomes a quoted figure. And
    without the note saying it can be dropped, the author keeps it rather than judging
    it — the note is what makes it a choice instead of a default.
    """
    text = outreach()
    dm = drafts().get("DM — for the original reporter", "")

    assert "90" in dm, "this test is about the ~90 ms clause; the draft has changed"

    # Hedge in the draft itself, not only in the file's notes beside it: the draft is
    # what gets pasted into a message box, and the notes do not travel with it.
    assert re.search(r"suggest|infer|arithmetic", dm, re.IGNORECASE), (
        "the ~90 ms figure is not hedged inside the draft, so it would reach a reader "
        "looking like a measurement"
    )
    assert re.search(r"cut that clause|remove it", text, re.IGNORECASE), (
        "the file does not say the ~90 ms clause can be dropped, so the author will keep "
        "it rather than judge it"
    )

/**
 * The comparison, as a reader reads it.
 *
 * Every figure here is read from the exported report. The component does no
 * arithmetic: it cannot, because it has no definition of a delta and no knowledge
 * that the anchor is the zero-preceding-commit condition. That is the point. A
 * viewer that subtracted two timestamps would be a second implementation of the
 * measurement, free to disagree with the README by one 20 ms quantisation step
 * with no test able to say which was right.
 *
 * The table's job is to make the discrepancy visible without the reader doing any:
 * the anchor's returned position sits in its own row beside every other row's, and
 * the delta is printed rather than left to be worked out.
 *
 * Two figures are kept visibly apart, because they look alike and are not:
 *
 * - **The delta**, which differences one condition's median against the anchor's.
 * - **The insertion-point gap**, which differences a timestamp against where a
 *   clip was placed. It is not an offset -- the original report states its numbers
 *   that way, which is why it is shown -- and it enters no delta. It has its own
 *   labelled block rather than a column, because a column would read as a fourth
 *   measurement.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { ConditionTable } from "./ConditionTable.js";
import { parseComparison, type ComparisonReport } from "./comparison.js";
import { parseRunIndex, type RunIndexEntry } from "./runIndex.js";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..");
const bundleDir = join(repoRoot, "viewer", "public");

/** The report and the index this project actually ships.
 *
 *  Read from the committed bundle rather than from fixtures shaped like it, so these
 *  tests describe the numbers a reader actually sees. A drift in either export
 *  fails here instead of quietly leaving the page showing something else.
 */
function shipped(): { report: ComparisonReport; groups: Map<string, readonly RunIndexEntry[]> } {
  const comparison = parseComparison(
    JSON.parse(readFileSync(join(bundleDir, "comparison.json"), "utf8")),
  );
  if (comparison.kind !== "report") throw new Error(`no report: ${comparison.reason}`);
  const index = parseRunIndex(JSON.parse(readFileSync(join(bundleDir, "runs.json"), "utf8")));
  return {
    report: comparison.report,
    groups: new Map(
      [...index.runs.reduce((all, run) => {
        const existing = all.get(run.conditionId) ?? [];
        existing.push(run);
        all.set(run.conditionId, existing);
        return all;
      }, new Map<string, RunIndexEntry[]>())].map(([id, runs]) => [
        id,
        runs.sort((a, b) => a.repeatIndex - b.repeatIndex),
      ]),
    ),
  };
}

function renderTable(
  options: {
    readonly report?: ComparisonReport | null;
    readonly unavailableReason?: string;
    readonly selectedRunId?: string;
    readonly selectedMarkerMs?: number | null;
  } = {},
) {
  const { report: shippedReport, groups } = shipped();
  // The caller's report wins, so a test can put a shape in front of the component
  // that the shipped bundle does not contain. Reading the shipped one regardless
  // would make such a test pass against the wrong row.
  const report = options.report === null ? null : (options.report ?? shippedReport);
  const onSelect = vi.fn();
  const selectedRunId = options.selectedRunId ?? "2026-10-01T21-12-51Z__vad_2__rep2";
  render(
    <ConditionTable
      comparison={
        report === null
          ? { kind: "unavailable", reason: options.unavailableReason ?? "no anchor condition" }
          : { kind: "report", report }
      }
      groups={groups}
      selectedRunId={selectedRunId}
      selectedMarkerMs={options.selectedMarkerMs ?? null}
      onSelect={onSelect}
    />,
  );
  return { onSelect, report, groups };
}

/** The comparison table, which is the one place a delta is shown.
 *
 *  Scoped to by test id rather than by role: the page carries three tables, and a
 *  query for "the table" would silently start matching a different one the moment
 *  another is added.
 */
function conditionsTable(): HTMLElement {
  return screen.getByTestId("conditions-table");
}

/** The row for a condition, found by its name rather than its position. */
function row(conditionId: string): HTMLElement {
  return within(conditionsTable()).getByRole("row", { name: new RegExp(`^${conditionId}\\b`) });
}

describe("what the table is", () => {
  it("says the figures come from the report over these runs, not from the page", () => {
    // A reader deciding whether to trust the page needs to know it is not doing the
    // arithmetic itself.
    renderTable();

    expect(screen.getByText(/This page subtracts nothing/i)).toBeInTheDocument();
  });

  it("states that no timestamp is compared to a clip's insertion point", () => {
    // The rule the whole project is built on, said where the numbers are.
    renderTable();

    expect(screen.getByTestId("measurement-rule")).toHaveTextContent(
      /no timestamp is compared to a clip insertion point/i,
    );
  });
});

describe("expected against returned, side by side", () => {
  it("gives the anchor's returned position as the baseline every delta is measured from", () => {
    // "Expected" here is what the same word returned with nothing preceding it --
    // not where its clip was placed, which is a different quantity entirely.
    renderTable();

    const baseline = screen.getByTestId("anchor-baseline");
    expect(baseline).toHaveTextContent("vad_0");
    expect(baseline).toHaveTextContent("12,200 ms");
    expect(baseline).toHaveTextContent(/everything below is measured against this/i);
  });

  it("prints each condition's returned position and the delta, so no arithmetic is needed", () => {
    renderTable();

    const vad1 = row("vad_1");
    expect(vad1).toHaveTextContent("12,300");
    expect(vad1).toHaveTextContent("+100");
  });

  it("shows the measured commit's drift where the two conditions meet", () => {
    renderTable();

    expect(row("vad_2")).toHaveTextContent("+180");
  });

  it("leaves the anchor with no delta of its own", () => {
    // A zero here would read as "no drift". The truth is "this is the baseline".
    renderTable();

    const anchor = row("vad_0");
    expect(anchor).toHaveTextContent(/baseline/i);
    expect(anchor).not.toHaveTextContent(/[+-]0(\.0)?\s*ms/);
  });

  it("renders a condition carrying no delta as having none, not as zero", () => {
    // The report cannot reach the table in this state -- the parser refuses a null
    // delta on a non-anchor -- so the row is built by hand. It is here because the
    // component must not be the layer that invents the zero: `+0` in the one column
    // a reader takes the finding from is indistinguishable from a measured absence of
    // drift, and nothing else on the page would show it had been substituted. It
    // must also not be labelled "baseline", which claims the row *is* the anchor --
    // a second false statement, and one the parser was right to prevent.
    const { report } = shipped();
    const anchor = report.conditions.find((condition) => condition.isAnchor)!;
    renderTable({
      report: {
        ...report,
        conditions: [
          { ...anchor, isAnchor: false, deltaVsAnchorMs: null, deltaIntervalMs: null },
        ],
      },
    });

    const cell = within(row("vad_0")).getByTestId("delta");
    expect(cell).toHaveTextContent(/no delta recorded/i);
    expect(cell).not.toHaveTextContent("baseline");
    expect(cell).not.toHaveTextContent("0");
  });

  it("reports the run on screen beside the condition's median", () => {
    // An individual repeat is not the condition's median, and showing one in the
    // other's place would be the single-number mistake this project refuses.
    renderTable({ selectedMarkerMs: 12_380 });

    expect(screen.getByTestId("selected-run")).toHaveTextContent(
      /returned the marker at 12,380 ms/i,
    );
  });

  it("does not claim a figure for the selected run when its marker is not in hand", () => {
    renderTable({ selectedMarkerMs: null });

    expect(screen.queryByTestId("selected-run")).not.toBeInTheDocument();
  });
});

describe("the run-to-run spread", () => {
  it("is shown for every condition, including the ones that did not move", () => {
    // A condition with zero spread is a result. Leaving the column out for it would
    // make "no spread" and "not measured" look the same.
    renderTable();

    // A column of zeroes is a result; leaving the figure out for a condition that did
    // not move would make "no spread" and "not measured" look the same.
    for (const id of ["vad_0", "vad_1", "vad_2", "manual_2"]) {
      expect(within(row(id)).getByTestId("spread"), id).toHaveTextContent("0");
    }
  });

  it("lists each repeat's own figure, not only the median", () => {
    // One number per condition is not a measurement; the repeats are the evidence.
    renderTable();

    expect(row("vad_2")).toHaveTextContent("12,380 / 12,380 / 12,380");
  });

  it("gives the range the delta took across repeats", () => {
    // What decides whether a claim can be told apart from a measurement.
    renderTable();

    expect(row("vad_2")).toHaveTextContent("180–180");
  });
});

describe("choosing an individual repeat", () => {
  it("offers every repeat of the condition", () => {
    renderTable();

    const buttons = within(row("vad_2")).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual([
      expect.stringContaining("rep 0"),
      expect.stringContaining("rep 1"),
      expect.stringContaining("rep 2"),
    ]);
  });

  it("marks which repeat is on screen", () => {
    renderTable({ selectedRunId: "2026-10-01T21-12-51Z__vad_2__rep2" });

    const current = within(row("vad_2")).getByRole("button", { name: /rep 2/ });
    expect(current).toHaveAttribute("aria-pressed", "true");
    expect(within(row("vad_2")).getByRole("button", { name: /rep 0/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("switches to the run behind the repeat that was chosen", async () => {
    const { onSelect } = renderTable();

    await userEvent.click(within(row("vad_2")).getByRole("button", { name: /rep 1/ }));

    expect(onSelect).toHaveBeenCalledWith("2026-10-01T21-11-35Z__vad_2__rep1");
  });

  it("switches condition when a different condition's row is chosen", async () => {
    const { onSelect } = renderTable();

    await userEvent.click(within(row("manual_2")).getAllByRole("button")[0]!);

    expect(onSelect).toHaveBeenCalledWith("2026-10-01T21-10-37Z__manual_2__rep0");
  });
});

describe("the counts a condition claims against the counts it returned", () => {
  it("shows the speech segments placed before the marker", () => {
    // The independent variable: how much speech the experiment put in front of it.
    renderTable();

    expect(row("vad_2")).toHaveTextContent("2");
    expect(row("vad_0")).toHaveTextContent("0");
  });

  it("shows the commits the server actually returned, beside that", () => {
    // The count the fixture asked for is the hypothesis; the count that came back is
    // the evidence. A run that returned a different number is not its own condition.
    renderTable();

    expect(row("vad_2")).toHaveTextContent("3 / 3 / 3");
  });
});

describe("the reported claim", () => {
  it("is shown next to the measurement, credited and dated", () => {
    // The finding is a reproduction of someone else's, and a reader has to be able
    // to see both numbers and check who reported which.
    renderTable();

    expect(screen.getByText(/elevenlabs-python#849/)).toBeInTheDocument();
    expect(screen.getByText(/2026-08-19/)).toBeInTheDocument();
    expect(row("vad_1")).toHaveTextContent("+100");
  });

  it("says the returned timestamps are quantised, which sets what counts as agreement", () => {
    // A 20 ms gap between a measured and a claimed step is one tick, not a
    // disagreement. Without the quantum, a reproduction reads as a refutation.
    renderTable();

    expect(screen.getByText(/20 ms/)).toBeInTheDocument();
  });
});

describe("the manual control", () => {
  it("states its conclusion rather than leaving a table row to be interpreted", () => {
    renderTable();

    const control = screen.getByRole("note", { name: "The manual control" });
    expect(control).toHaveTextContent(/do not accumulate/i);
    expect(control).toHaveTextContent(/manual_2/);
    expect(control).toHaveTextContent(/vad_2/);
  });
});

describe("figures that are not the measurement", () => {
  it("keeps the insertion-point gaps out of the table", () => {
    // A column of them would read as a fourth measurement. They are reported
    // because the original report states its numbers in exactly those terms.
    renderTable();

    const context = screen.getByRole("note", { name: "Not a measurement" });
    expect(within(context).getByText("vad_2")).toBeInTheDocument();
    expect(within(context).getByText(/\+380/)).toBeInTheDocument();
  });
});

describe("a bundle that cannot support a comparison", () => {
  it("says so plainly instead of drawing an empty table", () => {
    // An empty table of conditions reads as "no drift found", which is a finding.
    renderTable({ report: null, unavailableReason: "no anchor condition in this bundle" });

    expect(screen.getByRole("alert")).toHaveTextContent(/no anchor condition/);
    expect(screen.queryByTestId("conditions-table")).not.toBeInTheDocument();
  });

  it("still offers the runs, because a single run supports a lot on its own", () => {
    // One run is not a measurement, but it is the audio and the returned words, and a
    // reader who came for those should not be told there is nothing here.
    renderTable({ report: null });

    const list = within(screen.getByTestId("run-list"));
    expect(list.getAllByRole("button", { name: /rep 0/ })).toHaveLength(4);
  });
});

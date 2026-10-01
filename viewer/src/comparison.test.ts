/**
 * The comparison document: the published measurement, read rather than recomputed.
 *
 * `comparison.json` is written by `scripts/export_viewer.py` from
 * `scribe_timeline.analysis.compare`, over exactly the run records the bundle
 * serves. Every delta, median, spread and verdict in the viewer comes from that
 * file.
 *
 * That is the whole point of the module. Reimplementing the arithmetic here would
 * be a second implementation of the measurement, free to disagree with the README
 * by one 20 ms quantisation step, with no test able to say which one was right.
 * So the viewer parses the report and draws it.
 *
 * The document has two shapes. Either it carries a `report`, or it carries an
 * `unavailable_reason` -- a bundle of one run, or a set with no
 * zero-preceding-commit anchor, cannot support a comparison. Both are values
 * rather than failures, because "this bundle holds no comparison" is a fact a
 * reader is entitled to, and a missing file would be indistinguishable from a
 * broken deployment.
 *
 * As with the run record, a malformed comparison fails loudly. A delta that
 * rendered as `0` would be indistinguishable from a real finding of no drift.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { InvalidComparisonError, parseComparison, type Comparison } from "./comparison.js";

const here = dirname(fileURLToPath(import.meta.url));
const published = join(here, "..", "..", "evidence", "comparison.json");

/** A whole `comparison.json` document around a report body. */
function document(body: Record<string, unknown>): Record<string, unknown> {
  return { report: body };
}

/** The comparison the project actually published, parsed.
 *
 *  Read from `evidence/comparison.json` -- the report the README's table was
 *  written from -- rather than from a fixture shaped like it. The export wraps
 *  that same report in a document, so the wrapper is applied here too.
 */
function publishedComparison(): Comparison {
  return parseComparison(document(JSON.parse(readFileSync(published, "utf8"))));
}

/** A minimal well-formed report, so a test can change one field at a time. */
function report(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    marker_text: "Zorblax",
    anchor_condition_id: "vad_0",
    match_rule_note: "Every delta differences one condition's median against the anchor's.",
    drift_detected: true,
    conditions: [
      {
        condition_id: "vad_0",
        commit_strategy: "vad",
        prior_segment_count: 0,
        observed_commit_count: [1],
        repeat_count: 1,
        marker_start_ms: [12_200],
        median_marker_ms: 12_200,
        spread_ms: 0,
        is_anchor: true,
        delta_vs_anchor_ms: null,
        delta_spread_ms: null,
        delta_interval_ms: null,
      },
    ],
    claims: [],
    control: null,
    context: { conditions: [], note: "These figures are not the measurement." },
    timestamp_quantum_ms: 20,
    claims_source: "elevenlabs-python#849",
    claims_filed: "2026-08-19",
    reporter_claimed_offset_ms: {},
    reporter_claimed_manual_offset_ms: 10,
    ...overrides,
  };
}

describe("the published comparison parses", () => {
  it("keeps the anchor as a named condition rather than a zero", () => {
    // A delta of zero against itself would read as "no drift" where the truth is
    // "this is the baseline everything else is measured from". `null` is the only
    // value that can say both.
    const comparison = publishedComparison();

    expect(comparison.kind).toBe("report");
    if (comparison.kind !== "report") return;
    expect(comparison.report.anchorConditionId).toBe("vad_0");
    const anchor = comparison.report.conditions.find((c) => c.isAnchor)!;
    expect(anchor.deltaVsAnchorMs).toBeNull();
    expect(anchor.deltaIntervalMs).toBeNull();
  });

  it("carries the measured deltas the README quotes", () => {
    const comparison = publishedComparison();
    if (comparison.kind !== "report") return throwMissingReport(comparison);

    const byId = (id: string) => comparison.report.conditions.find((c) => c.conditionId === id)!;
    expect(byId("vad_1").deltaVsAnchorMs).toBe(100);
    expect(byId("vad_2").deltaVsAnchorMs).toBe(180);
    expect(byId("manual_2").deltaVsAnchorMs).toBe(0);
  });

  it("carries every repeat of every condition, not just a median", () => {
    // A median with the repeats thrown away is a single number, which is the thing
    // this project refuses to present as a measurement.
    const comparison = publishedComparison();
    if (comparison.kind !== "report") return throwMissingReport(comparison);

    const vad2 = comparison.report.conditions.find((c) => c.conditionId === "vad_2")!;
    expect(vad2.markerStartMs).toEqual([12_380, 12_380, 12_380]);
    expect(vad2.repeatCount).toBe(3);
    expect(vad2.spreadMs).toBe(0);
  });

  it("keeps the observed commit count beside the intended one", () => {
    // The count the server returned is evidence; the count the fixture asked for is
    // the hypothesis. A run that returned a different number is not the condition
    // its name claims.
    const comparison = publishedComparison();
    if (comparison.kind !== "report") return throwMissingReport(comparison);

    const vad2 = comparison.report.conditions.find((c) => c.conditionId === "vad_2")!;
    expect(vad2.priorSegmentCount).toBe(2);
    expect(vad2.observedCommitCount).toEqual([3, 3, 3]);
  });

  it("keeps the manual control's stated conclusion", () => {
    const comparison = publishedComparison();
    if (comparison.kind !== "report") return throwMissingReport(comparison);

    expect(comparison.report.control).not.toBeNull();
    expect(comparison.report.control!.manualAccumulates).toBe(false);
    expect(comparison.report.control!.comparesToConditionId).toBe("vad_2");
    expect(comparison.report.control!.interpretation).toMatch(/do not accumulate/);
  });

  it("keeps the figures that are not the measurement clearly apart", () => {
    // The original report states its numbers against the clip's insertion point,
    // so a reader comparing the two needs these -- and needs them not mistaken for
    // the deltas.
    const comparison = publishedComparison();
    if (comparison.kind !== "report") return throwMissingReport(comparison);

    expect(comparison.report.context.conditions[0]!.insertionPointGapMs).toBe(200);
    expect(comparison.report.context.note).toMatch(/not the measurement/);
  });

  it("carries the reported claim so a reader can see both numbers", () => {
    const comparison = publishedComparison();
    if (comparison.kind !== "report") return throwMissingReport(comparison);

    expect(comparison.report.claims.map((claim) => claim.conditionId)).toEqual([
      "vad_1",
      "vad_2",
    ]);
    expect(comparison.report.claims.every((claim) => claim.reproduces)).toBe(true);
    expect(comparison.report.claimsSource).toBe("elevenlabs-python#849");
  });
});

describe("a bundle that cannot support a comparison says so", () => {
  it("is a stated absence rather than an empty report", () => {
    // The distinction matters: an empty table of conditions reads as "no drift
    // found", which is a finding. "No anchor" is not a finding, it is a bundle
    // with one run in it.
    const comparison = parseComparison({
      unavailable_reason: "no anchor condition: the comparison needs a VAD run",
    });

    expect(comparison.kind).toBe("unavailable");
    if (comparison.kind !== "unavailable") return;
    expect(comparison.reason).toMatch(/no anchor condition/);
  });

  it("refuses a document carrying neither shape", () => {
    expect(() => parseComparison({})).toThrow(InvalidComparisonError);
  });

  it("refuses a document carrying both, rather than picking one", () => {
    expect(() => parseComparison({ report: report(), unavailable_reason: "why not" })).toThrow(
      InvalidComparisonError,
    );
  });
});

describe("a malformed comparison fails loudly", () => {
  it("refuses a condition with no id rather than drawing an unnamed row", () => {
    const broken = report();
    delete (broken.conditions as Record<string, unknown>[])[0]!["condition_id"];

    expect(() => parseComparison(document(broken))).toThrow(/condition_id/);
  });

  it("refuses a delta that is neither a number nor absent", () => {
    // A delta of 0 renders as a real result. A delta of "100" renders as a real
    // result too, just a different one.
    const broken = report();
    (broken.conditions as Record<string, unknown>[])[0]!["delta_vs_anchor_ms"] = "0";

    expect(() => parseComparison(document(broken))).toThrow(/delta_vs_anchor_ms/);
  });

  it("refuses an anchor that claims a delta against itself", () => {
    // The anchor has no delta, and the report is the only thing that knows which
    // condition that is. Rendering one would present the baseline as a result.
    const broken = report();
    (broken.conditions as Record<string, unknown>[])[0]!["delta_vs_anchor_ms"] = 0;

    expect(() => parseComparison(document(broken))).toThrow(/anchor/i);
  });

  it("refuses a non-anchor that omits its delta", () => {
    const conditions = (report().conditions as Record<string, unknown>[])[0]!;
    const drifting = { ...conditions, condition_id: "vad_1", is_anchor: false };

    expect(() => parseComparison(document(report({ conditions: [drifting] })))).toThrow(
      /delta_vs_anchor_ms/,
    );
  });

  it("refuses a condition whose repeats are not all there", () => {
    const broken = report();
    (broken.conditions as Record<string, unknown>[])[0]!["marker_start_ms"] = [12_200, 12_300];

    expect(() => parseComparison(document(broken))).toThrow(/repeat_count/);
  });

  it("refuses a control claiming a comparison against a condition that is absent", () => {
    // The control's whole claim is about a specific pair. Rendering it against a
    // condition the bundle does not contain would assert a comparison nobody can
    // check.
    const control = {
      condition_id: "manual_2",
      manual_condition_id: "manual_2",
      compares_to_condition_id: "vad_9",
      manual_delta_ms: 0,
      vad_delta_ms: 180,
      claimed_manual_step_ms: 1,
      manual_accumulates: false,
      manual_matches_claim: true,
      interpretation: "its marker timestamp did not move",
    };

    expect(() => parseComparison(document(report({ control })))).toThrow(/vad_9/);
  });

  it("names the path that was wrong", () => {
    const broken = report();
    (broken.conditions as Record<string, unknown>[])[0]!["median_marker_ms"] = null;

    expect(() => parseComparison(document(broken))).toThrow(/conditions\[0\]\.median_marker_ms/);
  });
});

function throwMissingReport(comparison: Comparison): never {
  throw new Error(
    `expected a report, got: ${comparison.kind === "unavailable" ? comparison.reason : "?"}`,
  );
}

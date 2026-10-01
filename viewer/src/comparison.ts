/**
 * The comparison document: the published measurement, read rather than recomputed.
 *
 * `comparison.json` is written by `scripts/export_viewer.py` from
 * `scribe_timeline.analysis.compare`, over exactly the run records the bundle
 * serves. Every delta, median, spread and verdict on the page comes from this
 * file.
 *
 * That is the point of the module. Reimplementing the arithmetic here would be a
 * second implementation of the measurement, free to disagree with the README by
 * one 20 ms quantisation step, with no test able to say which one was right. So
 * the viewer parses the report and draws it, and knows nothing about how a delta
 * is defined.
 *
 * The document has two shapes. Either it carries a `report`, or it carries an
 * `unavailable_reason`: a bundle of one run, or a set with no
 * zero-preceding-commit anchor, cannot support a comparison at all. Both are
 * values rather than failures, because "this bundle holds no comparison" is a fact
 * a reader is entitled to, and a missing file would be indistinguishable from a
 * broken deployment.
 *
 * Parsing is strict -- including about fields it does not recognise. The run
 * record's contract is a generated JSON Schema with a parity test, so an unknown
 * field there cannot happen; the comparison's `to_dict` is written by hand beside
 * this file, so an unknown field means the viewer is older than the report and
 * would silently drop a figure the page is supposed to show.
 */

import {
  InvalidJsonFieldError,
  nullableNumber,
  requireArray,
  requireBoolean,
  requireCommitStrategy,
  requireExactKeys,
  requireField,
  requireNumber,
  requireNumberMap,
  requireObject,
  requirePair,
  requireString,
} from "./json.js";
import type { CommitStrategy } from "./runRecord.js";

export class InvalidComparisonError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "InvalidComparisonError";
  }
}

function fail(message: string): never {
  throw new InvalidComparisonError(message);
}

/** An object carrying exactly `expected`'s fields, or a refusal naming the path.
 *
 *  The key check itself lives in `json.ts` with the rest of the field guards, so the
 *  index parser and this one cannot drift on what "a field I do not know" means. It
 *  is re-thrown as a comparison error so a caller can catch one type per document.
 */
function exactFields(value: unknown, expected: readonly string[], path: string): Record<string, unknown> {
  const object = requireObject(value, path);
  try {
    requireExactKeys(object, expected, path);
  } catch (error) {
    if (error instanceof InvalidJsonFieldError) throw new InvalidComparisonError(error.message);
    throw error;
  }
  return object;
}

const CONDITION_FIELDS = [
  "condition_id",
  "commit_strategy",
  "prior_segment_count",
  "observed_commit_count",
  "repeat_count",
  "marker_start_ms",
  "median_marker_ms",
  "spread_ms",
  "is_anchor",
  "delta_vs_anchor_ms",
  "delta_spread_ms",
  "delta_interval_ms",
] as const;

/** One condition's measurement, across all of its repeats. */
export interface ConditionSummary {
  readonly conditionId: string;
  readonly commitStrategy: CommitStrategy;
  /** Speech segments the fixture placed before the marker: the intended number of
   *  preceding commits. */
  readonly priorSegmentCount: number;
  /** Commits each repeat actually returned, one per repeat. Evidence, not intent:
   *  a run that returned a different count is not the condition its id claims. */
  readonly observedCommitCount: readonly number[];
  readonly repeatCount: number;
  /** The marker's returned timestamp in every repeat, in repeat order. Kept whole
   *  rather than reduced to a median, because one number is not a measurement. */
  readonly markerStartMs: readonly number[];
  readonly medianMarkerMs: number;
  readonly spreadMs: number;
  readonly isAnchor: boolean;
  /** Null for the anchor, which has no delta against itself. A zero here would read
   *  as "no drift" where the truth is "this is the baseline". */
  readonly deltaVsAnchorMs: number | null;
  readonly deltaSpreadMs: number | null;
  /** The range the delta took across every repeat, anchor spread included. What
   *  decides whether a claim can be told apart from a measurement. */
  readonly deltaIntervalMs: readonly [number, number] | null;
}

export interface ClaimComparison {
  readonly conditionId: string;
  readonly measuredStepMs: number;
  readonly measuredIntervalMs: readonly [number, number];
  readonly claimedStepMs: number;
  readonly differenceMs: number;
  readonly reproduces: boolean;
}

/** What the manual control concluded. A table row is not a conclusion, so the
 *  report states it. */
export interface ControlConclusion {
  /** The control's own condition. The report also names it as `manualConditionId`;
   *  the two are always the same value, kept because the report carries both. */
  readonly conditionId: string;
  readonly manualConditionId: string;
  readonly comparesToConditionId: string;
  readonly manualDeltaMs: number;
  readonly vadDeltaMs: number;
  readonly claimedManualStepMs: number;
  readonly manualAccumulates: boolean;
  readonly manualMatchesClaim: boolean;
  readonly interpretation: string;
}

/** A figure that looks like an offset and is not: the marker's returned timestamp
 *  minus the sample its clip was inserted at. Reported so a reader comparing
 *  against the original report can see it, and never used in a delta. */
export interface ConditionContext {
  readonly conditionId: string;
  readonly insertionPointGapMs: number;
  readonly clipPaddingMs: number;
  readonly note: string;
}

/** The figures that are deliberately kept out of the measurement. */
export interface ComparisonContext {
  readonly conditions: readonly ConditionContext[];
  readonly note: string;
}

export interface ComparisonReport {
  readonly markerText: string;
  readonly anchorConditionId: string;
  readonly matchRuleNote: string;
  readonly driftDetected: boolean;
  readonly conditions: readonly ConditionSummary[];
  readonly claims: readonly ClaimComparison[];
  readonly control: ControlConclusion | null;
  readonly context: ComparisonContext;
  readonly timestampQuantumMs: number;
  readonly claimsSource: string;
  readonly claimsFiled: string;
  readonly reporterClaimedOffsetMs: Readonly<Record<string, number>>;
  readonly reporterClaimedManualOffsetMs: number;
}

export type Comparison =
  | { readonly kind: "report"; readonly report: ComparisonReport }
  | { readonly kind: "unavailable"; readonly reason: string };

const REPORT_FIELDS = [
  "marker_text",
  "anchor_condition_id",
  "match_rule_note",
  "drift_detected",
  "conditions",
  "claims",
  "control",
  "context",
  "timestamp_quantum_ms",
  "claims_source",
  "claims_filed",
  "reporter_claimed_offset_ms",
  "reporter_claimed_manual_offset_ms",
] as const;

const CLAIM_FIELDS = [
  "condition_id",
  "measured_step_ms",
  "measured_interval_ms",
  "claimed_step_ms",
  "difference_ms",
  "reproduces",
] as const;

const CONTROL_FIELDS = [
  "condition_id",
  "manual_condition_id",
  "compares_to_condition_id",
  "manual_delta_ms",
  "vad_delta_ms",
  "claimed_manual_step_ms",
  "manual_accumulates",
  "manual_matches_claim",
  "interpretation",
] as const;

const CONTEXT_FIELDS = ["condition_id", "insertion_point_gap_ms", "clip_padding_ms", "note"] as const;

function parseNumberArray(value: unknown, path: string): readonly number[] {
  return requireArray(value, path).map((entry, index) => requireNumber(entry, `${path}[${index}]`));
}

function parseNullablePair(
  value: unknown,
  path: string,
): readonly [number, number] | null {
  return value === null ? null : requirePair(value, path);
}

function parseCondition(value: unknown, path: string): ConditionSummary {
  const raw = exactFields(value, CONDITION_FIELDS, path);
  const isAnchor = requireBoolean(raw["is_anchor"], `${path}.is_anchor`);
  const deltaVsAnchorMs = nullableNumber(
    requireField(raw, "delta_vs_anchor_ms", path),
    `${path}.delta_vs_anchor_ms`,
  );

  // The anchor is the baseline every other delta is measured against, so a delta
  // for it is not a small number but a category error. Rendering one would present
  // the baseline as a result.
  if (isAnchor && deltaVsAnchorMs !== null) {
    fail(
      `${path}.delta_vs_anchor_ms is ${deltaVsAnchorMs} on a condition marked is_anchor. The ` +
        `anchor is what every other delta is measured against, so it has none; a delta here ` +
        `would show the baseline as a result of the measurement.`,
    );
  }
  if (!isAnchor && deltaVsAnchorMs === null) {
    fail(
      `${path}.delta_vs_anchor_ms is missing on ${JSON.stringify(String(raw["condition_id"]))}, ` +
        `which is not the anchor. Every non-anchor condition is measured against the anchor's ` +
        `median, and reporting nothing here would read as no drift.`,
    );
  }

  const markerStartMs = parseNumberArray(raw["marker_start_ms"], `${path}.marker_start_ms`);
  const repeatCount = requireNumber(raw["repeat_count"], `${path}.repeat_count`);
  // A median whose repeats were dropped is a single number, which is the one thing
  // this project refuses to present as a measurement.
  if (markerStartMs.length !== repeatCount) {
    fail(
      `${path}.repeat_count is ${repeatCount} but marker_start_ms holds ` +
        `${markerStartMs.length} figure(s). The repeats are the evidence; a median without ` +
        `them cannot be checked.`,
    );
  }

  return {
    conditionId: requireString(raw["condition_id"], `${path}.condition_id`),
    commitStrategy: requireCommitStrategy(raw["commit_strategy"], `${path}.commit_strategy`),
    priorSegmentCount: requireNumber(raw["prior_segment_count"], `${path}.prior_segment_count`),
    observedCommitCount: parseNumberArray(
      raw["observed_commit_count"],
      `${path}.observed_commit_count`,
    ),
    repeatCount,
    markerStartMs,
    medianMarkerMs: requireNumber(raw["median_marker_ms"], `${path}.median_marker_ms`),
    spreadMs: requireNumber(raw["spread_ms"], `${path}.spread_ms`),
    isAnchor,
    deltaVsAnchorMs,
    deltaSpreadMs: nullableNumber(
      requireField(raw, "delta_spread_ms", path),
      `${path}.delta_spread_ms`,
    ),
    deltaIntervalMs: parseNullablePair(
      requireField(raw, "delta_interval_ms", path),
      `${path}.delta_interval_ms`,
    ),
  };
}

function parseClaim(value: unknown, path: string): ClaimComparison {
  const raw = exactFields(value, CLAIM_FIELDS, path);
  return {
    conditionId: requireString(raw["condition_id"], `${path}.condition_id`),
    measuredStepMs: requireNumber(raw["measured_step_ms"], `${path}.measured_step_ms`),
    measuredIntervalMs: requirePair(raw["measured_interval_ms"], `${path}.measured_interval_ms`),
    claimedStepMs: requireNumber(raw["claimed_step_ms"], `${path}.claimed_step_ms`),
    differenceMs: requireNumber(raw["difference_ms"], `${path}.difference_ms`),
    reproduces: requireBoolean(raw["reproduces"], `${path}.reproduces`),
  };
}

function parseControl(value: unknown, path: string): ControlConclusion {
  const raw = exactFields(value, CONTROL_FIELDS, path);
  return {
    conditionId: requireString(raw["condition_id"], `${path}.condition_id`),
    manualConditionId: requireString(raw["manual_condition_id"], `${path}.manual_condition_id`),
    comparesToConditionId: requireString(
      raw["compares_to_condition_id"],
      `${path}.compares_to_condition_id`,
    ),
    manualDeltaMs: requireNumber(raw["manual_delta_ms"], `${path}.manual_delta_ms`),
    vadDeltaMs: requireNumber(raw["vad_delta_ms"], `${path}.vad_delta_ms`),
    claimedManualStepMs: requireNumber(
      raw["claimed_manual_step_ms"],
      `${path}.claimed_manual_step_ms`,
    ),
    manualAccumulates: requireBoolean(raw["manual_accumulates"], `${path}.manual_accumulates`),
    manualMatchesClaim: requireBoolean(
      raw["manual_matches_claim"],
      `${path}.manual_matches_claim`,
    ),
    interpretation: requireString(raw["interpretation"], `${path}.interpretation`),
  };
}

function parseContext(value: unknown, path: string): ComparisonContext {
  const raw = exactFields(value, ["conditions", "note"], path);
  return {
    conditions: requireArray(raw["conditions"], `${path}.conditions`).map((entry, index) => {
      const entryPath = `${path}.conditions[${index}]`;
      const condition = exactFields(entry, CONTEXT_FIELDS, entryPath);
      return {
        conditionId: requireString(condition["condition_id"], `${entryPath}.condition_id`),
        insertionPointGapMs: requireNumber(
          condition["insertion_point_gap_ms"],
          `${entryPath}.insertion_point_gap_ms`,
        ),
        clipPaddingMs: requireNumber(
          condition["clip_padding_ms"],
          `${entryPath}.clip_padding_ms`,
        ),
        note: requireString(condition["note"], `${entryPath}.note`),
      };
    }),
    note: requireString(raw["note"], `${path}.note`),
  };
}

function parseReport(value: unknown, path: string): ComparisonReport {
  const raw = exactFields(value, REPORT_FIELDS, path);
  const conditions = requireArray(raw["conditions"], `${path}.conditions`).map((entry, index) =>
    parseCondition(entry, `${path}.conditions[${index}]`),
  );
  const anchorConditionId = requireString(raw["anchor_condition_id"], `${path}.anchor_condition_id`);

  // Both names have to agree. `anchor_condition_id` is what the report calls the
  // baseline and `is_anchor` is what each condition claims about itself; if they
  // disagree, one of the two is wrong and every delta in the table is measured
  // against something the report does not agree is the baseline.
  const flagged = conditions.filter((condition) => condition.isAnchor);
  if (flagged.length === 0) {
    fail(
      `${path}.anchor_condition_id names ${JSON.stringify(anchorConditionId)}, but no condition ` +
        `in this bundle is marked is_anchor. Every delta is measured against that baseline, so ` +
        `without it none of them can be checked.`,
    );
  }
  if (flagged.length > 1) {
    fail(
      `${path} marks ${flagged.map((c) => c.conditionId).join(", ")} as the anchor, and names ` +
        `${JSON.stringify(anchorConditionId)} as its anchor_condition_id. Which one is the ` +
        `baseline decides every delta, so this report cannot be read.`,
    );
  }
  if (flagged[0]!.conditionId !== anchorConditionId) {
    fail(
      `${path}.anchor_condition_id names ${JSON.stringify(anchorConditionId)}, but the condition ` +
        `marked is_anchor is ${JSON.stringify(flagged[0]!.conditionId)}. Every delta in this ` +
        `report is measured against one of them, and they cannot both be right.`,
    );
  }

  const control =
    raw["control"] === null ? null : parseControl(raw["control"], `${path}.control`);
  if (control !== null && !conditions.some((c) => c.conditionId === control.comparesToConditionId)) {
    fail(
      `${path}.control compares ${control.manualConditionId} against ` +
        `${JSON.stringify(control.comparesToConditionId)}, which is not a condition in this ` +
        `bundle. The control's claim is about that specific pair, so it cannot be shown against ` +
        `something else.`,
    );
  }

  return {
    markerText: requireString(raw["marker_text"], `${path}.marker_text`),
    anchorConditionId,
    matchRuleNote: requireString(raw["match_rule_note"], `${path}.match_rule_note`),
    driftDetected: requireBoolean(raw["drift_detected"], `${path}.drift_detected`),
    conditions,
    claims: requireArray(raw["claims"], `${path}.claims`).map((entry, index) =>
      parseClaim(entry, `${path}.claims[${index}]`),
    ),
    control,
    context: parseContext(raw["context"], `${path}.context`),
    timestampQuantumMs: requireNumber(raw["timestamp_quantum_ms"], `${path}.timestamp_quantum_ms`),
    claimsSource: requireString(raw["claims_source"], `${path}.claims_source`),
    claimsFiled: requireString(raw["claims_filed"], `${path}.claims_filed`),
    reporterClaimedOffsetMs: requireNumberMap(
      raw["reporter_claimed_offset_ms"],
      `${path}.reporter_claimed_offset_ms`,
    ),
    reporterClaimedManualOffsetMs: requireNumber(
      raw["reporter_claimed_manual_offset_ms"],
      `${path}.reporter_claimed_manual_offset_ms`,
    ),
  };
}

/**
 * Read a `comparison.json` document.
 *
 * Never throws for a document that is merely a stated absence -- that is the
 * `unavailable` shape. Throws `InvalidComparisonError` for anything else, so a
 * malformed report cannot reach the page as a table of zeros.
 */
export function parseComparison(input: unknown): Comparison {
  const root = requireObject(input, "comparison");
  const hasReport = Object.hasOwn(root, "report");
  const hasReason = Object.hasOwn(root, "unavailable_reason");

  if (hasReport && hasReason) {
    fail(
      "comparison carries both a report and an unavailable_reason. Those are opposites, so " +
        "one of them is wrong and there is no safe way to choose.",
    );
  }
  if (hasReason) {
    return { kind: "unavailable", reason: requireString(root["unavailable_reason"], "comparison.unavailable_reason") };
  }
  if (!hasReport) {
    fail(
      "comparison carries neither a report nor an unavailable_reason, so it is not a " +
        "comparison document this viewer understands.",
    );
  }
  return { kind: "report", report: parseReport(root["report"], "comparison.report") };
}

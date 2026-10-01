/**
 * The comparison, as a reader reads it.
 *
 * Every figure here is read from the exported report. The component does no
 * arithmetic: it has no definition of a delta and no knowledge that the anchor is
 * the zero-preceding-commit condition. That is the point. A viewer that subtracted
 * two timestamps would be a second implementation of the measurement, free to
 * disagree with the README by one 20 ms quantisation step with no test able to say
 * which was right.
 *
 * So the table's job is to make the discrepancy visible without the reader doing
 * any of it: the anchor's returned position sits in its own row beside every other
 * row's, and the delta is printed rather than left to be worked out.
 *
 * Two figures are kept visibly apart, because they look alike and are not:
 *
 * - **The delta**, which differences one condition's median against the anchor's.
 * - **The insertion-point gap**, which differences a timestamp against where a clip
 *   was placed. It is not an offset -- the original report states its numbers in
 *   exactly those terms, which is why it is shown at all -- and it enters no
 *   delta. It gets its own labelled block rather than a column, because a column
 *   would read as a fourth measurement.
 *
 * And one figure is never derived here: the selected run's own returned timestamp.
 * It comes from the run record the viewer already loaded, so a repeat is shown as
 * what that run said, next to the condition's median, rather than being folded
 * into it.
 */

import type { Comparison, ComparisonReport, ConditionSummary } from "./comparison.js";
import type { RunIndexEntry } from "./runIndex.js";
import { conditionOf } from "./selection.js";

export interface ConditionTableProps {
  readonly comparison: Comparison;
  readonly groups: ReadonlyMap<string, readonly RunIndexEntry[]>;
  readonly selectedRunId: string;
  /** The selected run's own returned marker timestamp, or null when it is not in
   *  hand. Shown beside the condition's median, never merged with it. */
  readonly selectedMarkerMs: number | null;
  readonly onSelect: (runId: string) => void;
}

/** A millisecond figure with thousands separators, as a reader reads it.
 *
 *  Formatting only. The value is whatever the report said, to the millisecond it
 *  was returned at; rounding it here would be a second, quieter version of the
 *  analysis.
 */
function ms(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

function signedMs(value: number): string {
  // Zero keeps its sign. A bare `0` in a delta column reads as an empty cell, and an
  // empty cell in a measurement reads as "not measured".
  return `${value >= 0 ? "+" : ""}${ms(value)}`;
}

function range(pair: readonly [number, number] | null): string {
  return pair === null ? "—" : `${ms(pair[0])}–${ms(pair[1])}`;
}

function ConditionRow({
  condition,
  runs,
  selectedRunId,
  onSelect,
}: {
  readonly condition: ConditionSummary;
  readonly runs: readonly RunIndexEntry[];
  readonly selectedRunId: string;
  readonly onSelect: (runId: string) => void;
}) {
  return (
    <tr data-testid="condition-row" data-condition-id={condition.conditionId}>
      <th scope="row">
        {condition.conditionId}
        {condition.isAnchor ? <span className="table__anchor"> (the anchor)</span> : null}
      </th>
      <td>{condition.commitStrategy}</td>
      <td>{condition.priorSegmentCount}</td>
      <td>{condition.observedCommitCount.join(" / ")}</td>
      <td className="table__figures">{condition.markerStartMs.map(ms).join(" / ")}</td>
      <td className="table__figures">{ms(condition.medianMarkerMs)}</td>
      <td className="table__figures" data-testid="spread">
        {ms(condition.spreadMs)}
      </td>
      <td className="table__figures" data-testid="delta">
        {condition.isAnchor ? (
          // Not "0". A delta of zero against itself would read as "no drift", where
          // the truth is that this is the baseline every other row is measured from.
          <span className="table__baseline">baseline</span>
        ) : (
          signedMs(condition.deltaVsAnchorMs ?? 0)
        )}
      </td>
      <td className="table__figures">{range(condition.deltaIntervalMs)}</td>
      <td>
        {runs.length === 0 ? null : (
          <span className="table__repeats">
            {runs.map((run) => (
              <button
                key={run.runId}
                type="button"
                className="table__repeat"
                aria-pressed={(run.runId === selectedRunId)}
                onClick={() => onSelect(run.runId)}
              >
                rep {run.repeatIndex}
              </button>
            ))}
          </span>
        )}
      </td>
    </tr>
  );
}

function Report({
  report,
  groups,
  selectedRunId,
  selectedMarkerMs,
  onSelect,
}: {
  readonly report: ComparisonReport;
  readonly groups: ReadonlyMap<string, readonly RunIndexEntry[]>;
  readonly selectedRunId: string;
  readonly selectedMarkerMs: number | null;
  readonly onSelect: (runId: string) => void;
}) {
  const anchor = report.conditions.find((condition) => condition.isAnchor);
  const selected = conditionOf(groups, selectedRunId);

  return (
    <>
      <p className="table__rule" data-testid="measurement-rule">
        {report.matchRuleNote}
      </p>
      <p className="table__rule table__rule--quiet">
        Every figure below is read from the exported report; no arithmetic is done in this page.
      </p>

      {anchor === undefined ? null : (
        <p className="table__baseline-line" data-testid="anchor-baseline">
          The anchor is <strong>{anchor.conditionId}</strong>, which had nothing preceding the
          marker: it returned <strong>{ms(anchor.medianMarkerMs)} ms</strong> across{" "}
          {anchor.repeatCount} repeat{anchor.repeatCount === 1 ? "" : "s"}. Everything below is
          measured against this.
        </p>
      )}

      {selectedMarkerMs === null ? null : (
        <p className="table__selected" data-testid="selected-run">
          The run on screen is <strong>{selectedRunId}</strong>, which returned the marker at{" "}
          <strong>{ms(selectedMarkerMs)} ms</strong>
          {selected === undefined ? null : (
            <>
              {" "}
              — repeat {selected.find((run) => run.runId === selectedRunId)?.repeatIndex} of{" "}
              {selected[0]!.conditionId}, which is a single run rather than the condition&rsquo;s
              median
            </>
          )}
          .
        </p>
      )}

      <table className="table" data-testid="conditions-table">
        <caption>
          Each condition holds the marker&rsquo;s bytes and sample position identical and varies
          only how much speech precedes it.
        </caption>
        <thead>
          <tr>
            <th scope="col">Condition</th>
            <th scope="col">Strategy</th>
            <th scope="col">Speech before</th>
            <th scope="col">Commits returned</th>
            <th scope="col">Marker returned, every repeat (ms)</th>
            <th scope="col">Median (ms)</th>
            <th scope="col">Spread (ms)</th>
            <th scope="col">Delta vs anchor (ms)</th>
            <th scope="col">Delta range (ms)</th>
            <th scope="col">Run</th>
          </tr>
        </thead>
        <tbody>
          {report.conditions.map((condition) => (
            <ConditionRow
              key={condition.conditionId}
              condition={condition}
              runs={groups.get(condition.conditionId) ?? []}
              selectedRunId={selectedRunId}
              onSelect={onSelect}
            />
          ))}
        </tbody>
      </table>

      <ClaimNotes report={report} />
      <ControlNote report={report} />
      <ContextNote report={report} />
    </>
  );
}

/** The reported claim, next to what was measured, with the quantum that sets what
 *  counts as agreement. */
function ClaimNotes({ report }: { readonly report: ComparisonReport }) {
  return (
    <section className="table__note" role="note" aria-labelledby="claims-heading">
      <h3 id="claims-heading">Against the reported claim</h3>
      <p>
        The claim is from {report.claimsSource}, filed {report.claimsFiled}, and states offsets
        from the clip&rsquo;s insertion point — so only the step between conditions is comparable,
        and that is what is compared. Returned timestamps land on {ms(report.timestampQuantumMs)} ms
        steps, so a difference within one of a claimed step counts as matching it.
      </p>
      <table className="table table--claims">
        <thead>
          <tr>
            <th scope="col">Condition</th>
            <th scope="col">Claimed step (ms)</th>
            <th scope="col">Measured step (ms)</th>
            <th scope="col">Difference (ms)</th>
            <th scope="col">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {report.claims.map((claim) => (
            <tr key={claim.conditionId}>
              <th scope="row">{claim.conditionId}</th>
              <td className="table__figures">{signedMs(claim.claimedStepMs)}</td>
              <td className="table__figures">
                {signedMs(claim.measuredStepMs)} ({range(claim.measuredIntervalMs)})
              </td>
              <td className="table__figures">{signedMs(claim.differenceMs)}</td>
              <td>{claim.reproduces ? "reproduces" : "does not reproduce"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/** The manual control's conclusion, stated rather than left to a table row. */
function ControlNote({ report }: { readonly report: ComparisonReport }) {
  const control = report.control;
  return (
    <section className="table__note" role="note" aria-labelledby="control-heading">
      <h3 id="control-heading">The manual control</h3>
      {control === null ? (
        <p>
          This bundle holds no manual control, so nothing here says whether offsets accumulate
          under manual commits.
        </p>
      ) : (
        <>
          <p>
            <strong>{control.manualConditionId}</strong> vs <strong>{control.comparesToConditionId}</strong>{" "}
            — the same audio with the same number of preceding commits, differing only in who chose
            the cut points: {signedMs(control.manualDeltaMs)} ms under manual against{" "}
            {signedMs(control.vadDeltaMs)} ms under VAD.
          </p>
          <p data-testid="control-conclusion">
            {control.manualAccumulates
              ? "Offsets DO accumulate under manual commits too, so the commit count rather than the VAD trigger is the operative variable."
              : "Offsets do NOT accumulate under manual commits, so the drift under VAD is attributable to VAD's commit triggering rather than to the number of commits."}
          </p>
          <p className="table__rule--quiet">{control.interpretation}</p>
        </>
      )}
    </section>
  );
}

/** The figures that look like offsets and are not.
 *
 *  A separate block, not a column. The original report states its numbers against the
 *  clip's insertion point, so a reader comparing the two needs to see these — and
 *  needs them clearly marked as a different quantity, or they would read as the
 *  measurement.
 */
function ContextNote({ report }: { readonly report: ComparisonReport }) {
  return (
    <section className="table__note" role="note" aria-labelledby="context-heading">
      <h3 id="context-heading">Not a measurement</h3>
      <p>{report.context.note}</p>
      <table className="table table--context">
        <thead>
          <tr>
            <th scope="col">Condition</th>
            <th scope="col">Marker timestamp minus its clip&rsquo;s insertion point (ms)</th>
          </tr>
        </thead>
        <tbody>
          {report.context.conditions.map((entry) => (
            <tr key={entry.conditionId}>
              <th scope="row">{entry.conditionId}</th>
              <td className="table__figures">{signedMs(entry.insertionPointGapMs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function ConditionTable(props: ConditionTableProps) {
  const { comparison } = props;
  return (
    <section className="comparison" aria-labelledby="comparison-heading">
      <h2 id="comparison-heading">The comparison</h2>
      {comparison.kind === "unavailable" ? (
        <>
          {/* An empty table of conditions would read as "no drift found", which is a
              finding. A bundle with no anchor supports no comparison at all, and
              saying so is the honest answer. The runs below it still stand on their
              own. */}
          <p className="app--error" role="alert">
            This bundle cannot support a comparison: {comparison.reason}. Nothing here says
            whether the offsets drift.
          </p>
          <RunList {...props} />
        </>
      ) : (
        <Report {...props} report={comparison.report} />
      )}
    </section>
  );
}

/** The runs on offer, shown even when there is no comparison to put them in.
 *
 *  One run is not a measurement, but it is the audio and the returned words, and a
 *  reader who came for that should not be told there is nothing here.
 */
function RunList({
  groups,
  selectedRunId,
  onSelect,
}: Pick<ConditionTableProps, "groups" | "selectedRunId" | "onSelect">) {
  return (
    <table className="table" data-testid="run-list">
      <caption>The runs in this bundle, and the figures their own records carry</caption>
      <thead>
        <tr>
          <th scope="col">Condition</th>
          <th scope="col">Strategy</th>
          <th scope="col">Run</th>
        </tr>
      </thead>
      <tbody>
        {[...groups.entries()].map(([conditionId, runs]) =>
          runs.map((run) => (
            <tr key={run.runId}>
              <th scope="row">{conditionId}</th>
              <td>{run.commitStrategy}</td>
              <td>
                <button
                  type="button"
                  className="table__repeat"
                  aria-pressed={(run.runId === selectedRunId)}
                  onClick={() => onSelect(run.runId)}
                >
                  rep {run.repeatIndex}
                </button>
              </td>
            </tr>
          )),
        )}
      </tbody>
    </table>
  );
}

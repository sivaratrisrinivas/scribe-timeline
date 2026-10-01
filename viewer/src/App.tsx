/**
 * The page as a reader arrives at it.
 *
 * Two things changed here for issue #6. The page now reads the bundle's comparison
 * as well as its run record, so the figures on screen are the exported report's
 * rather than anything computed in the browser; and it now loads *one* run out of
 * twelve on demand, so a reader can move between strategies, conditions and
 * repeats without leaving the page.
 *
 * The selection lives in the URL, as `?run=`. That was already there for deep links
 * and it stays the single source of truth: every switch sets it, and a link to a
 * particular run is the thing a maintainer pastes into an issue. Deriving the
 * condition and repeat from the run's own index entry, rather than adding two more
 * parameters, keeps one vocabulary -- and keeps the picker honest, because every
 * switch navigates to a run that exists rather than to a coordinate that has to be
 * resolved afterwards.
 *
 * Every failure still renders as a message naming what went wrong. A page that
 * quietly showed fewer conditions than the bundle holds would read as a finding of
 * "no drift", which is the one reading this project cannot survive.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { ConditionTable } from "./ConditionTable.js";
import { InvalidComparisonError, parseComparison, type Comparison } from "./comparison.js";
import { ElementPlayback, type Playback } from "./playback.js";
import { RunPicker } from "./RunPicker.js";
import { RunViewer } from "./RunViewer.js";
import {
  groupByCondition,
  InvalidRunIndexError,
  parseRunIndex,
  type RunIndexEntry,
} from "./runIndex.js";
import { InvalidRunRecordError, parseRunRecord } from "./runRecord.js";
import { entryFor, type Selection } from "./selection.js";
import {
  buildTimeline,
  MarkerTextAbsentError,
  type Timeline,
  UnimplementedMatchRuleError,
} from "./timeline.js";

const INDEX_URL = "runs.json";
const COMPARISON_URL = "comparison.json";

/** What the bundle holds, read once and kept: it describes every run, not the
 *  selected one, so re-reading it per switch would fetch the same file to change
 *  one run. */
interface Bundle {
  readonly note: string;
  readonly groups: ReadonlyMap<string, readonly RunIndexEntry[]>;
  readonly runIds: readonly string[];
}

type RunState =
  | { readonly status: "loading" }
  | { readonly status: "failed"; readonly message: string }
  | { readonly status: "loaded"; readonly entry: RunIndexEntry; readonly timeline: Timeline };

async function getJson(url: string): Promise<unknown> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as unknown;
}

/** A failure the reader can act on, rather than a stack trace or a blank page. */
function describeFailure(error: unknown): string {
  if (error instanceof InvalidRunRecordError) {
    return `This run record is not valid, so nothing is shown: ${error.message}. Showing a partial timeline would be worse than showing none, because a missing value would look like a result.`;
  }
  if (error instanceof InvalidRunIndexError) {
    return `This bundle's index is not valid, so nothing is shown: ${error.message}. The index is what says which runs exist, so guessing past it could show a reader a different run than the one they asked for.`;
  }
  if (error instanceof InvalidComparisonError) {
    return `This bundle's comparison is not valid, so its figures are withheld: ${error.message}. A delta rendered from a malformed report would be indistinguishable from a real one.`;
  }
  if (error instanceof MarkerTextAbsentError) {
    return `This run supports no measurement: ${error.message}`;
  }
  if (error instanceof UnimplementedMatchRuleError) {
    return `This run cannot be shown faithfully: ${error.message}`;
  }
  if (error instanceof Error) return error.message;
  return String(error);
}

function requestedRunId(): string | null {
  const requested = new URLSearchParams(window.location.search).get("run");
  return requested === null || requested === "" ? null : requested;
}

function writeRunToUrl(runId: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("run", runId);
  // `replaceState`, not `pushState`: moving between conditions is browsing within one
  // piece of evidence, and a reader comparing four conditions should not have to press
  // Back four times to reach the page they came from. The run id is the link a
  // maintainer shares, and one that grows a history of every click they made.
  window.history.replaceState({}, "", url);
}

export function App() {
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunState>({ status: "loading" });
  // The audio element arrives via a ref callback rather than `useRef`, because the
  // transport is built from the element and `useRef` would not trigger a render when
  // it attaches.
  const [audioElement, setAudioElement] = useState<HTMLAudioElement | null>(null);

  // One transport per element. Creating it during render would attach a fresh set of
  // listeners on every render and re-subscribe the viewer to a new object each time,
  // so it is built once the element exists and torn down when it goes. StrictMode
  // mounts twice in development, so the teardown is load-bearing rather than hygiene:
  // without it the first transport keeps listening to a live `<audio>`.
  const playback: Playback | null = useMemo(
    () => (audioElement === null ? null : new ElementPlayback(audioElement)),
    [audioElement],
  );
  useEffect(() => () => playback?.dispose(), [playback]);

  useEffect(() => {
    let cancelled = false;
    async function loadBundle(): Promise<void> {
      const [index, comparisonDocument] = await Promise.all([
        getJson(INDEX_URL),
        getJson(COMPARISON_URL),
      ]);
      const parsed = parseRunIndex(index);
      const loaded: Bundle = {
        note: parsed.note,
        groups: groupByCondition(parsed.runs),
        runIds: parsed.runs.map((entry) => entry.runId),
      };
      if (cancelled) return;
      setBundle(loaded);
      setComparison(parseComparison(comparisonDocument));
      // The URL wins when it names a run; otherwise the first one the bundle lists.
      // An unrecognised name is passed through rather than replaced, so it is
      // refused below instead of quietly showing a reader a different run.
      setRunId(requestedRunId() ?? loaded.runIds[0] ?? null);
    }
    void loadBundle().catch((error: unknown) => {
      if (!cancelled) setRun({ status: "failed", message: describeFailure(error) });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (bundle === null || runId === null) return;
    // Held in a local, because the loader closes over it and TypeScript will not carry
    // the narrowing of the outer `bundle` into a callback.
    const index = bundle;
    let cancelled = false;
    async function loadRun(): Promise<void> {
      // Reset on every switch, so the previous run's timeline is never on screen
      // under a heading that now names a different one. A stale timeline labelled
      // with the wrong condition is worse than a brief pause.
      setRun({ status: "loading" });
      const entry = [...index.groups.values()]
        .flat()
        .find((candidate) => candidate.runId === runId);
      if (entry === undefined) {
        throw new Error(
          `No run named ${JSON.stringify(runId)} is in this bundle. Available: ` +
            index.runIds.join(", "),
        );
      }
      const record = parseRunRecord(await getJson(`runs/${entry.runId}.json`));
      // Derived here, at load, rather than during render. A record that cannot become
      // a timeline -- an absent marker, an unimplemented match rule -- then fails
      // through the same reported path as a bad fetch, instead of escaping a render
      // and taking the page down with an unhandled error.
      const timeline = buildTimeline(record, entry.commits);
      if (!cancelled) setRun({ status: "loaded", entry, timeline });
    }
    void loadRun().catch((error: unknown) => {
      if (!cancelled) setRun({ status: "failed", message: describeFailure(error) });
    });
    return () => {
      cancelled = true;
    };
  }, [bundle, runId]);

  const selectRun = useCallback((id: string) => {
    writeRunToUrl(id);
    setRunId(id);
  }, []);

  const select = useCallback(
    (selection: Selection) => {
      if (bundle === null) return;
      const entry = entryFor(bundle.groups, selection);
      // A selection the bundle does not hold is ignored rather than guessed at: the
      // index is the only thing that knows which runs exist.
      if (entry !== undefined) selectRun(entry.runId);
    },
    [bundle, selectRun],
  );

  if (run.status === "failed" || bundle === null || comparison === null || run.status === "loading") {
    if (run.status === "failed") {
      return (
        <main className="app app--notice app--error" role="alert">
          <h1>This run cannot be shown</h1>
          <p>{run.message}</p>
        </main>
      );
    }
    return <main className="app app--notice">Loading saved runs…</main>;
  }

  const { entry, timeline } = run;
  const selection: Selection = { conditionId: entry.conditionId, repeatIndex: entry.repeatIndex };

  return (
    <main className="app">
      <h1>Scribe Realtime — saved runs</h1>
      <p className="app__note">{bundle.note}</p>
      <RunPicker groups={bundle.groups} selection={selection} onSelect={select} />
      <ConditionTable
        comparison={comparison}
        groups={bundle.groups}
        selectedRunId={entry.runId}
        selectedMarkerMs={measuredMarkerMs(timeline, comparison)}
        onSelect={selectRun}
      />
      <audio
        ref={setAudioElement}
        className="app__audio"
        aria-label="Run audio"
        controls
        preload="auto"
        src={entry.hasAudio ? entry.audioPath : undefined}
      >
        {entry.hasAudio ? null : <p>This run has no rebuilt audio, so there is nothing to play.</p>}
      </audio>
      {entry.hasAudio ? null : (
        <p className="app--error" role="alert">
          The audio for this run could not be rebuilt from the committed clips, so it is not
          offered here. The words and timestamps below are the run record&rsquo;s own.
        </p>
      )}
      {playback === null ? null : <RunViewer timeline={timeline} playback={playback} />}
    </main>
  );
}

/** The measured marker's returned timestamp, when this run's timeline carries it.
 *
 *  Matched by the report's own marker text, not by "the first marker": a record
 *  naming two markers would otherwise have one of them presented as the measured
 *  word, which is the kind of wrong answer that looks entirely plausible.
 *
 *  Null when the report and the run disagree about which word was measured, and null
 *  when no comparison has loaded -- there being no marker text to check against. The
 *  figure is withheld rather than guessed, because the comparison quotes this run's
 *  median and a per-run figure from the wrong word would sit beside it unchallenged.
 */
function measuredMarkerMs(timeline: Timeline, comparison: Comparison): number | null {
  if (comparison.kind !== "report") return null;
  const wanted = comparison.report.markerText.toLocaleLowerCase();
  const match = timeline.markers.find(
    (marker) => marker.text.toLocaleLowerCase() === wanted,
  );
  return match?.returnedWord.startMs ?? null;
}

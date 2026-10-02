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
import { conditionOf, entryFor, type Selection } from "./selection.js";
import {
  buildTimeline,
  MarkerTextAbsentError,
  normaliseToken,
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
  | {
      readonly status: "failed";
      readonly message: string;
      /** Which of the three documents failed. A bundle that cannot be read has no
       *  run to show, and saying otherwise sends a reader after the wrong file. */
      readonly scope: "bundle" | "run";
    }
  | { readonly status: "loaded"; readonly entry: RunIndexEntry; readonly timeline: Timeline };

async function getJson(url: string): Promise<unknown> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status} ${response.statusText}`);
  }
  try {
    return (await response.json()) as unknown;
  } catch (error) {
    // Named, and named as a whole-document failure. A static host asked for a file
    // it does not have answers with the site's own index page and a 200 on several
    // configurations, so the browser's complaint is about a token, on a page, in a
    // file the reader never named. The most useful thing to say is which file, and
    // that whatever came back was not the document.
    throw new Error(
      `${url} did not return JSON (${error instanceof Error ? error.message : String(error)}). ` +
        `Nothing is shown from it: a page that guessed at the contents of a file it ` +
        `could not read would be guessing at the evidence. If the bundle was deployed ` +
        `by hand, check that this file was deployed.`,
    );
  }
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
  // Whether the current run's audio element has failed to load. Held apart from the
  // run itself, because the run is readable either way: the record, the returned
  // words and the commit boundaries do not depend on the WAV having been deployed.
  const [audioFailed, setAudioFailed] = useState(false);
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
      setComparison(parseComparisonSafely(comparisonDocument));
      // The URL wins when it names a run; otherwise the first one the bundle lists.
      // An unrecognised name is passed through rather than replaced, so it is
      // refused below instead of quietly showing a reader a different run.
      setRunId(requestedRunId() ?? loaded.runIds[0] ?? null);
    }
    void loadBundle().catch((error: unknown) => {
      if (!cancelled) {
        setRun({ status: "failed", scope: "bundle", message: describeFailure(error) });
      }
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
    const wanted = runId;
    let cancelled = false;
    async function loadRun(): Promise<void> {
      // Reset on every switch, so the previous run's timeline is never on screen
      // under a heading that now names a different one. A stale timeline labelled
      // with the wrong condition is worse than a brief pause. The audio failure goes
      // with it: it belongs to the run that was on screen when it happened.
      setRun({ status: "loading" });
      setAudioFailed(false);
      const entry = conditionOf(index.groups, wanted)?.find((run) => run.runId === wanted);
      if (entry === undefined) {
        throw new Error(
          `No run named ${JSON.stringify(wanted)} is in this bundle. Available: ` +
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
      if (!cancelled) {
        setRun({ status: "failed", scope: "run", message: describeFailure(error) });
      }
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
          <h1>
            {run.scope === "bundle"
              ? "This bundle cannot be read"
              : "This run cannot be shown"}
          </h1>
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
        onError={() => setAudioFailed(true)}
      >
        {entry.hasAudio ? null : <p>This run has no rebuilt audio, so there is nothing to play.</p>}
      </audio>
      {entry.hasAudio && audioFailed ? (
        // The browser could not load the WAV. What the page does not know is why, and
        // the possibilities are a file that was not deployed and a file that was
        // deployed broken -- so the message states the failure and not a cause. A
        // control that sits there doing nothing reads as a recording that is silent,
        // which is a claim about the audio the page cannot support.
        <p className="app--error" role="alert">
          This run&rsquo;s audio could not be loaded from <code>{entry.audioPath}</code>, so there
          is nothing to play. The index says the audio was rebuilt, so the file is either
          absent from where this page was served or unreadable there. Everything below is
          the run record&rsquo;s own, and is unaffected.
        </p>
      ) : null}
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

/**
 * Read the comparison, or say why it could not be read.
 *
 *  The comparison is one of three documents the page reads, and it is the only
 *  source of the deltas -- but a run record is evidence in its own right, and it is
 *  the only source of the audio, the returned words and the commit boundaries. So a
 *  malformed report withholds *its* figures and leaves the run standing, rather than
 *  taking the evidence down with it. A page that loses the track and the audio
 *  because a summary could not be read has thrown away more than it protected.
 */
function parseComparisonSafely(document: unknown): Comparison {
  try {
    return parseComparison(document);
  } catch (error) {
    if (error instanceof InvalidComparisonError) {
      return { kind: "unavailable", reason: `this report could not be read: ${error.message}` };
    }
    throw error;
  }
}

/**
 * The measured marker's returned timestamp, when this run's timeline carries it.
 *
 *  Located under the *recorded* match rule, the same one the runner applied and the
 *  same one the timeline already used to place the marker. A looser rule here would
 *  be a second rule quietly deciding which word was measured, and it would fail
 *  silently: a marker the server returned as `Zorblax,` would stop matching, the
 *  figure would vanish, and nothing on the page would say why.
 *
 *  Null when the report and the run disagree about which word was measured, and null
 *  when no report has loaded -- there being no marker text to check against. The
 *  figure is withheld rather than guessed, because the comparison quotes this run's
 *  median and a per-run figure for the wrong word would sit beside it unchallenged.
 */
function measuredMarkerMs(timeline: Timeline, comparison: Comparison): number | null {
  if (comparison.kind !== "report") return null;
  const wanted = normaliseToken(comparison.report.markerText);
  const match = timeline.markers.find((marker) => normaliseToken(marker.text) === wanted);
  return match?.returnedWord.startMs ?? null;
}

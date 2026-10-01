/**
 * Loading one saved run record, and wiring it to a real audio element.
 *
 * `RunViewer` is transport-agnostic so it can be tested without a browser. This is
 * where the browser half lives: the `<audio>` element the reader actually hears,
 * and the adapter that lets the viewer read it.
 *
 * The record is chosen from `?run=<run_id>` in the URL, falling back to the first
 * one the index lists. Only one run is shown: switching between conditions is the
 * comparison view's job, and a picker here would invite a reader to compare two
 * single runs as though that were a measurement.
 *
 * Every failure renders as a message naming what went wrong. A record that cannot
 * be parsed, or a run whose audio was not rebuilt, shows why -- it never renders as
 * an empty timeline, because an empty timeline is indistinguishable from a run that
 * returned no words.
 */

import { useEffect, useMemo, useState } from "react";

import { ElementPlayback, type Playback } from "./playback.js";
import { RunViewer } from "./RunViewer.js";
import { InvalidRunRecordError, parseRunRecord } from "./runRecord.js";
import {
  buildTimeline,
  MarkerTextAbsentError,
  type Timeline,
  UnimplementedMatchRuleError,
} from "./timeline.js";

const INDEX_URL = "runs.json";

export interface RunIndexEntry {
  readonly run_id: string;
  readonly condition_id: string;
  readonly commit_strategy: string;
  readonly repeat_index: number;
  readonly audio_path: string;
  readonly has_audio: boolean;
}

interface RunIndex {
  readonly note: string;
  readonly runs: readonly RunIndexEntry[];
}

type LoadState =
  | { readonly status: "loading" }
  | { readonly status: "failed"; readonly message: string }
  | {
      readonly status: "loaded";
      readonly entry: RunIndexEntry;
      readonly timeline: Timeline;
    };

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

/** A failure the reader can act on, rather than a stack trace or a blank page. */
function describeFailure(error: unknown): string {
  if (error instanceof InvalidRunRecordError) {
    return `This run record is not valid, so nothing is shown: ${error.message}. Showing a partial timeline would be worse than showing none, because a missing value would look like a result.`;
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

export function App() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  // The audio element arrives via a ref callback rather than `useRef`, because the
  // transport is built from the element and `useRef` would not trigger a render when
  // it attaches.
  const [audioElement, setAudioElement] = useState<HTMLAudioElement | null>(null);

  // One transport per element. Creating it during render would attach a fresh set
  // of listeners on every render and re-subscribe the viewer to a new object each
  // time, so it is built once the element exists and torn down when it goes.
  // StrictMode mounts twice in development, so the teardown is load-bearing, not
  // hygiene: without it the first transport keeps listening to a live `<audio>`.
  const playback: Playback | null = useMemo(
    () => (audioElement === null ? null : new ElementPlayback(audioElement)),
    [audioElement],
  );
  useEffect(() => () => playback?.dispose(), [playback]);

  useEffect(() => {
    let cancelled = false;
    async function load(): Promise<void> {
      try {
        const index = await getJson<RunIndex>(INDEX_URL);
        const requested = requestedRunId();
        if (requested !== null && !index.runs.some((run) => run.run_id === requested)) {
          throw new Error(
            `No run named ${JSON.stringify(requested)} is in this bundle. Available: ` +
              index.runs.map((run) => run.run_id).join(", "),
          );
        }
        const entry =
          requested === null
            ? index.runs[0]
            : index.runs.find((run) => run.run_id === requested);
        if (entry === undefined) throw new Error("The bundle lists no runs to show.");

        const record = parseRunRecord(await getJson<unknown>(`runs/${entry.run_id}.json`));
        // Derived here, at load, rather than during render. A record that cannot
        // become a timeline -- an absent marker, an unimplemented match rule -- then
        // fails through the same reported path as a bad fetch, instead of escaping a
        // render and taking the page down with an unhandled error.
        const timeline = buildTimeline(record);
        if (!cancelled) setState({ status: "loaded", entry, timeline });
      } catch (error) {
        if (!cancelled) setState({ status: "failed", message: describeFailure(error) });
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") {
    return <main className="app app--notice">Loading saved run…</main>;
  }

  if (state.status === "failed") {
    return (
      <main className="app app--notice app--error" role="alert">
        <h1>This run cannot be shown</h1>
        <p>{state.message}</p>
      </main>
    );
  }

  const { entry, timeline } = state;

  return (
    <main className="app">
      <h1>Scribe Realtime — saved run</h1>
      <audio
        ref={setAudioElement}
        className="app__audio"
        aria-label="Run audio"
        controls
        preload="auto"
        src={entry.has_audio ? entry.audio_path : undefined}
      >
        {entry.has_audio ? null : <p>This run has no rebuilt audio, so there is nothing to play.</p>}
      </audio>
      {entry.has_audio ? null : (
        <p className="app--error" role="alert">
          The audio for this run could not be rebuilt from the committed clips, so it is not
          offered here. The words and timestamps below are the run record&rsquo;s own.
        </p>
      )}
      {playback === null ? null : <RunViewer timeline={timeline} playback={playback} />}
    </main>
  );
}

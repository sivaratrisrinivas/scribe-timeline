/**
 * The timeline, as a reader sees it.
 *
 * This component draws what `timeline.ts` computed and does no arithmetic of its
 * own. That split is the point: every number on screen was placed by the model,
 * which has tests around the conversions that could silently mislead (samples
 * against milliseconds, an insertion point against a returned timestamp). A
 * component that computed positions as it rendered would put those mistakes
 * somewhere with no tests.
 *
 * Two things it deliberately does *not* do:
 *
 * - **Subtract the marker's two positions.** The gap between them is the
 *   measurement, and one run is not a measurement. Comparing across conditions is
 *   the comparison view's job; subtracting them here would assert an offset from a
 *   single run, which is the mistake this project exists to avoid.
 * - **Snap anything to where a clip was inserted.** The returned word is drawn
 *   where the server said it was. That is the whole observation.
 */

import { useEffect, useState } from "react";

import type { Playback } from "./playback.js";
import { activeWordAt, type Timeline, type TimelineWord } from "./timeline.js";

/** A millisecond figure as a reader reads it, without decimal noise. */
function formatMs(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function percent(fraction: number): string {
  return `${fraction * 100}%`;
}

interface WordMarkerProps {
  readonly word: TimelineWord;
  readonly sounding: boolean;
  readonly onSeek: (ms: number) => void;
}

function WordMarker({ word, sounding, onSeek }: WordMarkerProps) {
  const label = word.isMarker
    ? `Marker: ${word.text}, returned at ${formatMs(word.startMs)} ms`
    : `${word.text}, returned at ${formatMs(word.startMs)} ms`;

  return (
    <button
      type="button"
      className="word-marker"
      data-testid="word-marker"
      data-sounding={String(sounding)}
      data-marker={String(word.isMarker)}
      data-within-audio={String(word.withinAudio)}
      style={{
        left: percent(word.startFraction),
        // A floor on the width, so a short word is still a click target rather than
        // a hairline. It never overlaps its neighbour: the words here are seconds
        // apart on an eighteen-second track.
        width: percent(Math.max(word.endFraction - word.startFraction, 0.005)),
      }}
      title={label}
      onClick={() => onSeek(word.startMs)}
    >
      <span className="word-marker__text">{word.text}</span>
      <span className="word-marker__time">{formatMs(word.startMs)} ms</span>
    </button>
  );
}

export interface RunViewerProps {
  readonly timeline: Timeline;
  readonly playback: Playback;
}

export function RunViewer({ timeline, playback }: RunViewerProps) {
  const [timeMs, setTimeMs] = useState<number | null>(null);
  useEffect(() => playback.subscribe(setTimeMs), [playback]);

  const playheadFraction =
    timeMs === null ? null : Math.min(Math.max(timeMs / timeline.durationMs, 0), 1);
  const sounding = timeMs === null ? null : activeWordAt(timeline.words, timeMs);

  return (
    <article className="run-viewer">
      <header className="run-viewer__header">
        {/* The condition and the strategy in one heading, so the pair a reader must
            never confuse -- a VAD run and a manual control -- is read as one name. */}
        <h2>
          {timeline.conditionId} <span className="run-viewer__strategy">({timeline.commitStrategy})</span>
        </h2>
        <p className="run-viewer__run">
          repeat {timeline.repeatIndex} · run <code>{timeline.runId}</code>
        </p>
        <p className="run-viewer__provenance">
          Saved evidence, replayed from committed clips. <strong>No capture was re-run</strong> and
          no API call was made to produce this page.
        </p>
        <p className="run-viewer__units">
          The API returned word timestamps in <strong>{timeline.sourceTimestampUnit}</strong>. The
          run record converts them to milliseconds once, on ingest, and every figure below is that
          converted value. The unconverted values stay in the record&rsquo;s raw{" "}
          <code>events</code>, and the viewer converts nothing itself.
        </p>
      </header>

      <div
        className="track"
        role="group"
        aria-label={`Returned words across ${formatMs(timeline.durationMs)} ms of audio`}
      >
        <div className="track__rail" aria-hidden="true" />
        {timeline.markers.map((marker) => (
          <div
            key={marker.text}
            className="track__insertion"
            data-testid="marker-insertion"
            style={{ left: percent(marker.insertionPointMs / timeline.durationMs) }}
            aria-hidden="true"
          />
        ))}
        {timeline.words.map((word) => (
          <WordMarker
            key={`${word.text}-${word.startMs}`}
            word={word}
            sounding={sounding !== null && word === sounding}
            onSeek={(ms) => playback.seek(ms)}
          />
        ))}
        {playheadFraction !== null && (
          <div
            className="track__playhead"
            data-testid="playhead"
            style={{ left: percent(playheadFraction) }}
            aria-hidden="true"
          />
        )}
      </div>

      {timeline.markers.map((marker) => (
        <p key={marker.text} className="marker-figures">
          <strong>{marker.text}</strong>: clip inserted at {formatMs(marker.insertionPointMs)} ms
          (sample {marker.insertionPointSample}) · returned at {formatMs(marker.returnedWord.startMs)} ms
        </p>
      ))}

      <table className="words">
        <caption>Returned words, exactly as the API reported them</caption>
        <thead>
          <tr>
            <th scope="col">Word</th>
            <th scope="col">Start (ms)</th>
            <th scope="col">End (ms)</th>
            <th scope="col">logprob</th>
          </tr>
        </thead>
        <tbody>
          {timeline.words.map((word) => (
            <tr
              key={`${word.text}-${word.startMs}-row`}
              data-within-audio={String(word.withinAudio)}
            >
              <th scope="row">{word.text}</th>
              <td>{formatMs(word.startMs)}</td>
              <td>{formatMs(word.endMs)}</td>
              <td>{word.logprob === null ? "—" : word.logprob.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </article>
  );
}

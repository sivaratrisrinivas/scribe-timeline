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
import {
  activeWordAt,
  type Timeline,
  type TimelineCommit,
  type TimelineWord,
} from "./timeline.js";

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

/** The silence one commit was cut from, as a reader is told about it.
 *
 *  The record does not say where inside this gap the server committed -- only that
 *  the previous commit ended here and this one began there. So the band is the
 *  whole of what can honestly be claimed, and the wording says so.
 */
function boundaryLabel(commit: TimelineCommit): string {
  return `commit ${commit.commitIndex} was cut somewhere between ${formatMs(commit.boundaryFromMs!)} ms and ${formatMs(commit.boundaryToMs!)} ms; the record does not say where within it`;
}

function CommitBoundary({ commit }: { readonly commit: TimelineCommit }) {
  if (commit.boundaryFromFraction === null || commit.boundaryToFraction === null) return null;
  return (
    <div
      className="track__boundary"
      data-testid="commit-boundary"
      style={{
        left: percent(commit.boundaryFromFraction),
        width: percent(Math.max(commit.boundaryToFraction - commit.boundaryFromFraction, 0.002)),
      }}
      title={boundaryLabel(commit)}
      aria-hidden="true"
    />
  );
}

function CommitSpan({ commit }: { readonly commit: TimelineCommit }) {
  if (commit.firstFraction === null || commit.lastFraction === null) return null;
  return (
    <div
      className="track__commit"
      data-testid="commit-span"
      data-commit-index={String(commit.commitIndex)}
      style={{
        left: percent(commit.firstFraction),
        width: percent(Math.max(commit.lastFraction - commit.firstFraction, 0.004)),
      }}
      title={`commit ${commit.commitIndex}: ${commit.wordCount} word(s) from ${formatMs(commit.firstWordMs!)} ms to ${formatMs(commit.lastWordMs!)} ms`}
      aria-hidden="true"
    />
  );
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
        {timeline.commits.map((commit) => (
          <CommitBoundary key={`boundary-${commit.commitIndex}`} commit={commit} />
        ))}
        {timeline.commits.map((commit) => (
          <CommitSpan key={`commit-${commit.commitIndex}`} commit={commit} />
        ))}
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

      <section className="commits" aria-labelledby="commits-heading">
        <h3 id="commits-heading" className="commits__heading">
          Where the server cut
        </h3>
        <p className="commits__note">
          {timeline.commitStrategy === "manual" ? (
            <>
              Under manual commits the runner requested these cut points at known samples, once the
              speech before them had been streamed in full.
            </>
          ) : (
            <>
              Under VAD the server&rsquo;s voice activity detection chose these cut points, which is
              the variable under test.
            </>
          )}{" "}
          Each band on the track is the silence a commit was cut from, and the record does not say
          where within it the cut fell.
        </p>
        {timeline.commits.length === 0 ? (
          <p className="commits__none">
            This run returned no timestamped commit, so there is no boundary to mark. The words
            below are the whole of what it said.
          </p>
        ) : (
          <table className="commits__table">
            <caption>
              One row per timestamped commit, in the order they arrived. The position of a commit is
              where the server placed its first and last returned words.
            </caption>
            <thead>
              <tr>
                <th scope="col">Commit</th>
                <th scope="col">Words</th>
                <th scope="col">First word (ms)</th>
                <th scope="col">Last word (ms)</th>
                <th scope="col">Cut from</th>
              </tr>
            </thead>
            <tbody>
              {timeline.commits.map((commit) => (
                <tr
                  key={commit.commitIndex}
                  data-testid="commit-row"
                  data-commit-index={String(commit.commitIndex)}
                  data-placed={String(commit.firstWordMs !== null)}
                >
                  <th scope="row">{commit.commitIndex}</th>
                  <td>{commit.wordCount}</td>
                  <td>
                    {commit.firstWordMs === null ? "no word returned" : formatMs(commit.firstWordMs)}
                  </td>
                  <td>
                    {commit.lastWordMs === null ? "no word returned" : formatMs(commit.lastWordMs)}
                  </td>
                  <td>
                    {commit.boundaryFromMs === null || commit.boundaryToMs === null
                      ? "—"
                      : `${formatMs(commit.boundaryFromMs)}–${formatMs(commit.boundaryToMs)} ms`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

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

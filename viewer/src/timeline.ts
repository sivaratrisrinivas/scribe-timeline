/**
 * The timeline: a run record as the geometry a reader can look at.
 *
 * This is the module that turns a record into positions on a track, and it is
 * deliberately pure. Everything the interface draws comes from here, so the
 * arithmetic that could quietly mislead a reader -- samples against milliseconds,
 * a word against the position its clip was inserted at -- happens in one place
 * with tests around it, rather than inside a render function where a mistake would
 * only be visible by eye.
 *
 * Two rules govern the arithmetic.
 *
 * **A returned timestamp is shown where the server put it.** The manifest says
 * where a clip was *inserted*; the transcript says where the server thought the
 * word *was*. Those differ, and the difference is the measurement. The insertion
 * point is therefore carried as its own field, named for what it is, and never
 * substituted for a returned timestamp.
 *
 * **A missing value is missing, not zero.** A marker absent from the transcript
 * raises. Zero is a legitimate-looking timestamp that would be indistinguishable
 * from a real result, which is the one thing a diagnostic about a 100 ms drift
 * cannot afford.
 */

import type { CommitExtent } from "./runIndex.js";
import type { RunRecord, WordTiming } from "./runRecord.js";

/** The only matching rule this viewer implements, and the one the project ran. */
export const IMPLEMENTED_MATCH_RULE = "exact-text-case-and-punctuation-insensitive";

/** Stripped from both ends of a token. Interior characters are left alone, so
 *  `Zorblaxx` still fails to match `Zorblax`. Mirrors
 *  `scribe_timeline.analysis.matching`. */
const EDGE_PUNCTUATION = /^[^\p{L}\p{N}_]+|[^\p{L}\p{N}_]+$/gu;

export class UnimplementedMatchRuleError extends Error {
  public constructor(rule: string) {
    super(
      `this record was matched with ${JSON.stringify(rule)}, which the viewer does not ` +
        `implement; it implements ${JSON.stringify(IMPLEMENTED_MATCH_RULE)}. Applying one ` +
        `rule to a record that asked for another would report a number derived by a rule ` +
        `nobody ran.`,
    );
    this.name = "UnimplementedMatchRuleError";
  }
}

export class MarkerTextAbsentError extends Error {
  public constructor(text: string, returned: readonly string[]) {
    super(
      `marker ${JSON.stringify(text)} is not in the returned transcript; received: ` +
        `${returned.length === 0 ? "(no words returned)" : returned.join(", ")}. ` +
        `This run supports no measurement, so no position is shown for it.`,
    );
    this.name = "MarkerTextAbsentError";
  }
}

/** Normalised exactly as the project's matcher normalises, so both sides of a
 *  comparison agree on which two tokens are the same word. */
export function normaliseToken(text: string): string {
  return text.replace(EDGE_PUNCTUATION, "").toLocaleLowerCase();
}

export interface TimelineWord {
  readonly text: string;
  readonly startMs: number;
  readonly endMs: number;
  /** 0..1 along the track. Clamped to the track, so a word past the end of the
   *  audio is drawn at the edge rather than off it. */
  readonly startFraction: number;
  readonly endFraction: number;
  /** Whether the word falls inside the audio at all. A word that does not is
   *  still listed with its timestamps, so nothing is hidden by clamping. */
  readonly withinAudio: boolean;
  /** Whether this word is the measured marker. */
  readonly isMarker: boolean;
  readonly logprob: number | null;
}

export interface TimelineMarker {
  /** The marker text as the manifest names it: the word that was spoken. */
  readonly text: string;
  /** Where the manifest says the marker's clip was inserted, in milliseconds.
   *  NOT where the word started -- the clip carries leading silence, so this is
   *  not an expected timestamp for anything. */
  readonly insertionPointMs: number;
  readonly insertionPointSample: number;
  /** The returned word the marker matched, at the position the server gave it. */
  readonly returnedWord: TimelineWord;
}

export interface Timeline {
  readonly runId: string;
  readonly conditionId: string;
  readonly commitStrategy: RunRecord["commit_strategy"];
  readonly repeatIndex: number;
  /** Length of the audio this record describes, derived from the manifest. */
  readonly durationMs: number;
  readonly sampleRate: number;
  readonly sampleCount: number;
  readonly words: readonly TimelineWord[];
  readonly markers: readonly TimelineMarker[];
  /** Where each commit this run returned fell, in the order they arrived. */
  readonly commits: readonly TimelineCommit[];
  /** The unit the API returned timestamps in. The viewer converts nothing, so this
   *  is how a reader learns the raw values were seconds. */
  readonly sourceTimestampUnit: string;
  readonly matchRule: string;
  readonly apiKeyPresent: boolean;
}

/** One commit, placed on the audio clock.
 *
 *  Carries the commit's own span *and* the silence it was cut from, because those
 *  are different claims. The span is where the server said the words were. The
 *  silence is where it was free to cut, and the record does not narrow that any
 *  further -- so the boundary is a gap on the track, never a line claiming a
 *  position the evidence does not support.
 *
 *  A commit with no placeable word keeps its place in the sequence and carries no
 *  position. The commit count is cross-checked against the condition in the
 *  comparison beside it, so dropping one would put the two figures out of step.
 */
export interface TimelineCommit {
  readonly commitIndex: number;
  readonly wordCount: number;
  readonly firstWordMs: number | null;
  readonly lastWordMs: number | null;
  /** 0..1 along the track, or null when the commit has no position. */
  readonly firstFraction: number | null;
  readonly lastFraction: number | null;
  /** The silence this commit was cut from: the previous commit's last word to this
   *  one's first. Null for the first commit, which was cut from the start of the
   *  audio, and null wherever a neighbouring commit has no known position. */
  readonly boundaryFromMs: number | null;
  readonly boundaryToMs: number | null;
  readonly boundaryFromFraction: number | null;
  readonly boundaryToFraction: number | null;
}

function msPerSample(sampleRate: number): number {
  return 1000 / sampleRate;
}

function clampFraction(value: number): number {
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

/** The one word in `words` that `text` names, under the recorded match rule.
 *
 *  Returned with its index, because a transcript may name the same word more than
 *  once and the caller needs to tell those occurrences apart.
 */
function locateMarker(
  words: readonly WordTiming[],
  text: string,
): { readonly index: number; readonly word: WordTiming } | null {
  const wanted = normaliseToken(text);
  for (const [index, word] of words.entries()) {
    if (normaliseToken(word.text) === wanted) return { index, word };
  }
  return null;
}

function toTimelineWord(
  word: WordTiming,
  durationMs: number,
  isMarker: boolean,
): TimelineWord {
  // Read from the record's snake_case fields and named `Ms` here, because this
  // model is in milliseconds by construction while the record's field names say
  // only what the API called them.
  const startMs = word.start_ms;
  const endMs = word.end_ms;
  const withinAudio = startMs < durationMs;
  return {
    text: word.text,
    startMs,
    endMs,
    startFraction: clampFraction(startMs / durationMs),
    endFraction: clampFraction(endMs / durationMs),
    withinAudio,
    isMarker,
    logprob: word.logprob,
  };
}

export function buildTimeline(record: RunRecord, commits: readonly CommitExtent[]): Timeline {
  if (record.match_rule !== IMPLEMENTED_MATCH_RULE) {
    throw new UnimplementedMatchRuleError(record.match_rule);
  }

  const msPerSampleIndex = msPerSample(record.manifest.sample_rate);
  const durationMs = record.manifest.sample_count * msPerSampleIndex;

  const markers: TimelineMarker[] = [];
  // Indexes of the specific word instances the markers claim, rather than their
  // normalised text. A marker word appearing twice in one transcript would
  // otherwise be dropped from the track and the table entirely -- a word the server
  // returned, silently not shown, which is the omission this project cannot afford.
  const claimed = new Set<number>();
  for (const [text, sample] of Object.entries(record.manifest.markers)) {
    const matched = locateMarker(record.words, text);
    if (matched === null) {
      throw new MarkerTextAbsentError(text, record.words.map((word) => word.text));
    }
    claimed.add(matched.index);
    markers.push({
      text,
      insertionPointMs: sample * msPerSampleIndex,
      insertionPointSample: sample,
      returnedWord: toTimelineWord(matched.word, durationMs, true),
    });
  }

  // The marker words come first so a marker word is rendered from the entry the
  // marker claims rather than being matched a second time here.
  const words = [
    ...markers.map((marker) => marker.returnedWord),
    ...record.words
      .filter((_, index) => !claimed.has(index))
      .map((word) => toTimelineWord(word, durationMs, false)),
  ];

  return {
    runId: record.run_id,
    conditionId: record.condition_id,
    commitStrategy: record.commit_strategy,
    repeatIndex: record.repeat_index,
    durationMs,
    sampleRate: record.manifest.sample_rate,
    sampleCount: record.manifest.sample_count,
    words,
    markers,
    commits: placeCommits(commits, durationMs),
    sourceTimestampUnit: record.source_timestamp_unit,
    matchRule: record.match_rule,
    apiKeyPresent: record.api_key_present,
  };
}

/** Place each commit on the track, and each boundary in the silence before it.
 *
 *  A missing position stays missing. `0` is a legitimate-looking place on an
 *  eighteen-second track, and a boundary drawn there would read as an observation
 *  the server never made.
 */
function placeCommits(
  commits: readonly CommitExtent[],
  durationMs: number,
): TimelineCommit[] {
  const fraction = (ms: number): number => clampFraction(ms / durationMs);
  return commits.map((commit, index) => {
    const previous = index === 0 ? null : (commits[index - 1] ?? null);
    // A boundary needs both ends. Either neighbouring commit sitting at an unknown
    // position leaves the gap unstated, rather than guessing which side it fell on.
    const boundaryFromMs = previous?.lastWordMs ?? null;
    const boundaryToMs = commit.firstWordMs;
    const hasBoundary = boundaryFromMs !== null && boundaryToMs !== null;
    return {
      commitIndex: commit.commitIndex,
      wordCount: commit.wordCount,
      firstWordMs: commit.firstWordMs,
      lastWordMs: commit.lastWordMs,
      firstFraction: commit.firstWordMs === null ? null : fraction(commit.firstWordMs),
      lastFraction: commit.lastWordMs === null ? null : fraction(commit.lastWordMs),
      boundaryFromMs: hasBoundary ? boundaryFromMs : null,
      boundaryToMs: hasBoundary ? boundaryToMs : null,
      boundaryFromFraction: hasBoundary ? fraction(boundaryFromMs) : null,
      boundaryToFraction: hasBoundary ? fraction(boundaryToMs) : null,
    };
  });
}

/** The word sounding at `timeMs`, or null in the silence between words.
 *
 *  Half-open on purpose: a word's start is inside it and its end is not, so the
 *  silence between two words belongs to neither. That silence is where a VAD
 *  commit boundary falls, so attributing it to a word would misdescribe where the
 *  server cut.
 */
export function activeWordAt(
  words: readonly TimelineWord[],
  timeMs: number,
): TimelineWord | null {
  for (const word of words) {
    if (timeMs >= word.startMs && timeMs < word.endMs) return word;
  }
  return null;
}

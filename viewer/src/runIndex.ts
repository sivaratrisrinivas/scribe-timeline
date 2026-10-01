/**
 * The bundle index: which runs a reader can pick, and how each one plays.
 *
 * `make viewer-export` writes `runs.json` alongside the run records. It is the one
 * document that describes the whole bundle, so it is where the per-run facts a
 * reader chooses by live: which condition a run belongs to, which strategy
 * triggered its commits, which repeat it is, whether its audio could be rebuilt,
 * and where its commits fell on the audio clock.
 *
 * Two of those are *derived* rather than recorded, and both are derived here in
 * Python rather than here in TypeScript:
 *
 * - **`has_audio`** -- a run whose audio cannot be rebuilt is listed anyway, so a
 *   reader can still read what it says. Omitting it would make a bundle look
 *   smaller than the evidence it holds.
 * - **`commits`** -- where each commit landed, in milliseconds. The run record
 *   holds the raw events, and those carry *seconds* in a field named `start`. A
 *   viewer reading `0.22` as a millisecond would scale its whole track by a
 *   thousand and still look like a plausible timeline, so the conversion happens
 *   once, on export.
 *
 * The index is parsed as strictly as the record. A run listed without its strategy
 * would be indistinguishable from the manual control, which is the one thing the
 * experiment's conclusion rests on.
 */

import {
  nullableNumber,
  requireArray,
  requireBoolean,
  requireExactKeys,
  requireField,
  requireNonNegativeInteger,
  requireNumber,
  requireObject,
  requireString,
} from "./json.js";
import type { CommitStrategy } from "./runRecord.js";

export type { CommitStrategy };

export class InvalidRunIndexError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "InvalidRunIndexError";
  }
}

function fail(message: string): never {
  throw new InvalidRunIndexError(message);
}

function exactFields(value: unknown, expected: readonly string[], path: string): Record<string, unknown> {
  const object = requireObject(value, path);
  try {
    requireExactKeys(object, expected, path);
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error));
  }
  return object;
}

/** One commit's span of audio, in milliseconds, as the export derived it.
 *
 *  `firstWordMs` and `lastWordMs` are `null` for a commit the server returned
 *  without any word this project could place. That is a different statement from a
 *  commit at zero, and the distinction is carried all the way to the page.
 */
export interface CommitExtent {
  readonly commitIndex: number;
  readonly wordCount: number;
  readonly firstWordMs: number | null;
  readonly lastWordMs: number | null;
}

export interface RunIndexEntry {
  readonly runId: string;
  readonly conditionId: string;
  readonly commitStrategy: CommitStrategy;
  readonly repeatIndex: number;
  readonly audioPath: string;
  readonly hasAudio: boolean;
  readonly commits: readonly CommitExtent[];
}

export interface RunIndex {
  /** What the bundle is, in the exporter's own words. Shown to the reader. */
  readonly note: string;
  readonly runs: readonly RunIndexEntry[];
}

const ENTRY_FIELDS = [
  "run_id",
  "condition_id",
  "commit_strategy",
  "repeat_index",
  "audio_path",
  "has_audio",
  "commits",
] as const;

const COMMIT_FIELDS = ["commit_index", "word_count", "first_word_ms", "last_word_ms"] as const;

export function parseCommitExtents(value: unknown, path = "commits"): CommitExtent[] {
  return requireArray(value, path).map((entry, index) => {
    const entryPath = `${path}[${index}]`;
    const raw = exactFields(entry, COMMIT_FIELDS, entryPath);
    return {
      commitIndex: requireNonNegativeInteger(raw["commit_index"], `${entryPath}.commit_index`),
      wordCount: requireNonNegativeInteger(raw["word_count"], `${entryPath}.word_count`),
      firstWordMs: nullableNumber(
        requireField(raw, "first_word_ms", entryPath),
        `${entryPath}.first_word_ms`,
      ),
      lastWordMs: nullableNumber(
        requireField(raw, "last_word_ms", entryPath),
        `${entryPath}.last_word_ms`,
      ),
    };
  });
}

function parseEntry(value: unknown, path: string): RunIndexEntry {
  const raw = exactFields(value, ENTRY_FIELDS, path);
  const commitStrategy = requireString(raw["commit_strategy"], `${path}.commit_strategy`);
  if (commitStrategy !== "vad" && commitStrategy !== "manual") {
    fail(
      `${path}.commit_strategy must be "vad" or "manual", got ${JSON.stringify(commitStrategy)}. ` +
        `A run of unknown strategy cannot be told apart from the manual control, which is the ` +
        `one thing this experiment's conclusion rests on.`,
    );
  }
  return {
    runId: requireString(raw["run_id"], `${path}.run_id`),
    conditionId: requireString(raw["condition_id"], `${path}.condition_id`),
    commitStrategy,
    repeatIndex: requireNonNegativeInteger(raw["repeat_index"], `${path}.repeat_index`),
    audioPath: requireString(raw["audio_path"], `${path}.audio_path`),
    hasAudio: requireBoolean(raw["has_audio"], `${path}.has_audio`),
    commits: parseCommitExtents(raw["commits"], `${path}.commits`),
  };
}

export function parseRunIndex(input: unknown): RunIndex {
  const root = exactFields(input, ["note", "runs"], "runs.json");
  const runs = requireArray(root["runs"], "runs.json.runs").map((entry, index) =>
    parseEntry(entry, `runs.json.runs[${index}]`),
  );

  const seen = new Set<string>();
  for (const run of runs) {
    if (seen.has(run.runId)) {
      // Two entries with one id would make `?run=` ambiguous, and picking either
      // would show a reader a different run than the link they followed.
      fail(`runs.json lists ${JSON.stringify(run.runId)} more than once`);
    }
    seen.add(run.runId);
  }

  if (runs.length === 0) {
    // A viewer listing nothing looks like a broken page rather than an empty
    // evidence set, so this is refused where it can still be named.
    fail("runs.json lists no runs, so there is no evidence to show");
  }

  return { note: requireString(root["note"], "runs.json.note"), runs };
}

/** The entries grouped by condition, each group's repeats in order.
 *
 *  Built here rather than in a component because it is the one place the
 *  relationship between a condition and its repeats is established, and two
 *  components deriving it separately could order them differently.
 */
export function groupByCondition(
  runs: readonly RunIndexEntry[],
): ReadonlyMap<string, readonly RunIndexEntry[]> {
  const grouped = new Map<string, RunIndexEntry[]>();
  for (const run of runs) {
    const existing = grouped.get(run.conditionId);
    if (existing === undefined) grouped.set(run.conditionId, [run]);
    else existing.push(run);
  }
  for (const group of grouped.values()) {
    group.sort((a, b) => a.repeatIndex - b.repeatIndex);
  }
  // Ordered by strategy then by run id, so the VAD family and its manual control
  // appear in a stable order that does not depend on which entry was read first.
  return new Map(
    [...grouped.entries()].sort(([aId, a], [bId, b]) =>
      a[0]!.commitStrategy !== b[0]!.commitStrategy
        ? a[0]!.commitStrategy === "vad"
          ? -1
          : 1
        : aId.localeCompare(bId),
    ),
  );
}

/**
 * The bundle index: what a reader can pick, and how it plays.
 *
 * The index is the one document that describes the whole bundle, so it carries the
 * two figures the single-run viewer could not: where each run's commits fell, and
 * whether its audio could be rebuilt. Both are derived in Python on export, and the
 * reason is not tidiness -- the raw events hold *seconds* in a field named `start`,
 * so a viewer that read `0.22` as a millisecond would scale its whole track by a
 * thousand and still look like a plausible timeline.
 *
 * So the tests here are mostly about refusing. A `commits` list that defaults to
 * empty when the field is missing would draw a run with no boundaries, which reads
 * as "this run had no commits" rather than "the index is corrupt".
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  groupByCondition,
  InvalidRunIndexError,
  parseCommitExtents,
  parseRunIndex,
} from "./runIndex.js";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..");
const bundleDir = join(repoRoot, "viewer", "public");

/** The index the project actually ships, parsed. */
function shippedIndex() {
  return parseRunIndex(JSON.parse(readFileSync(join(bundleDir, "runs.json"), "utf8")));
}

function entry(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: "2026-10-02T00-00-00Z__vad_2__rep0",
    condition_id: "vad_2",
    commit_strategy: "vad",
    repeat_index: 0,
    audio_path: "audio/2026-10-02T00-00-00Z__vad_2__rep0.wav",
    has_audio: true,
    commits: [
      { commit_index: 1, word_count: 1, first_word_ms: 220, last_word_ms: 640 },
    ],
    ...overrides,
  };
}

describe("the shipped index parses", () => {
  it("lists every published run with its condition and strategy", () => {
    // The whole point of the index: a VAD run and its manual control are told
    // apart before a reader has looked at anything else.
    const index = shippedIndex();

    expect(index.runs).toHaveLength(
      readdirSync(join(repoRoot, "evidence", "runs")).filter((n) => n.endsWith(".json")).length,
    );
    expect(new Set(index.runs.map((run) => run.conditionId))).toEqual(
      new Set(["vad_0", "vad_1", "vad_2", "manual_2"]),
    );
    expect(index.runs.filter((run) => run.commitStrategy === "manual")).toHaveLength(3);
  });

  it("states in the exporter's own words what the bundle is", () => {
    // Shown to the reader, so it is a string this project chose rather than one the
    // viewer invented.
    expect(shippedIndex().note).toMatch(/no API call/i);
  });

  it("carries every run's commits in milliseconds", () => {
    const vad2 = shippedIndex().runs.find((run) => run.conditionId === "vad_2")!;

    expect(vad2.commits).toHaveLength(3);
    expect(vad2.commits[2]!.firstWordMs).toBe(12_380);
  });

  it("carries no commits for the anchor, which returned exactly one", () => {
    // A run with nothing preceding the marker gets one commit. The boundary count
    // on the track is what shows that, and it is different from zero commits.
    const anchor = shippedIndex().runs.find((run) => run.conditionId === "vad_0")!;

    expect(anchor.commits).toHaveLength(1);
  });
});

describe("grouping runs by condition", () => {
  it("puts a condition's repeats together, in order", () => {
    const grouped = groupByCondition(shippedIndex().runs);

    expect(grouped.get("vad_1")!.map((run) => run.repeatIndex)).toEqual([0, 1, 2]);
  });

  it("puts the VAD family before the manual control", () => {
    // The control is a check on the family beside it. Listing it first would put
    // the thing being controlled ahead of the thing controlling it.
    expect([...groupByCondition(shippedIndex().runs).keys()]).toEqual([
      "vad_0",
      "vad_1",
      "vad_2",
      "manual_2",
    ]);
  });
});

describe("a missing field is a failure, not a default", () => {
  it("refuses an entry with no commits rather than reporting none", () => {
    // An empty list means "this run returned no timestamped commit". Defaulting to
    // it would draw a run with no boundaries, which is a different statement.
    const { commits: _omitted, ...withoutCommits } = entry();

    expect(() => parseRunIndex({ note: "x", runs: [withoutCommits] })).toThrow(/commits/);
  });

  it("refuses a commit with no position rather than placing it at zero", () => {
    // Zero is a legitimate-looking place on an eighteen-second track.
    const commit = { commit_index: 1, word_count: 0, last_word_ms: null };

    expect(() => parseCommitExtents([commit])).toThrow(/first_word_ms/);
  });

  it("refuses a run whose strategy it cannot place", () => {
    // A run of unknown strategy is indistinguishable from the manual control, which
    // is the one comparison the whole conclusion rests on.
    expect(() =>
      parseRunIndex({ note: "x", runs: [entry({ commit_strategy: "automatic" })] }),
    ).toThrow(/commit_strategy/);
  });

  it("refuses an index naming no runs", () => {
    // A viewer listing nothing looks like a broken page rather than an empty
    // evidence set.
    expect(() => parseRunIndex({ note: "x", runs: [] })).toThrow(/lists no runs/i);
  });

  it("refuses two entries claiming one run id", () => {
    // `?run=` would be ambiguous, and picking either would show a reader a
    // different run than the link they followed.
    expect(() =>
      parseRunIndex({ note: "x", runs: [entry(), entry({ condition_id: "vad_1" })] }),
    ).toThrow(/more than once/);
  });

  it("refuses a commit count that is not a count", () => {
    expect(() =>
      parseCommitExtents([{ commit_index: 1, word_count: 1.5, first_word_ms: null, last_word_ms: null }]),
    ).toThrow(/word_count/);
  });

  it("refuses a field this viewer does not know, rather than ignoring it", () => {
    // The export is written beside this parser. A field added there and not here
    // would be a figure the report carries and the page silently drops.
    expect(() => parseRunIndex({ note: "x", runs: [entry({ measured_offset_ms: 180 })] })).toThrow(
      InvalidRunIndexError,
    );
  });

  it("names the run that was wrong", () => {
    const broken = [entry(), { ...entry({ run_id: "run-b" }), commit_strategy: "nope" }];

    expect(() => parseRunIndex({ note: "x", runs: broken })).toThrow(/runs\[1\]/);
  });
});

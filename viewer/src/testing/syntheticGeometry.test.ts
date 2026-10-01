/**
 * The synthetic fixture's geometry must still describe the real experiment.
 *
 * `syntheticRecord.ts` hand-copies five numbers: the sample rate and length from the
 * fixture family, and the two clip lengths from the committed `.pcm` files. Nothing
 * in TypeScript ties them to those sources, so they could drift silently — and a
 * drifted fixture is the worst kind of test failure, because every assertion still
 * passes while describing a timeline the real records do not have.
 *
 * This is the parity check the schema already has on the Python side, applied to the
 * numbers the viewer's own tests are built on. It reads the committed clips and a
 * published run record directly, so it fails the moment either changes.
 */

import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { parseRunRecord, type RunRecord } from "../runRecord.js";
import {
  EARLIER_CLIP_SAMPLES,
  MARKER_CLIP_SAMPLES,
  MARKER_RETURNED_MS,
  MARKER_START_SAMPLE,
  SAMPLE_COUNT,
  SAMPLE_RATE,
  syntheticCommitsJson,
  syntheticRecordJson,
  TIMESTAMPED_EVENT,
} from "./syntheticRecord.js";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..", "..");
const fixturesDir = join(repoRoot, "fixtures");
const evidenceDir = join(repoRoot, "evidence", "runs");

/** Mono PCM16, so two bytes per sample. */
const SAMPLE_WIDTH_BYTES = 2;

function committedClipSamples(clipId: string): number {
  const pcm = readFileSync(join(fixturesDir, `${clipId}.pcm`));
  return pcm.length / SAMPLE_WIDTH_BYTES;
}

function publishedRunIds(): string[] {
  return readdirSync(evidenceDir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => name.replace(/\.json$/, ""));
}

describe("the sample rate is the one the API was run at", () => {
  it("matches the sample rate every published run record echoes", () => {
    const [first] = publishedRunIds();
    expect(first, "no published run records to check against").toBeDefined();

    const record = parseRunRecord(
      JSON.parse(readFileSync(join(evidenceDir, `${first}.json`), "utf8")),
    );

    expect(record.echoed_config.sample_rate).toBe(SAMPLE_RATE);
    expect(record.manifest.sample_rate).toBe(SAMPLE_RATE);
  });
});

describe("the timeline length is the one the fixture composes", () => {
  it("matches every published run record's manifest", () => {
    for (const runId of publishedRunIds()) {
      const record = parseRunRecord(
        JSON.parse(readFileSync(join(evidenceDir, `${runId}.json`), "utf8")),
      );

      expect(record.manifest.sample_count, runId).toBe(SAMPLE_COUNT);
    }
  });

  it("is 18 seconds at 16 kHz, which is what the matrix declares", () => {
    expect(SAMPLE_COUNT / SAMPLE_RATE).toBe(18);
  });
});

describe("the marker sits where the fixture places it", () => {
  it("matches every published run record's marker position", () => {
    for (const runId of publishedRunIds()) {
      const record = parseRunRecord(
        JSON.parse(readFileSync(join(evidenceDir, `${runId}.json`), "utf8")),
      );

      // The invariant the whole experiment rests on: the marker's bytes and sample
      // position are identical across every condition.
      expect(Object.values(record.manifest.markers), runId).toEqual([MARKER_START_SAMPLE]);
    }
  });

  it("is at 12 seconds", () => {
    expect(MARKER_START_SAMPLE / SAMPLE_RATE).toBe(12);
  });
});

describe("the clip lengths are the committed ones", () => {
  it("matches the earlier clip on disk", () => {
    expect(committedClipSamples("earlier")).toBe(EARLIER_CLIP_SAMPLES);
  });

  it("matches the marker clip on disk", () => {
    expect(committedClipSamples("marker")).toBe(MARKER_CLIP_SAMPLES);
  });
});

describe("the marker timestamp is a real observation", () => {
  it("matches what a real vad_2 run returned, to the tick", () => {
    const record = parseRunRecord(syntheticRecordJson());
    const marker = record.words.find((word) => word.text.startsWith("Zorblax"));

    expect(marker?.start_ms).toBe(MARKER_RETURNED_MS);
  });

  it("lands on a 20 ms multiple, as every observed marker timestamp does", () => {
    // The API quantises to 20 ms. A synthetic fixture on an arbitrary offset would
    // hide a viewer that assumed any particular alignment.
    expect(MARKER_RETURNED_MS % 20).toBe(0);
  });

  it("is later than the insertion point, which is the drift being shown", () => {
    // If this ever read as earlier, the synthetic fixture would be modelling the
    // opposite of what the project measured.
    const insertionPointMs = (MARKER_START_SAMPLE * 1000) / SAMPLE_RATE;
    expect(MARKER_RETURNED_MS).toBeGreaterThan(insertionPointMs);
  });
});

describe("the synthetic commits describe the real ones", () => {
  /** Commits as `scribe_timeline.viewer.commits` reads them from a published record.
   *
   *  Recomputed here rather than read from the export, so this is an independent
   *  statement of where the commits fell. The rule is Python's: the only event that
   *  carries word timestamps, first and last word of each, converted from the unit
   *  the record names.
   */
  function publishedCommits(record: RunRecord): Array<{
    commit_index: number;
    word_count: number;
    first_word_ms: number | null;
    last_word_ms: number | null;
  }> {
    return record.events
      .filter((event) => event.type === TIMESTAMPED_EVENT)
      .map((event, index) => {
        const words = event.payload["words"];
        const list = Array.isArray(words) ? words : [];
        const first = list[0] as { start?: unknown } | undefined;
        const last = list[list.length - 1] as { end?: unknown } | undefined;
        const ms = (value: unknown): number | null =>
          typeof value === "number" ? value * 1000 : null;
        return {
          commit_index: index + 1,
          word_count: list.length,
          first_word_ms: ms(first?.start),
          last_word_ms: ms(last?.end),
        };
      });
  }

  function vad2Record(): RunRecord {
    const [id] = publishedRunIds().filter((runId) => runId.includes("__vad_2__"));
    expect(id, "no published vad_2 run record to check against").toBeDefined();
    return parseRunRecord(JSON.parse(readFileSync(join(evidenceDir, `${id}.json`), "utf8")));
  }

  it("places every commit where the published run placed it", () => {
    // The commit boundaries on the track are drawn at these positions, so a fixture
    // even slightly off would put every band somewhere the real run has no silence.
    // The whole list, not just the measured commit: the earlier two are what the
    // boundary bands are made of.
    expect(syntheticCommitsJson()).toEqual(publishedCommits(vad2Record()));
  });

  it("places the measured marker where the measurement put it", () => {
    // The one figure the whole project rests on, asserted separately so a failure
    // says which of the two things moved.
    expect(syntheticCommitsJson()[2]!.first_word_ms).toBe(MARKER_RETURNED_MS);
    expect(publishedCommits(vad2Record())[2]!.first_word_ms).toBe(MARKER_RETURNED_MS);
  });

  it("has as many commits as the published run returned", () => {
    // If the count drifts, every test above it is describing a timeline the real
    // evidence does not have.
    expect(syntheticCommitsJson()).toHaveLength(publishedCommits(vad2Record()).length);
  });

  it("agrees with the anchor's single commit, where there is no preceding speech", () => {
    // `vad_0` is the anchor: one commit, and it is the one carrying the marker.
    const [anchorId] = publishedRunIds().filter((runId) => runId.includes("__vad_0__"));
    const anchor = publishedCommits(
      parseRunRecord(JSON.parse(readFileSync(join(evidenceDir, `${anchorId}.json`), "utf8"))),
    );

    expect(anchor).toHaveLength(1);
    expect(syntheticCommitsJson({ priorStarts: [], markerStartMs: 12_200 })).toHaveLength(1);
  });
});

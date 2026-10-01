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

import { parseRunRecord } from "../runRecord.js";
import {
  EARLIER_CLIP_SAMPLES,
  MARKER_CLIP_SAMPLES,
  MARKER_RETURNED_MS,
  MARKER_START_SAMPLE,
  SAMPLE_COUNT,
  SAMPLE_RATE,
  syntheticRecordJson,
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

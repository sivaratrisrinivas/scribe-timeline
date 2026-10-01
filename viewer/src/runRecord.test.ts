import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { InvalidRunRecordError, parseRunRecord } from "./runRecord.js";

const here = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(
  readFileSync(join(here, "..", "..", "schema", "run-record.schema.json"), "utf8"),
);

/**
 * A minimal record satisfying the schema's `required` list.
 *
 * Written as a literal rather than generated from the schema, so the parser is
 * exercised against input shaped the way a real runner writes it. The schema is
 * read separately in the parity block below.
 */
function validRecord(): unknown {
  return {
    schema_version: 1,
    run_id: "2026-10-02T00-00-00Z__vad_1__rep0",
    condition_id: "vad_1",
    commit_strategy: "vad",
    repeat_index: 0,
    manifest: {
      condition_id: "vad_1",
      sample_rate: 16_000,
      sample_count: 224_000,
      markers: { Zorblax: 192_000 },
      segments: [
        {
          clip_id: "marker",
          start_sample: 192_000,
          sample_count: 4_000,
          marker_text: "Zorblax",
        },
      ],
    },
    events: [
      {
        type: "committed_transcript",
        received_at_ms: 12.4,
        clock_origin: "monotonic_since_connect",
        payload: { text: "Kalvik Zorblax" },
      },
    ],
    echoed_config: {
      model_id: "scribe_v2_realtime",
      language_code: "en",
      sample_rate: 16_000,
      include_timestamps: true,
      commit_strategy: "vad",
      vad_silence_threshold_secs: 1.5,
      vad_threshold: 0.4,
      min_speech_duration_ms: 100,
      min_silence_duration_ms: 100,
    },
    words: [{ text: "Zorblax", start_ms: 11_984, end_ms: 12_093, confidence: 0.97 }],
    api_key_present: false,
  };
}

describe("parseRunRecord", () => {
  it("accepts a record satisfying the schema", () => {
    expect(() => parseRunRecord(validRecord())).not.toThrow();
  });

  it("exposes marker positions in samples", () => {
    const record = parseRunRecord(validRecord());

    expect(record.manifest.markers.Zorblax).toBe(192_000);
  });

  it("keeps the echoed config distinct from anything sent", () => {
    const record = parseRunRecord(validRecord());

    expect(record.echoed_config.vad_silence_threshold_secs).toBe(1.5);
    expect(record.echoed_config.min_speech_duration_ms).toBe(100);
  });
});

describe("malformed records fail loudly rather than defaulting to zero", () => {
  const cases: Array<[string, (r: Record<string, unknown>) => void]> = [
    [
      "a missing marker position",
      (r) => {
        (r["manifest"] as Record<string, unknown>)["markers"] = { Zorblax: null };
      },
    ],
    [
      "a marker position of the wrong type",
      (r) => {
        (r["manifest"] as Record<string, unknown>)["markers"] = { Zorblax: "192000" };
      },
    ],
    [
      "a missing sample rate",
      (r) => {
        delete (r["manifest"] as Record<string, unknown>)["sample_rate"];
      },
    ],
    [
      "an unknown commit strategy",
      (r) => {
        r["commit_strategy"] = "automatic";
      },
    ],
    [
      "an echoed config whose strategy disagrees with the run",
      (r) => {
        (r["echoed_config"] as Record<string, unknown>)["commit_strategy"] = "nonsense";
      },
    ],
    [
      "a word that ends before it starts",
      (r) => {
        r["words"] = [{ text: "Zorblax", start_ms: 12_093, end_ms: 11_984, confidence: 0.97 }];
      },
    ],
    [
      "a non-numeric timestamp",
      (r) => {
        r["words"] = [{ text: "Zorblax", start_ms: "11984", end_ms: 12_093, confidence: 0.97 }];
      },
    ],
    [
      "an unknown clock origin",
      (r) => {
        (r["events"] as Array<Record<string, unknown>>)[0]!["clock_origin"] = "wall_clock";
      },
    ],
    [
      "a marker at the very end of the audio",
      (r) => {
        (r["manifest"] as Record<string, unknown>)["markers"] = { Zorblax: 224_000 };
        (r["manifest"] as Record<string, unknown>)["sample_count"] = 224_000;
      },
    ],
    ["a null manifest", (r) => void (r["manifest"] = null)],
    ["an array instead of a record", (r) => void (r["manifest"] = [])],
  ];

  it.each(cases)("rejects %s", (_label, mutate) => {
    const record = validRecord() as Record<string, unknown>;
    mutate(record);

    expect(() => parseRunRecord(record)).toThrow(InvalidRunRecordError);
  });

  it("rejects a bare null", () => {
    expect(() => parseRunRecord(null)).toThrow(InvalidRunRecordError);
  });

  it("rejects a record that names no marker at all", () => {
    // A run with no marker position cannot support the measurement, so accepting
    // it would let a viewer render an empty timeline as though it were a result.
    const record = validRecord() as Record<string, unknown>;
    (record["manifest"] as Record<string, unknown>)["markers"] = {};

    expect(() => parseRunRecord(record)).toThrow(InvalidRunRecordError);
  });

  it("rejects a record whose marker position is negative", () => {
    const record = validRecord() as Record<string, unknown>;
    (record["manifest"] as Record<string, unknown>)["markers"] = { Zorblax: -1 };

    expect(() => parseRunRecord(record)).toThrow(InvalidRunRecordError);
  });
});

describe("parity with the committed JSON Schema", () => {
  it("the schema forbids additional properties, as the parser assumes", () => {
    expect(schema.additionalProperties).toBe(false);
  });

  it("the schema has no credential-bearing property beyond a boolean flag", () => {
    const banned = ["key", "token", "secret", "auth", "credential", "password"];
    const visited: string[] = [];

    const walk = (node: Record<string, unknown>, path: string): void => {
      const properties = (node["properties"] ?? {}) as Record<string, unknown>;
      for (const [name, sub] of Object.entries(properties)) {
        if (banned.some((word) => name.toLowerCase().includes(word))) {
          visited.push(`${path}.${name}`);
          expect((sub as Record<string, unknown>)["type"], `${path}.${name}`).toBe("boolean");
        }
        walk(sub as Record<string, unknown>, `${path}.${name}`);
      }
      const defs = (node["$defs"] ?? {}) as Record<string, unknown>;
      for (const [name, sub] of Object.entries(defs)) {
        walk(sub as Record<string, unknown>, `${path}.${name}`);
      }
    };

    walk(schema as unknown as Record<string, unknown>, "$");

    // Asserts the walk was not vacuous: without this an emptied schema would pass.
    expect(visited).toEqual(["$.api_key_present"]);
  });

  it("documents that the free-form payload stays open in the schema", () => {
    // The credential guarantee comes from the Python validator walking the
    // payload, not from the schema. If this ever fails, the validator may be
    // simplifiable.
    const payload = (
      schema["$defs"] as Record<string, Record<string, unknown>>
    )["RawEvent"]!["properties"] as Record<string, Record<string, unknown>>;

    expect(payload["payload"]!["additionalProperties"]).not.toBe(false);
  });
});

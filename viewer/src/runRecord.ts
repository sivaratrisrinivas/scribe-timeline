/**
 * Run-record types for the viewer.
 *
 * These mirror the JSON Schema generated from the Python models in
 * `scribe_timeline.records`. The schema is the contract; this file is the
 * TypeScript projection of it, and a test asserts the two stay in step.
 *
 * A malformed run record must fail loudly. A viewer that renders a missing value
 * as `0` looks exactly like a real result of zero, which is the one thing this
 * whole project exists to avoid.
 */

import {
  InvalidJsonFieldError,
  optionalNumber,
  optionalString,
  requireArray,
  requireBoolean,
  requireNonNegativeInteger,
  requireNonNegativeNumber,
  requireNumber,
  requireObject,
  requirePositiveNumber,
  requireString,
} from "./json.js";

export type CommitStrategy = "vad" | "manual";

export interface Segment {
  readonly clip_id: string;
  readonly start_sample: number;
  readonly sample_count: number;
  readonly marker_text: string | null;
}

export interface Manifest {
  readonly condition_id: string;
  readonly sample_rate: number;
  readonly sample_count: number;
  readonly markers: Readonly<Record<string, number>>;
  readonly segments: readonly Segment[];
}

/** What `received_at_ms` is measured from, so arrival latency is distinguishable
 *  from timestamps on the audio sample clock. */
export type ClockOrigin = "monotonic_since_connect";

export interface RawEvent {
  readonly type: string;
  readonly received_at_ms: number;
  readonly clock_origin: ClockOrigin;
  readonly payload: Readonly<Record<string, unknown>>;
}

export interface EchoedSessionConfig {
  readonly model_id: string;
  readonly language_code: string | null;
  readonly sample_rate: number;
  readonly include_timestamps: boolean;
  /** What the server reported, which may be nothing: `session_started` does not
   *  echo this. The record's own `commit_strategy` is what was requested. */
  readonly commit_strategy: CommitStrategy | null;
  readonly vad_silence_threshold_secs: number | null;
  readonly vad_threshold: number | null;
  readonly min_speech_duration_ms: number | null;
  readonly min_silence_duration_ms: number | null;
}

export interface WordTiming {
  readonly text: string;
  /** Converted to milliseconds on ingest. The API returns seconds; see
   *  `source_timestamp_unit` on the record. */
  readonly start_ms: number;
  readonly end_ms: number;
  /** Per-word recogniser log-probability. Negative and unbounded, e.g. -0.1 for a
   *  confident word and -1.3 for a doubtful one. Not a probability. */
  readonly logprob: number | null;
}

export interface RunRecord {
  readonly schema_version: number;
  readonly run_id: string;
  readonly condition_id: string;
  readonly commit_strategy: CommitStrategy;
  readonly repeat_index: number;
  readonly manifest: Manifest;
  readonly events: readonly RawEvent[];
  readonly echoed_config: EchoedSessionConfig;
  readonly words: readonly WordTiming[];
  /** Whether a credential was in the environment. The credential is never stored. */
  readonly api_key_present: boolean;
  /** How the measured marker was located in the returned transcript. Travels with
   *  the record so a reader knows which word was measured. */
  readonly match_rule: string;
  /** The unit the API returned timestamps in, before conversion to ms. */
  readonly source_timestamp_unit: string;
}

/** Thrown when a record cannot be trusted. Never swallowed into a default. */
export class InvalidRunRecordError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "InvalidRunRecordError";
  }
}

function optionalLogprob(value: unknown, path: string): number | null {
  const parsed = optionalNumber(value, path);
  // log(p) <= 0 for any probability p. A positive value means the field is
  // carrying something other than a log-probability, which is worth failing on
  // rather than displaying as a score.
  if (parsed !== null && parsed > 0) {
    throw new InvalidRunRecordError(
      `${path} is a log-probability and cannot exceed 0, got ${parsed}`,
    );
  }
  return parsed;
}

/**
 * Parse and validate a run record.
 *
 * Throws `InvalidRunRecordError` rather than returning a partial record, so a
 * broken fixture is visible instead of rendering as a plausible zero.
 *
 * The field checks themselves live in `json.ts`, because the comparison parser
 * needs exactly the same ones and two copies of a rule this strict would be two
 * places for it to drift. This function is the only adapter: a bad field in a run
 * record is reported as a run-record error, so the page can say so in the record's
 * own terms.
 */
export function parseRunRecord(input: unknown): RunRecord {
  try {
    return readRunRecord(input);
  } catch (error) {
    if (error instanceof InvalidJsonFieldError) {
      throw new InvalidRunRecordError(error.message);
    }
    throw error;
  }
}

function readRunRecord(input: unknown): RunRecord {
  const root = requireObject(input, "runRecord");

  const strategy = requireString(root["commit_strategy"], "commit_strategy");
  if (strategy !== "vad" && strategy !== "manual") {
    throw new InvalidRunRecordError(
      `commit_strategy must be "vad" or "manual", got ${JSON.stringify(strategy)}`,
    );
  }

  const manifestRaw = requireObject(root["manifest"], "manifest");
  const sampleCount = requireNumber(manifestRaw["sample_count"], "manifest.sample_count");
  const sampleRate = requireNumber(manifestRaw["sample_rate"], "manifest.sample_rate");
  if (sampleRate <= 0) {
    throw new InvalidRunRecordError(`manifest.sample_rate must be positive, got ${sampleRate}`);
  }
  const markersRaw = requireObject(manifestRaw["markers"], "manifest.markers");
  const markers: Record<string, number> = {};
  for (const [text, start] of Object.entries(markersRaw)) {
    const position = requireNumber(start, `manifest.markers[${text}]`);
    // A negative position is a corrupt record, not a marker before the start.
    if (position < 0) {
      throw new InvalidRunRecordError(`manifest.markers[${text}] is negative (${position})`);
    }
    // A marker at or past the end claims a word where no audio exists. The
    // Python model enforces this too; the viewer must not be the lax side.
    if (position >= sampleCount) {
      throw new InvalidRunRecordError(
        `manifest.markers[${text}] at sample ${position} is outside audio of ${sampleCount} samples`,
      );
    }
    markers[text] = position;
  }
  // A record naming no marker cannot support a measurement. Accepting it would
  // let a timeline render empty and read as a result rather than a failure.
  if (Object.keys(markers).length === 0) {
    throw new InvalidRunRecordError("manifest.markers names no marker position");
  }

  const segments = requireArray(manifestRaw["segments"], "manifest.segments").map(
    (raw, index): Segment => {
      const segment = requireObject(raw, `manifest.segments[${index}]`);
      return {
        clip_id: requireString(segment["clip_id"], `manifest.segments[${index}].clip_id`),
        start_sample: requireNumber(
          segment["start_sample"],
          `manifest.segments[${index}].start_sample`,
        ),
        sample_count: requireNumber(
          segment["sample_count"],
          `manifest.segments[${index}].sample_count`,
        ),
        marker_text: optionalString(
          segment["marker_text"],
          `manifest.segments[${index}].marker_text`,
        ),
      };
    },
  );

  const echoedRaw = requireObject(root["echoed_config"], "echoed_config");
  // `session_started` does not echo this, so absence is legitimate and must not be
  // filled in with a default: inventing "manual" would misreport a VAD run's
  // control. The record's own `commit_strategy` carries what was requested.
  const rawStrategy = echoedRaw["commit_strategy"] ?? null;
  if (rawStrategy !== null && rawStrategy !== "vad" && rawStrategy !== "manual") {
    throw new InvalidRunRecordError(
      `echoed_config.commit_strategy must be "vad", "manual", or absent, ` +
        `got ${JSON.stringify(rawStrategy)}`,
    );
  }
  const echoedStrategy = rawStrategy as CommitStrategy | null;

  return {
    schema_version: requireNonNegativeInteger(root["schema_version"], "schema_version"),
    run_id: requireString(root["run_id"], "run_id"),
    condition_id: requireString(root["condition_id"], "condition_id"),
    commit_strategy: strategy,
    repeat_index: requireNonNegativeInteger(root["repeat_index"], "repeat_index"),
    manifest: {
      condition_id: requireString(manifestRaw["condition_id"], "manifest.condition_id"),
      sample_rate: sampleRate,
      sample_count: sampleCount,
      markers,
      segments,
    },
    events: requireArray(root["events"] ?? [], "events").map((raw, index): RawEvent => {
      const event = requireObject(raw, `events[${index}]`);
      const origin = requireString(event["clock_origin"], `events[${index}].clock_origin`);
      if (origin !== "monotonic_since_connect") {
        throw new InvalidRunRecordError(
          `events[${index}].clock_origin must be "monotonic_since_connect", ` +
            `got ${JSON.stringify(origin)}; an unknown origin makes received_at_ms ambiguous`,
        );
      }
      return {
        type: requireString(event["type"], `events[${index}].type`),
        received_at_ms: requireNonNegativeNumber(
          event["received_at_ms"],
          `events[${index}].received_at_ms`,
        ),
        clock_origin: origin,
        payload: requireObject(event["payload"] ?? {}, `events[${index}].payload`),
      };
    }),
    echoed_config: {
      model_id: requireString(echoedRaw["model_id"], "echoed_config.model_id"),
      language_code: optionalString(
        echoedRaw["language_code"],
        "echoed_config.language_code",
      ),
      sample_rate: requirePositiveNumber(
        echoedRaw["sample_rate"],
        "echoed_config.sample_rate",
      ),
      include_timestamps: requireBoolean(
        echoedRaw["include_timestamps"],
        "echoed_config.include_timestamps",
      ),
      commit_strategy: echoedStrategy,
      vad_silence_threshold_secs: optionalNumber(
        echoedRaw["vad_silence_threshold_secs"],
        "echoed_config.vad_silence_threshold_secs",
      ),
      vad_threshold: optionalNumber(echoedRaw["vad_threshold"], "echoed_config.vad_threshold"),
      min_speech_duration_ms: optionalNumber(
        echoedRaw["min_speech_duration_ms"],
        "echoed_config.min_speech_duration_ms",
      ),
      min_silence_duration_ms: optionalNumber(
        echoedRaw["min_silence_duration_ms"],
        "echoed_config.min_silence_duration_ms",
      ),
    },
    words: requireArray(root["words"] ?? [], "words").map((raw, index): WordTiming => {
      const word = requireObject(raw, `words[${index}]`);
      const start = requireNumber(word["start_ms"], `words[${index}].start_ms`);
      const end = requireNumber(word["end_ms"], `words[${index}].end_ms`);
      if (end < start) {
        throw new InvalidRunRecordError(
          `words[${index}] ends (${end}ms) before it starts (${start}ms)`,
        );
      }
      return {
        text: requireString(word["text"], `words[${index}].text`),
        start_ms: start,
        end_ms: end,
        logprob: optionalLogprob(word["logprob"], `words[${index}].logprob`),
      };
    }),
    api_key_present: requireBoolean(root["api_key_present"] ?? false, "api_key_present"),
    match_rule: requireString(
      root["match_rule"] ?? "exact-text-case-and-punctuation-insensitive",
      "match_rule",
    ),
    source_timestamp_unit: requireString(
      root["source_timestamp_unit"] ?? "seconds",
      "source_timestamp_unit",
    ),
  };
}

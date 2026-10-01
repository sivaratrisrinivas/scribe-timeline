/**
 * Synthetic run records for developing and testing the viewer.
 *
 * Shaped from a real captured record (`vad_2`, repeat 2) so the parser and the
 * timeline are exercised against the field set a runner actually writes, rather
 * than a hand-written minimum. Deliberately free of network and API key, which is
 * what lets the viewer be built and tested before any live data is needed.
 *
 * The geometry below is copied from the fixture family and the committed `.pcm`
 * clips. `syntheticGeometry.test.ts` asserts it still matches both, because a
 * hand-copied constant that drifts is the one thing that would let the viewer's
 * tests pass against geometry the real records do not have.
 *
 * Not shipped in the bundle. Only tests import it, because a viewer showing invented
 * data would be worse than one showing nothing.
 */

/** Sample geometry of the fixture family: 16 kHz, 18 s, marker clip at 12 s. */
export const SAMPLE_RATE = 16_000;
export const SAMPLE_COUNT = 288_000;
export const MARKER_START_SAMPLE = 192_000;
export const EARLIER_CLIP_SAMPLES = 16_472;
export const MARKER_CLIP_SAMPLES = 18_701;

export const MARKER_TEXT = "Zorblax";

/** The returned marker timestamp under VAD with two preceding commits. */
export const MARKER_RETURNED_MS = 12_380;

export interface SyntheticOptions {
  /** Sample positions of the unmarked speech segments placed before the marker. */
  readonly priorStarts?: readonly number[];
  /** The marker's returned start, in ms. `null` models a transcript with no marker. */
  readonly markerStartMs?: number | null;
  readonly conditionId?: string;
  readonly commitStrategy?: "vad" | "manual";
  readonly repeatIndex?: number;
  readonly matchRule?: string;
  readonly sampleRate?: number;
}

export function syntheticRecordJson(options: SyntheticOptions = {}): unknown {
  const {
    priorStarts = [0, 96_000],
    markerStartMs = MARKER_RETURNED_MS,
    conditionId = "vad_2",
    commitStrategy = "vad",
    repeatIndex = 2,
    matchRule = "exact-text-case-and-punctuation-insensitive",
    sampleRate = SAMPLE_RATE,
  } = options;

  const words = priorStarts.map((startSample, index) => {
    // The segment's sample position, in milliseconds. Written the long way on
    // purpose: this fixture exists to catch a sample/millisecond mix-up, so the
    // conversion should be legible rather than folded into a divisor.
    const startMs = (startSample * 1000) / sampleRate;
    return {
      // The server's rendering of the fixture's "Kalvik". Nothing measures it.
      text: "Kolvig.",
      start_ms: startMs + 220,
      end_ms: startMs + 640,
      logprob: -0.8 - index * 0.01,
    };
  });
  if (markerStartMs !== null) {
    words.push({
      text: "Zorblax.",
      start_ms: markerStartMs,
      end_ms: markerStartMs + 600,
      logprob: -0.85,
    });
  }

  return {
    schema_version: 1,
    run_id: `2026-10-01T21-12-51Z__${conditionId}__rep${repeatIndex}`,
    condition_id: conditionId,
    commit_strategy: commitStrategy,
    repeat_index: repeatIndex,
    manifest: {
      condition_id: conditionId,
      sample_rate: sampleRate,
      sample_count: SAMPLE_COUNT,
      markers: { [MARKER_TEXT]: MARKER_START_SAMPLE },
      segments: [
        ...priorStarts.map((startSample) => ({
          clip_id: "earlier",
          start_sample: startSample,
          sample_count: EARLIER_CLIP_SAMPLES,
          marker_text: null,
        })),
        {
          clip_id: "marker",
          start_sample: MARKER_START_SAMPLE,
          sample_count: MARKER_CLIP_SAMPLES,
          marker_text: MARKER_TEXT,
        },
      ],
    },
    events: [
      {
        type: "session_started",
        received_at_ms: 0.72,
        clock_origin: "monotonic_since_connect",
        payload: { model_id: "scribe_v2_realtime", session_id: "opaque" },
      },
    ],
    echoed_config: {
      model_id: "scribe_v2_realtime",
      language_code: "en",
      sample_rate: sampleRate,
      include_timestamps: true,
      // The server does not echo it. Absence must survive to the UI as absence.
      commit_strategy: null,
      vad_silence_threshold_secs: 1.5,
      vad_threshold: 0.4,
      min_speech_duration_ms: 100,
      min_silence_duration_ms: 100,
    },
    words,
    api_key_present: true,
    match_rule: matchRule,
    source_timestamp_unit: "seconds",
  };
}

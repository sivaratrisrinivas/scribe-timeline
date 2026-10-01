import { describe, expect, it } from "vitest";

import {
  activeWordAt,
  buildTimeline,
  MarkerTextAbsentError,
  UnimplementedMatchRuleError,
} from "./timeline.js";
import { parseRunRecord } from "./runRecord.js";
import {
  MARKER_RETURNED_MS,
  MARKER_TEXT,
  SAMPLE_COUNT,
  SAMPLE_RATE,
  syntheticRecordJson,
} from "./testing/syntheticRecord.js";

function timeline(options: Parameters<typeof syntheticRecordJson>[0] = {}) {
  return buildTimeline(parseRunRecord(syntheticRecordJson(options)));
}

describe("the timeline's own geometry", () => {
  it("is as long as the record says the audio is", () => {
    // 288000 samples at 16 kHz. The model derives this rather than reading it off
    // the audio element, so a truncated file cannot silently shorten the scale the
    // words are drawn against.
    expect(timeline().durationMs).toBe(18_000);
  });

  it("converts the marker's sample position to milliseconds", () => {
    expect(timeline().markers[0]!.insertionPointMs).toBe(12_000);
  });
});

describe("the returned word timestamps are shown as returned", () => {
  it("places the marker word at the timestamp the server sent", () => {
    // 12380 ms, not the 12000 ms insertion point. Snapping the word to where the
    // clip was placed would draw the drift this project exists to show.
    expect(timeline().markers[0]!.returnedWord!.startMs).toBe(MARKER_RETURNED_MS);
  });

  it("keeps the insertion point and the returned word as separate figures", () => {
    const marker = timeline().markers[0]!;

    expect(marker.insertionPointMs).toBe(12_000);
    expect(marker.returnedWord!.startMs).toBe(MARKER_RETURNED_MS);
    // The difference is the whole measurement, and it is arithmetic the viewer
    // must not perform on the reader's behalf.
    expect(marker.insertionPointMs).not.toBe(marker.returnedWord!.startMs);
  });
});

describe("the match rule is the one the project actually ran", () => {
  it("refuses a record whose match rule this viewer does not implement", () => {
    // The record names how the marker was located. Implementing one rule and
    // silently applying it to a record that asked for another would report a
    // number derived by a rule nobody ran.
    expect(() => timeline({ matchRule: "fuzzy-prefix-approximate" })).toThrow(
      UnimplementedMatchRuleError,
    );
  });

  it("matches the marker case- and edge-punctuation-insensitively", () => {
    // The fixture word is `Zorblax`; the server returned `Zorblax.` with a full
    // stop. Under the recorded rule that is the same word.
    const marker = timeline().markers[0]!;

    expect(marker.returnedWord!.text).toBe("Zorblax.");
  });

  it("does not match a different word that merely contains the marker", () => {
    // `Zorblaxx` is a different word. A substring rule would measure the offset
    // against audio the fixture never played.
    const record = parseRunRecord(syntheticRecordJson({ markerStartMs: 12_380 }));
    const words = record.words.map((word) =>
      word.text === "Zorblax." ? { ...word, text: "Zorblaxx" } : word,
    );

    expect(() => buildTimeline({ ...record, words })).toThrow(MarkerTextAbsentError);
  });

  it("keeps a second occurrence of the marker word rather than dropping it", () => {
    // The server can return the same word twice. Dropping every word whose text
    // matches the marker would hide the second one from both the track and the
    // table -- a returned word silently not shown.
    const record = parseRunRecord(syntheticRecordJson());
    const words = [
      ...record.words,
      { text: "Zorblax.", start_ms: 15_000, end_ms: 15_600, logprob: -0.9 },
    ];

    const model = buildTimeline({ ...record, words });

    expect(model.words.filter((word) => word.text === "Zorblax.")).toHaveLength(2);
    // The marker claims the first occurrence; the second is shown as an ordinary word.
    expect(model.markers[0]!.returnedWord.startMs).toBe(MARKER_RETURNED_MS);
    expect(model.words.filter((word) => word.isMarker)).toHaveLength(1);
  });
});

describe("a marker missing from the transcript is absent, never zero", () => {
  it("raises rather than reporting the insertion point as the returned time", () => {
    // A run that lost its marker supports no measurement. Reporting 12000 ms would
    // look exactly like the anchor condition's result.
    expect(() => timeline({ markerStartMs: null })).toThrow(MarkerTextAbsentError);
  });
});

describe("word positions on the track", () => {
  it("are fractions of the audio's length", () => {
    const markerWord = timeline().markers[0]!.returnedWord!;

    expect(markerWord.startFraction).toBeCloseTo(12_380 / 18_000, 10);
  });

  it("flags a word that falls outside the audio instead of clamping it silently", () => {
    // A word past the end of the audio cannot be played, and drawing it at the
    // right-hand edge would place it at a position the record never claimed.
    const record = parseRunRecord(syntheticRecordJson());
    const words = [
      ...record.words,
      { text: "Orbique", start_ms: 19_000, end_ms: 19_400, logprob: -0.3 },
    ];

    const stray = buildTimeline({ ...record, words }).words.find((w) => w.text === "Orbique")!;

    expect(stray.withinAudio).toBe(false);
    expect(stray.startFraction).toBeLessThanOrEqual(1);
  });

  it("marks the words that fall inside the audio as inside", () => {
    expect(timeline().words.every((word) => word.withinAudio)).toBe(true);
  });
});

describe("which word is sounding at a given moment", () => {
  it("is the word whose interval contains the time", () => {
    // The marker word is listed first by `buildTimeline`, so index by text rather
    // than position here: this test is about which word sounds, not where it sits
    // in the list.
    const { words } = timeline();

    expect(activeWordAt(words, 300)?.text).toBe("Kolvig.");
    expect(activeWordAt(words, 6_400)?.text).toBe("Kolvig.");
    expect(activeWordAt(words, 12_400)?.text).toBe("Zorblax.");
  });

  it("lists the marker word first, so it is not matched twice", () => {
    const { words, markers } = timeline();

    expect(words[0]).toBe(markers[0]!.returnedWord);
  });

  it("is nothing at all in the silence between words", () => {
    // The gaps are where a VAD commit boundary falls, so rendering a word as active
    // across one would misattribute the silence to the recogniser.
    const { words } = timeline();

    expect(activeWordAt(words, 3_000)).toBeNull();
  });

  it("treats a word's start as inside it and its end as outside it", () => {
    const { words } = timeline();
    const first = words.find((word) => word.text === "Kolvig.")!;

    expect(activeWordAt(words, first.startMs)?.text).toBe(first.text);
    expect(activeWordAt(words, first.endMs)).toBeNull();
  });

  it("is nothing before the first word and after the last", () => {
    const { words } = timeline();

    expect(activeWordAt(words, 0)).toBeNull();
    expect(activeWordAt(words, 99_999)).toBeNull();
  });

  it("is the same object the track draws, so they cannot disagree", () => {
    // The playhead and the word list both read from this. Returning a freshly
    // computed word here would let the highlight drift from the rendered marker.
    const { words, markers } = timeline();

    expect(activeWordAt(words, 12_400)).toBe(markers[0]!.returnedWord);
  });
});

describe("the record's identity reaches the model", () => {
  it("keeps the condition, strategy and repeat the record names", () => {
    const model = timeline({ conditionId: "manual_2", commitStrategy: "manual" });

    expect(model.conditionId).toBe("manual_2");
    expect(model.commitStrategy).toBe("manual");
    expect(model.repeatIndex).toBe(2);
  });

  it("names the unit the API returned timestamps in", () => {
    // Seconds, not milliseconds. The viewer never converts anything, so this is
    // how a reader learns the raw values were seconds.
    expect(timeline().sourceTimestampUnit).toBe("seconds");
  });

  it("exposes the sample geometry the numbers were measured against", () => {
    const model = timeline();

    expect(model.sampleRate).toBe(SAMPLE_RATE);
    expect(model.sampleCount).toBe(SAMPLE_COUNT);
  });

  it("reports whether a credential was present, as a boolean and nothing more", () => {
    expect(timeline().apiKeyPresent).toBe(true);
  });

  it("names the marker text from the manifest rather than assuming one", () => {
    expect(timeline().markers[0]!.text).toBe(MARKER_TEXT);
  });
});

import { describe, expect, it } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { RunViewer } from "./RunViewer.js";
import { buildTimeline } from "./timeline.js";
import { parseCommitExtents } from "./runIndex.js";
import { parseRunRecord } from "./runRecord.js";
import { FakePlayback } from "./testing/fakePlayback.js";
import {
  MARKER_RETURNED_MS,
  syntheticCommitsJson,
  syntheticRecordJson,
} from "./testing/syntheticRecord.js";

function setup(options: Parameters<typeof syntheticRecordJson>[0] = {}) {
  const record = parseRunRecord(syntheticRecordJson(options));
  const timeline = buildTimeline(record, parseCommitExtents(syntheticCommitsJson(options)));
  const playback = new FakePlayback();
  render(<RunViewer timeline={timeline} playback={playback} />);
  return { playback, timeline };
}

/** Move the playhead as playback would.
 *
 *  Wrapped in `act`, because the fake calls subscribers directly: without it React
 *  has not been told a state change happened and the rendered playhead is stale. */
function playTo(playback: FakePlayback, timeMs: number | null): void {
  act(() => playback.advanceTo(timeMs));
}

/** The word markers on the track, in the order the track renders them. */
function markers() {
  return screen.getAllByTestId("word-marker");
}

describe("the viewer says what it is showing", () => {
  it("states that this is saved evidence rather than a live capture", () => {
    setup();

    expect(
      screen.getByText(/saved evidence/i),
      "a reader must be able to tell replayed evidence from a live capture at a glance",
    ).toBeInTheDocument();
  });

  it("states that no capture was re-run to produce it", () => {
    // The distinguishing claim from a live capture: this is the same file the
    // comparison read, not a fresh request.
    setup();

    expect(screen.getByText(/no capture was re-run/i)).toBeInTheDocument();
  });

  it("names the condition and the strategy as one thing", () => {
    // A VAD run mislabelled as a manual one would destroy the control, so the
    // strategy is read together with the condition rather than left to the filename.
    setup({ conditionId: "manual_2", commitStrategy: "manual" });

    expect(
      screen.getByRole("heading", { name: "manual_2 (manual)" }),
    ).toBeInTheDocument();
  });

  it("names the repeat it is showing", () => {
    setup({ conditionId: "manual_2", commitStrategy: "manual" });

    expect(screen.getByText(/repeat 2/)).toBeInTheDocument();
  });

  it("distinguishes a VAD run from a manual control in its own heading", () => {
    // The whole experiment rests on these two being told apart.
    setup({ conditionId: "vad_2", commitStrategy: "vad" });

    expect(screen.getByRole("heading", { name: "vad_2 (vad)" })).toBeInTheDocument();
  });

  it("states the unit the API returned timestamps in, and how the figures relate to it", () => {
    // The record's own `source_timestamp_unit` is seconds, while every figure on the
    // page is the converted millisecond value. Saying only one of those would leave a
    // reader comparing a "seconds" label against figures marked "ms".
    setup();

    const units = screen.getByText(/returned word timestamps in/i);

    expect(units).toHaveTextContent(/seconds/);
    expect(units).toHaveTextContent(/converts them to milliseconds once, on ingest/i);
    expect(units).toHaveTextContent(/viewer converts nothing itself/i);
  });
});

describe("the words on the track", () => {
  it("renders a marker for every returned word", () => {
    const { timeline } = setup();

    expect(markers()).toHaveLength(timeline.words.length);
  });

  it("places each word at the fraction of the audio its timestamp implies", () => {
    const { timeline } = setup();
    const marker = timeline.markers[0]!.returnedWord;

    const rendered = markers().find((node) => within(node).queryByText(marker.text) !== null)!;

    expect(rendered.style.left).toBe(`${marker.startFraction * 100}%`);
  });

  it("names each word's returned start in milliseconds", () => {
    setup();

    expect(
      screen.getByRole("button", { name: new RegExp(`Zorblax.*${MARKER_RETURNED_MS} ms`) }),
    ).toBeInTheDocument();
  });
});

describe("clicking a word seeks the audio to it", () => {
  it("seeks to the timestamp the server returned for that word", async () => {
    const { playback } = setup();

    await userEvent.click(screen.getByRole("button", { name: /Zorblax/ }));

    expect(playback.seeks).toEqual([MARKER_RETURNED_MS]);
  });

  it("seeks to the first word when the first word is clicked", async () => {
    const { playback, timeline } = setup();
    const earlier = timeline.words.find((word) => word.text === "Kolvig.")!;

    await userEvent.click(screen.getAllByRole("button", { name: /Kolvig/ })[0]!);

    expect(playback.seeks).toEqual([earlier.startMs]);
  });

  it("does not move the playhead to where the marker's clip was inserted", async () => {
    // The insertion point is 12000 ms; the returned word is at 12380 ms. Seeking to
    // the insertion point would hide the drift the viewer exists to show.
    const { playback } = setup();

    await userEvent.click(screen.getByRole("button", { name: /Zorblax/ }));

    expect(playback.seeks).not.toContain(12_000);
  });
});

describe("the marker's two positions are both shown, and kept apart", () => {
  it("shows where the marker's clip was inserted", () => {
    setup();

    // Named as an insertion point, not an expected timestamp: the clip carries
    // leading silence, so its position is not where the word began.
    expect(screen.getByText(/clip inserted at/i)).toBeInTheDocument();
  });

  it("shows the returned timestamp beside it", () => {
    setup();

    expect(screen.getByText(/returned at/i)).toBeInTheDocument();
  });

  it("does not compute the difference on the reader's behalf", () => {
    // The gap between the two figures is the measurement, and issue #6 is where it
    // gets compared across conditions. A viewer that quietly subtracted them would
    // be asserting an offset from a single run, which is not a measurement.
    setup();

    expect(screen.queryByText(/drift of|offset of|lag of/i)).not.toBeInTheDocument();
  });
});

describe("the playhead follows the audio", () => {
  it("highlights the word that is sounding", () => {
    const { playback } = setup();

    playTo(playback, 12_400);

    expect(screen.getByRole("button", { name: /Zorblax/ })).toHaveAttribute(
      "data-sounding",
      "true",
    );
    for (const marker of screen.getAllByRole("button", { name: /Kolvig/ })) {
      expect(marker).toHaveAttribute("data-sounding", "false");
    }
  });

  it("highlights nothing during the silence between words", () => {
    // The gaps are where a VAD commit boundary falls. Highlighting a word across one
    // would attribute the silence to the recogniser.
    const { playback } = setup();

    playTo(playback, 3_000);

    for (const marker of markers()) {
      expect(marker.getAttribute("data-sounding")).toBe("false");
    }
  });

  it("moves off the previous word when the next one starts", () => {
    const { playback } = setup();
    // Indexed by text rather than position: the track lists the marker word first.
    const [first, second] = screen.getAllByRole("button", { name: /Kolvig/ });

    playTo(playback, 300);
    expect(first!).toHaveAttribute("data-sounding", "true");

    playTo(playback, 6_400);
    expect(first!).toHaveAttribute("data-sounding", "false");
    expect(second!).toHaveAttribute("data-sounding", "true");
  });

  it("shows no playhead at all while the position is unknown", () => {
    // Before the audio loads there is no position. Drawing one at zero would put a
    // plausible place on a track that has none.
    render(
      <RunViewer
        timeline={buildTimeline(
          parseRunRecord(syntheticRecordJson()),
          parseCommitExtents(syntheticCommitsJson()),
        )}
        playback={new FakePlayback(null)}
      />,
    );

    expect(screen.queryByTestId("playhead")).not.toBeInTheDocument();
  });

  it("places the playhead at the fraction of the audio it is at", () => {
    const { playback } = setup();

    playTo(playback, 9_000);

    expect(screen.getByTestId("playhead").style.left).toBe("50%");
  });

  it("keeps the playhead inside the word it is highlighting", () => {
    // Both are read from the same clock, so a drift between them would highlight a
    // word the playhead is not actually in. The playhead sits 20 ms after the word's
    // start because 12400 ms is 20 ms into a word that began at 12380 ms.
    const { playback } = setup();

    playTo(playback, 12_400);

    const playhead = Number.parseFloat(screen.getByTestId("playhead").style.left);
    const marker = screen.getByRole("button", { name: /Zorblax/ });
    const start = Number.parseFloat(marker.style.left);
    const end = start + Number.parseFloat(marker.style.width);

    expect(playhead).toBeGreaterThanOrEqual(start);
    expect(playhead).toBeLessThan(end);
  });
});

describe("a word the audio does not contain is still shown", () => {
  it("lists it with its real timestamps rather than dropping or clamping it", () => {
    const record = parseRunRecord(syntheticRecordJson());
    const words = [
      ...record.words,
      { text: "Orbique", start_ms: 19_000, end_ms: 19_400, logprob: -0.3 },
    ];
    render(
      <RunViewer
        timeline={buildTimeline({ ...record, words }, [])}
        playback={new FakePlayback()}
      />,
    );

    const stray = screen.getByRole("button", { name: /Orbique/ });

    expect(stray).toBeInTheDocument();
    expect(stray).toHaveAccessibleName(/19000 ms/);
    expect(stray).toHaveAttribute("data-within-audio", "false");
  });
});

// --- Commit boundaries ---------------------------------------------------------

describe("where the server cut", () => {
  it("marks every commit this run returned", () => {
    // Two preceding segments and the marker: three commits, so any reader can see
    // that the drift is being read against a specific number of them.
    setup();

    expect(screen.getAllByTestId("commit-span")).toHaveLength(3);
  });

  it("draws a commit where its own words were returned, not where the clip was inserted", () => {
    // The measured commit starts at 12380 ms. Drawing it at the 12000 ms insertion
    // point would hide the very figure the run exists to show.
    const { timeline } = setup();

    const measured = screen
      .getAllByTestId("commit-span")
      .find((node) => node.dataset["commitIndex"] === "3")!;

    expect(measured.style.left).toBe(`${timeline.commits[2]!.firstFraction! * 100}%`);
  });

  it("marks the silence the cut fell in, rather than a line pretending to know where", () => {
    // The record does not say where in the silence the server committed -- only that
    // the previous commit ended there and this one began here. Drawing a boundary at
    // a chosen point inside the gap would be a claim nobody can check.
    setup();

    const boundaries = screen.getAllByTestId("commit-boundary");
    expect(boundaries).toHaveLength(2);
    // Commit 2 was cut from the 5.7 s of silence after commit 1; commit 3 from the
    // silence between 6720 ms and the marker at 12380 ms.
    expect(boundaries[0]).toHaveAttribute("title", expect.stringContaining("640 ms and 6320 ms"));
    expect(boundaries[1]).toHaveAttribute(
      "title",
      expect.stringContaining("6720 ms and 12380 ms"),
    );
    expect(boundaries[0]).toHaveAttribute("title", expect.stringContaining("does not say where"));
  });

  it("draws no boundary before the first commit, which was cut from the start", () => {
    // Two boundaries for three commits. A third would claim a cut before any audio.
    setup();

    expect(screen.getAllByTestId("commit-boundary")).toHaveLength(2);
    expect(screen.getAllByTestId("commit-span")).toHaveLength(3);
  });

  it("says who chose the cut points under VAD", () => {
    // The server's own segmentation is the variable under test, so a reader must
    // not think the runner picked these.
    setup({ conditionId: "vad_2", commitStrategy: "vad" });

    expect(screen.getByText(/voice activity detection chose/i)).toBeInTheDocument();
  });

  it("says who chose the cut points under manual commits", () => {
    // The control's cut points were requested at known samples. Saying "the server
    // chose these" here would misdescribe the one run whose boundaries are known.
    setup({ conditionId: "manual_2", commitStrategy: "manual" });

    expect(screen.getByText(/runner requested/i)).toBeInTheDocument();
  });

  it("says the record does not narrow the cut any further", () => {
    // Without this, a band on the track reads as a boundary the server reported.
    setup();

    expect(screen.getByText(/does not say where within it/i)).toBeInTheDocument();
  });
});

describe("the commits behind the track, as figures", () => {
  it("lists each commit's first and last returned word", () => {
    setup();

    const row = screen.getByRole("row", { name: /^1 .*220/ });
    expect(row).toHaveTextContent("220");
    expect(row).toHaveTextContent("640");
  });

  it("lists the measured commit at the timestamp the server returned", () => {
    setup();

    expect(
      screen.getByRole("row", { name: new RegExp(`^3 .*${MARKER_RETURNED_MS}`) }),
    ).toBeInTheDocument();
  });

  it("says a commit returned no words rather than drawing it at zero", () => {
    // Zero is a position. A commit the server returned empty has none, and saying
    // "0 ms" would put a boundary at the very start of the audio.
    const record = parseRunRecord(syntheticRecordJson());
    const real = syntheticCommitsJson();
    render(
      <RunViewer
        timeline={buildTimeline(
          record,
          parseCommitExtents([
            real[0]!,
            { commit_index: 2, word_count: 0, first_word_ms: null, last_word_ms: null },
            real[2]!,
          ]),
        )}
        playback={new FakePlayback()}
      />,
    );

    const empty = screen.getByRole("row", { name: /^2 / });
    expect(empty).toHaveTextContent(/no word/);
    expect(empty).not.toHaveTextContent(/\b0 ms/);
  });

  it("keeps an empty commit in the sequence, so the count stays right", () => {
    const record = parseRunRecord(syntheticRecordJson());
    const real = syntheticCommitsJson();
    render(
      <RunViewer
        timeline={buildTimeline(
          record,
          parseCommitExtents([
            real[0]!,
            { commit_index: 2, word_count: 0, first_word_ms: null, last_word_ms: null },
            real[2]!,
          ]),
        )}
        playback={new FakePlayback()}
      />,
    );

    expect(screen.getAllByRole("row", { name: /^\d / })).toHaveLength(3);
  });
});

describe("a run that returned no commits at all", () => {
  it("says so, rather than drawing a track with nothing on it", () => {
    // An empty commit list is an observation about the run. A track with no
    // boundaries and no explanation looks like a viewer that lost the data.
    const { timeline } = timelineWithNoCommits();

    expect(screen.getByText(/returned no timestamped commit/i)).toBeInTheDocument();
    expect(screen.queryAllByTestId("commit-boundary")).toHaveLength(0);
    expect(timeline.commits).toEqual([]);
  });
});

function timelineWithNoCommits() {
  const timeline = buildTimeline(parseRunRecord(syntheticRecordJson()), []);
  render(<RunViewer timeline={timeline} playback={new FakePlayback()} />);
  return { timeline };
}

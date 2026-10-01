/**
 * The page as a reader arrives at it.
 *
 * The bundle is stubbed from memory, shaped like what `make viewer-export` writes,
 * so these tests never touch the network -- and therefore cost nothing, which is
 * what lets the page be built and checked before a single API call is spent.
 *
 * What is asserted here is the wiring, not the analysis. The deltas belong to the
 * exported report and are tested in `ConditionTable.test.tsx`; what this file
 * checks is that a reader can reach every run in the bundle, that a switch says so
 * in the URL, and that each of the three documents the page reads can fail on its
 * own without taking the others with it.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App.js";
import { HAVE_METADATA } from "./playback.js";
import { syntheticCommitsJson, syntheticRecordJson } from "./testing/syntheticRecord.js";

/** The four conditions this project runs, with three repeats each.
 *
 *  Shaped like the real bundle -- the same condition ids, the same strategies, the
 *  same three repeats -- so a switch that resolves against this has to resolve
 *  against the real index too.
 */
const BUNDLE = {
  vad_0: ["2026-10-01T21-09-41Z__vad_0__rep0", "2026-10-01T21-10-57Z__vad_0__rep1", "2026-10-01T21-12-13Z__vad_0__rep2"],
  vad_1: ["2026-10-01T21-10-00Z__vad_1__rep0", "2026-10-01T21-11-16Z__vad_1__rep1", "2026-10-01T21-12-32Z__vad_1__rep2"],
  vad_2: ["2026-10-01T21-10-19Z__vad_2__rep0", "2026-10-01T21-11-35Z__vad_2__rep1", "2026-10-01T21-12-51Z__vad_2__rep2"],
  manual_2: ["2026-10-01T21-10-37Z__manual_2__rep0", "2026-10-01T21-11-54Z__manual_2__rep1", "2026-10-01T21-12-58Z__manual_2__rep2"],
} as const;

const REP0 = BUNDLE.vad_2[0];

/** The bundle's own report, carried by the same condition ids. */
function reportJson() {
  return {
    marker_text: "Zorblax",
    anchor_condition_id: "vad_0",
    match_rule_note:
      "Every delta differences one condition's median marker timestamp against the anchor's. No timestamp is compared to a clip insertion point.",
    drift_detected: true,
    conditions: Object.entries(BUNDLE).map(([conditionId, runIds], index) => {
      const median = [12_200, 12_300, 12_380, 12_200][index]!;
      const isAnchor = conditionId === "vad_0";
      return {
        condition_id: conditionId,
        commit_strategy: conditionId.startsWith("manual") ? "manual" : "vad",
        prior_segment_count: conditionId === "vad_0" ? 0 : conditionId === "vad_1" ? 1 : 2,
        observed_commit_count: runIds.map(() => (isAnchor ? 1 : index + 1)),
        repeat_count: 3,
        marker_start_ms: runIds.map(() => median),
        median_marker_ms: median,
        spread_ms: 0,
        is_anchor: isAnchor,
        delta_vs_anchor_ms: isAnchor ? null : median - 12_200,
        delta_spread_ms: isAnchor ? null : 0,
        delta_interval_ms: isAnchor ? null : [median - 12_200, median - 12_200],
      };
    }),
    claims: [],
    control: {
      condition_id: "manual_2",
      manual_condition_id: "manual_2",
      compares_to_condition_id: "vad_2",
      manual_delta_ms: 0,
      vad_delta_ms: 180,
      claimed_manual_step_ms: 1,
      manual_accumulates: false,
      manual_matches_claim: true,
      interpretation: "Offsets do not accumulate under manual commits.",
    },
    context: {
      conditions: Object.keys(BUNDLE).map((conditionId) => ({
        condition_id: conditionId,
        insertion_point_gap_ms: 200,
        clip_padding_ms: 120,
        note: "Marker timestamp minus the sample its clip was inserted at. Not a measured offset.",
      })),
      note: "These figures are not the measurement.",
    },
    timestamp_quantum_ms: 20,
    claims_source: "elevenlabs-python#849",
    claims_filed: "2026-08-19",
    reporter_claimed_offset_ms: { vad_0: 9, vad_1: 109, vad_2: 209 },
    reporter_claimed_manual_offset_ms: 10,
  };
}

function indexJson() {
  return {
    note: "Saved evidence, replayed from committed clips. No capture was re-run and no API call was made to build this.",
    runs: Object.entries(BUNDLE).flatMap(([conditionId, runIds]) =>
      runIds.map((runId, repeatIndex) => ({
        run_id: runId,
        condition_id: conditionId,
        commit_strategy: conditionId.startsWith("manual") ? "manual" : "vad",
        repeat_index: repeatIndex,
        audio_path: `audio/${runId}.wav`,
        has_audio: true,
        commits: syntheticCommitsJson(),
      })),
    ),
  };
}

function recordJson(runId: string) {
  const match = Object.entries(BUNDLE).find(([, ids]) => ids.includes(runId as never));
  const [conditionId, runIds] = match!;
  return syntheticRecordJson({
    conditionId,
    commitStrategy: conditionId.startsWith("manual") ? "manual" : "vad",
    repeatIndex: runIds.indexOf(runId as never),
  });
}

function stubBundle(
  options: {
    readonly index?: unknown;
    readonly comparison?: unknown;
    readonly record?: unknown;
    readonly fails?: string;
  } = {},
) {
  const index = options.index ?? indexJson();
  const comparison = options.comparison ?? { report: reportJson() };

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (options.fails !== undefined && url.includes(options.fails)) {
        return { ok: false, status: 404, statusText: "Not Found" };
      }
      let body: unknown;
      if (url.endsWith("runs.json")) body = index;
      else if (url.endsWith("comparison.json")) body = comparison;
      // The record is looked up by the run the page asked for, so a switch really
      // does load a different run rather than the same one under a new heading.
      else {
        const runId = url.replace("runs/", "").replace(".json", "");
        body = options.record ?? recordJson(runId);
      }
      return { ok: true, status: 200, statusText: "OK", json: async () => body };
    }),
  );
}

function setRunQuery(value: string | null): void {
  const url = new URL(window.location.href);
  if (value === null) url.searchParams.delete("run");
  else url.searchParams.set("run", value);
  window.history.replaceState({}, "", url);
}

function currentRunId(): string | null {
  return new URLSearchParams(window.location.search).get("run");
}

// The URL is shared state that outlives a test. Left set, a `?run=` from one test
// silently decides which run every later test loads.
afterEach(() => {
  setRunQuery(null);
});

describe("the page loads the bundle and one run", () => {
  it("shows the run the bundle lists first", async () => {
    stubBundle();
    render(<App />);

    expect(await screen.findByRole("heading", { name: /vad_0/ })).toBeInTheDocument();
  });

  it("gives the reader the audio to play", async () => {
    stubBundle();
    render(<App />);

    await screen.findByRole("heading", { name: /vad_0/ });

    // The exact label, not `/audio/`: the track group is also labelled in terms of
    // audio, and a loose match here would pass against the wrong element.
    expect(screen.getByLabelText("Run audio")).toHaveAttribute(
      "src",
      `audio/${BUNDLE.vad_0[0]}.wav`,
    );
  });

  it("states that nothing was captured to build the page", async () => {
    stubBundle();
    render(<App />);

    // Scoped to the run viewer's own line: the bundle's note says it too, and a loose
    // match here would pass against the wrong element.
    const viewer = (await screen.findByRole("heading", { name: /vad_0/ })).closest("article")!;
    expect(within(viewer).getByText(/No capture was re-run/)).toBeInTheDocument();
  });

  it("shows the comparison, with the figures the report carries", async () => {
    // The deltas are read from the exported report, not computed here, so a reader
    // checking one against the README is checking the same calculation.
    stubBundle();
    render(<App />);

    const table = await screen.findByTestId("conditions-table");
    expect(within(table).getByRole("row", { name: /^vad_2/ })).toHaveTextContent("+180");
    expect(within(table).getByRole("row", { name: /^manual_2/ })).toHaveTextContent("+0");
  });

  it("marks the selected run's own returned figure beside the condition's median", async () => {
    // One run is not the median of three, and quoting it as such is the single-number
    // mistake this project exists to avoid.
    stubBundle();
    setRunQuery(BUNDLE.vad_2[2]);
    render(<App />);

    expect(await screen.findByTestId("selected-run")).toHaveTextContent(
      `returned the marker at 12,380 ms`,
    );
  });
});

describe("switching between strategies and conditions", () => {
  it("moves to the manual control's own run, and says so in the URL", async () => {
    // The control is a check on the VAD family. Being able to get to it in one click
    // is what makes the comparison worth making.
    stubBundle();
    render(<App />);
    await screen.findByRole("heading", { name: /vad_0/ });

    await userEvent.click(
      within(screen.getByRole("group", { name: "Commit strategy" })).getByRole("button", {
        name: /manual/,
      }),
    );

    await screen.findByRole("heading", { name: "manual_2 (manual)" });
    expect(currentRunId()).toBe(BUNDLE.manual_2[0]);
  });

  it("switches to a named condition within the strategy", async () => {
    stubBundle();
    render(<App />);
    await screen.findByRole("heading", { name: /vad_0/ });

    await userEvent.click(
      within(screen.getByRole("group", { name: "Condition" })).getByRole("button", {
        name: /^vad_2/,
      }),
    );

    expect(await screen.findByRole("heading", { name: "vad_2 (vad)" })).toBeInTheDocument();
  });

  it("selects an individual repeat rather than only the condition's aggregate", async () => {
    stubBundle();
    setRunQuery(BUNDLE.vad_2[0]);
    render(<App />);
    await screen.findByRole("heading", { name: "vad_2 (vad)" });

    await userEvent.click(
      within(screen.getByRole("group", { name: "Repeat" })).getByRole("button", {
        name: /repeat 1/,
      }),
    );

    const viewer = (await screen.findByRole("heading", { name: "vad_2 (vad)" })).closest("article")!;
    expect(within(viewer).getByText(/repeat 1/)).toBeInTheDocument();
    expect(currentRunId()).toBe(BUNDLE.vad_2[1]);
  });

  it("keeps the repeat when the condition changes, so like is compared with like", async () => {
    stubBundle();
    setRunQuery(BUNDLE.vad_2[2]);
    render(<App />);
    await screen.findByRole("heading", { name: "vad_2 (vad)" });

    await userEvent.click(
      within(screen.getByRole("group", { name: "Condition" })).getByRole("button", {
        name: /^vad_1/,
      }),
    );

    await screen.findByRole("heading", { name: "vad_1 (vad)" });
    expect(currentRunId()).toBe(BUNDLE.vad_1[2]);
  });

  it("marks the run on screen in the table, so the two views agree", async () => {
    stubBundle();
    setRunQuery(BUNDLE.vad_1[1]);
    render(<App />);
    await screen.findByRole("heading", { name: "vad_1 (vad)" });

    const row = within(screen.getByTestId("conditions-table")).getByRole("row", {
      name: /^vad_1/,
    });
    expect(within(row).getByRole("button", { name: /rep 1/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("switches run from the table as well as the picker", async () => {
    // The table is where a reader comparing conditions is looking, so it has to be
    // where they can choose the repeat.
    stubBundle();
    setRunQuery(BUNDLE.vad_2[0]);
    render(<App />);
    await screen.findByRole("heading", { name: "vad_2 (vad)" });

    await userEvent.click(
      within(
        within(screen.getByTestId("conditions-table")).getByRole("row", { name: /^manual_2/ }),
      ).getAllByRole("button")[2]!,
    );

    expect(await screen.findByRole("heading", { name: "manual_2 (manual)" })).toBeInTheDocument();
    expect(currentRunId()).toBe(BUNDLE.manual_2[2]);
  });

  it("loads only the run it is showing, not all twelve", async () => {
    // Fetching every record to show one would make the page slow for no gain, and
    // the request list is also what proves the free-to-view claim.
    stubBundle();
    render(<App />);
    await screen.findByRole("heading", { name: /vad_0/ });

    const requested = (fetch as unknown as { mock: { calls: [string][] } }).mock.calls.map(
      (call) => call[0],
    );
    expect(requested).toEqual(["runs.json", "comparison.json", `runs/${BUNDLE.vad_0[0]}.json`]);
  });
});

describe("choosing a run by name", () => {
  it("loads the run named in the URL", async () => {
    stubBundle();
    setRunQuery(BUNDLE.manual_2[1]);
    render(<App />);

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(`runs/${BUNDLE.manual_2[1]}.json`));
  });

  it("says so plainly when the named run is not in the bundle", async () => {
    // Falling back to some other run would be worse than failing: the reader asked
    // for one piece of evidence and would be shown another without noticing.
    stubBundle();
    setRunQuery("not-a-run");
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/not-a-run/);
  });
});

describe("anything that goes wrong is visible", () => {
  it("reports a missing bundle rather than showing an empty timeline", async () => {
    stubBundle({ fails: "runs.json" });
    render(<App />);

    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent(/runs\.json returned 404/);
    // An empty track is indistinguishable from a run that returned no words.
    expect(screen.queryByTestId("word-marker")).not.toBeInTheDocument();
  });

  it("reports a malformed run record rather than rendering what it can", async () => {
    stubBundle({ record: { run_id: "broken", commit_strategy: "automatic" } });
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/not valid/i);
  });

  it("reports a malformed comparison rather than showing a table of guesses", async () => {
    // The report is the only source of every delta on the page. A malformed one must
    // be refused, not half-read.
    stubBundle({ comparison: { report: { conditions: [{ condition_id: "vad_0" }] } } });
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/comparison is not valid/i);
  });

  it("reports a malformed index rather than guessing which runs exist", async () => {
    stubBundle({ index: { note: "x", runs: [{ run_id: "a" }] } });
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/index is not valid/i);
  });

  it("reports a record whose marker is missing from its transcript", async () => {
    stubBundle({ record: syntheticRecordJson({ markerStartMs: null }) });
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/supports no measurement/i);
  });

  it("says when a run's audio could not be rebuilt, and offers no player", async () => {
    // `make viewer-export` records this as `has_audio: false` rather than omitting
    // the run, so a bundle can carry a readable record with no playable audio.
    const index = indexJson();
    index.runs[0]!.has_audio = false;
    stubBundle({ index });
    render(<App />);

    await screen.findByRole("heading", { name: /vad_0/ });

    expect(screen.getByRole("alert")).toHaveTextContent(/could not be rebuilt/i);
    expect(screen.getByLabelText("Run audio")).not.toHaveAttribute("src");
  });
});

describe("a bundle that cannot support a comparison", () => {
  it("says so, and still shows the runs", async () => {
    // One run is not a measurement, but it is the audio and the returned words, and a
    // reader who came for those should not be told there is nothing here.
    stubBundle({ comparison: { unavailable_reason: "no anchor condition in this bundle" } });
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/no anchor condition/);
    expect(screen.getByRole("heading", { name: /vad_0/ })).toBeInTheDocument();
    expect(screen.queryByTestId("conditions-table")).not.toBeInTheDocument();
  });

  it("withholds the selected run's figure rather than quoting the wrong word", async () => {
    // The comparison is what names the measured word. With no report, there is
    // nothing to check the run's marker against, and the figure is withheld.
    stubBundle({ comparison: { unavailable_reason: "no anchor condition" } });
    setRunQuery(BUNDLE.vad_2[0]);
    render(<App />);

    await screen.findByRole("heading", { name: "vad_2 (vad)" });

    expect(screen.queryByTestId("selected-run")).not.toBeInTheDocument();
  });
});

describe("the page reads no credential", () => {
  it("requests only the bundle's own files", async () => {
    // Everything the page shows must come from files in the repository. A request
    // anywhere else would mean the free-to-view claim was wrong.
    stubBundle();
    render(<App />);

    await screen.findByRole("heading", { name: /vad_0/ });

    const requested = (fetch as unknown as { mock: { calls: [string][] } }).mock.calls.map(
      (call) => call[0],
    );
    expect(requested.every((url) => !url.startsWith("http") && !url.startsWith("/"))).toBe(true);
  });
});

describe("clicking a word on the real page", () => {
  it("moves the audio element a reader actually hears", async () => {
    // `RunViewer` is tested against a fake transport, so this is the only test that
    // proves the browser half is wired up: click a word, and the real `<audio>`'s
    // `currentTime` lands on the returned timestamp.
    stubBundle();
    setRunQuery(REP0);
    render(<App />);
    await screen.findByRole("heading", { name: "vad_2 (vad)" });

    const audio = screen.getByLabelText(/run audio/i) as HTMLAudioElement;
    // jsdom never loads media, so it reports `readyState` 0 and the transport would
    // hold the seek forever. Standing in for "the audio has loaded" is the only way
    // to reach the branch that writes to the element.
    Object.defineProperty(audio, "readyState", { value: HAVE_METADATA, configurable: true });
    audio.dispatchEvent(new Event("loadedmetadata"));

    await userEvent.click(screen.getByRole("button", { name: /Zorblax/ }));

    // 12380 ms, in the seconds a media element counts. Not 12000 ms: the click seeks
    // to where the server said the word was, not to where its clip was inserted.
    expect(audio.currentTime).toBeCloseTo(12.38, 6);
  });
});

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
    /** A static host answering with something that is not JSON, which is what a
     *  missing file looks like on several of them. */
    readonly servesInsteadOfJson?: boolean;
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
      if (options.servesInsteadOfJson === true) {
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => {
            throw new SyntaxError("Unexpected token '<', \"<!doctype\"... is not valid JSON");
          },
        };
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

describe("the page states the question it answers", () => {
  // The public link's promise is that a maintainer sees the question, the evidence and
  // the result within thirty seconds of opening it. The evidence and the result are
  // the table below, which this page already carried. The question was missing: a
  // reader landing here from a link would find a table of deltas with nothing saying
  // what the deltas were deltas *of*, which is a number to be puzzled at rather than a
  // finding to be judged.

  it("says what is being asked, in the page's own words", async () => {
    stubBundle();
    render(<App />);

    // On the words rather than the whole sentence, and with the apostrophe allowed to
    // be typographic: the page writes a curly `’`, and a test that only matched the
    // ASCII form would fail against correct prose.
    expect(await screen.findByTestId("the-question")).toHaveTextContent(
      /word.?s returned timestamp drift/i,
    );
  });

  it("names the report the question comes from, and credits its author", async () => {
    // Taken from the report rather than hard-coded in the page, so the credit cannot
    // name one issue while the claim table below it names another.
    stubBundle();
    render(<App />);

    const question = await screen.findByTestId("the-question");
    expect(question).toHaveTextContent("elevenlabs-python#849");
    expect(question).toHaveTextContent("2026-08-19");
  });

  it("gives the answer the page's own comparison found", async () => {
    // The question alone would leave a reader to find the verdict in a table. The
    // report's `drift_detected` is the analysis's own conclusion, so it is shown rather
    // than re-decided here.
    stubBundle();
    render(<App />);

    expect(await screen.findByTestId("the-answer")).toHaveTextContent(/drift detected/i);
  });

  it("says the drift did not reproduce when that is what the report found", async () => {
    // The direction of the claim has to be able to go the other way, or this is a
    // finding-shaped decoration that can only ever confirm. Driven by the same flag the
    // analysis sets, so a report that did not reproduce is reported as such.
    //
    // `{ report: ... }` because that is the shape `stubBundle` expects; spreading
    // `reportJson()` at the top level puts the fields beside `report` rather than
    // inside it, and the document is then refused for carrying an unknown key.
    stubBundle({ comparison: { report: { ...reportJson(), drift_detected: false } } });
    render(<App />);

    expect(await screen.findByTestId("the-answer")).toHaveTextContent(/no drift detected/i);
  });

  it("withholds the question when the report cannot be read", async () => {
    // A question with no table under it is a promise the page cannot keep. A bundle
    // whose report will not parse supports no comparison, so it states no question and
    // no answer -- rather than asking something and showing nothing.
    //
    // A malformed *report* rather than a malformed document: the document is parsed
    // strictly, so a string here fails the whole bundle and the run view never renders
    // at all. That is a different failure with its own test.
    stubBundle({ comparison: { report: { conditions: [{ condition_id: "vad_0" }] } } });
    render(<App />);

    await screen.findByTestId("run-list");
    expect(screen.queryByTestId("the-question")).not.toBeInTheDocument();
    expect(screen.queryByTestId("the-answer")).not.toBeInTheDocument();
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

  it("names the file when the host serves something that is not JSON", async () => {
    // A missing file on a static host is often answered with the site's own index
    // page and a 200. The browser's "Unexpected token '<'" says nothing about which
    // file was wrong or that the likely cause is a file that is not there, and a
    // reader who is sent to re-run the capture should not be sent there by a
    // message about a token.
    stubBundle({ servesInsteadOfJson: true });
    render(<App />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/runs\.json/);
    expect(alert).toHaveTextContent(/did not return JSON/i);
    // The whole bundle failed, not one run, and the heading says which: a reader
    // sent to re-run a capture that is not the problem would be chasing the wrong
    // thing.
    expect(alert).toHaveTextContent(/bundle cannot be read/i);
  });

  it("says so when a run's audio cannot be loaded, rather than showing a dead player", async () => {
    // `has_audio: true` is the exporter's word that the WAV was written. A
    // deployment that drops the file anyway leaves a control that does nothing when
    // pressed, which reads as a broken recording rather than a missing file.
    stubBundle();
    render(<App />);
    await screen.findByRole("heading", { name: /vad_0/ });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const audio = screen.getByLabelText("Run audio");
    audio.dispatchEvent(new Event("error"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      new RegExp(`audio/${BUNDLE.vad_0[0]}\\.wav`),
    );
  });

  it("withholds a malformed comparison's figures without taking the run down with them", async () => {
    // The report is the only source of every delta, so it must be refused rather than
    // half-read. But the run record is the only source of the audio, the returned
    // words and the commit boundaries, and a summary failing to parse is no reason to
    // throw away the evidence beside it.
    stubBundle({ comparison: { report: { conditions: [{ condition_id: "vad_0" }] } } });
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be read/i);
    expect(screen.getByRole("heading", { name: /vad_0/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Run audio")).toHaveAttribute(
      "src",
      `audio/${BUNDLE.vad_0[0]}.wav`,
    );
    expect(screen.queryByTestId("conditions-table")).not.toBeInTheDocument();
  });

  it("still marks the commit boundaries when the report cannot be read", async () => {
    // The boundaries come from the run's own record and index, never from the report,
    // so the one thing a reader came to see survives the report's failure.
    stubBundle({ comparison: { report: { nonsense: true } } });
    render(<App />);

    await screen.findByRole("heading", { name: /vad_0/ });
    expect(screen.getAllByTestId("commit-span").length).toBeGreaterThan(0);
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

  it("locates the measured word under the recorded match rule, not a looser one", async () => {
    // A second, laxer rule would decide for itself which word was measured. It would
    // also fail silently: a marker the server returned as `Zorblax,` would stop
    // matching, the figure would vanish, and nothing would say why.
    stubBundle({
      record: {
        ...(recordJson(BUNDLE.vad_2[0]) as Record<string, unknown>),
        words: [
          { text: "Kolvig.", start_ms: 220, end_ms: 640, logprob: -0.8 },
          { text: "Zorblax,", start_ms: 12_380, end_ms: 12_980, logprob: -0.85 },
        ],
      },
    });
    setRunQuery(BUNDLE.vad_2[0]);
    render(<App />);

    await screen.findByRole("heading", { name: "vad_2 (vad)" });

    expect(screen.getByTestId("selected-run")).toHaveTextContent("12,380 ms");
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

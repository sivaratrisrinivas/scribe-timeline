import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App.js";
import { HAVE_METADATA } from "./playback.js";
import { syntheticRecordJson } from "./testing/syntheticRecord.js";

const RUN_ID = "2026-10-01T21-12-51Z__vad_2__rep2";

/** Stand in for the exported bundle, so the test never touches the network.
 *
 *  The index and the record are what `make viewer-export` writes; serving them from
 *  memory keeps the test honest about the shapes while costing nothing to run.
 */
function stubBundle(
  options: { readonly index?: unknown; readonly record?: unknown; readonly fails?: string } = {},
) {
  const index =
    options.index ??
    ({
      note: "Saved evidence.",
      runs: [
        {
          run_id: RUN_ID,
          condition_id: "vad_2",
          commit_strategy: "vad",
          repeat_index: 2,
          audio_path: `audio/${RUN_ID}.wav`,
          has_audio: true,
        },
      ],
    } as unknown);

  const record = options.record ?? syntheticRecordJson();

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (options.fails !== undefined && url.includes(options.fails)) {
        return { ok: false, status: 404, statusText: "Not Found" };
      }
      const body = url === "runs.json" ? index : record;
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

// The URL is shared state that outlives a test. Left set, a `?run=` from one test
// silently decides which run every later test loads.
afterEach(() => {
  setRunQuery(null);
});

describe("the page loads one saved run", () => {
  it("shows the run the bundle lists", async () => {
    stubBundle();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "vad_2 (vad)" })).toBeInTheDocument();
  });

  it("gives the reader the audio to play", async () => {
    stubBundle();
    render(<App />);

    await screen.findByRole("heading", { name: "vad_2 (vad)" });

    // The exact label, not `/audio/`: the track group is also labelled in terms of
    // audio, and a loose match here would pass against the wrong element.
    expect(screen.getByLabelText("Run audio")).toHaveAttribute("src", `audio/${RUN_ID}.wav`);
  });

  it("states that nothing was captured to build the page", async () => {
    stubBundle();
    render(<App />);

    expect(await screen.findByText(/No capture was re-run/)).toBeInTheDocument();
  });
});

describe("choosing a run by name", () => {
  it("loads the run named in the URL", async () => {
    stubBundle();
    setRunQuery(RUN_ID);
    render(<App />);

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(`runs/${RUN_ID}.json`));
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

  it("reports a record whose marker is missing from its transcript", async () => {
    stubBundle({ record: syntheticRecordJson({ markerStartMs: null }) });
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/supports no measurement/i);
  });

  it("says when a run's audio could not be rebuilt, and offers no player", async () => {
    // `make viewer-export` records this as `has_audio: false` rather than omitting
    // the run, so a bundle can carry a readable record with no playable audio.
    stubBundle({
      index: {
        note: "Saved evidence.",
        runs: [
          {
            run_id: RUN_ID,
            condition_id: "vad_2",
            commit_strategy: "vad",
            repeat_index: 2,
            audio_path: `audio/${RUN_ID}.wav`,
            has_audio: false,
          },
        ],
      },
    });
    render(<App />);

    await screen.findByRole("heading", { name: "vad_2 (vad)" });

    expect(screen.getByRole("alert")).toHaveTextContent(/could not be rebuilt/i);
    expect(screen.getByLabelText("Run audio")).not.toHaveAttribute("src");
  });
});

describe("the page reads no credential", () => {
  it("requests only the bundle's own files", async () => {
    // Everything the page shows must come from files in the repository. A request
    // anywhere else would mean the free-to-view claim was wrong.
    stubBundle();
    render(<App />);

    await screen.findByRole("heading", { name: "vad_2 (vad)" });

    const requested = (fetch as unknown as { mock: { calls: [string][] } }).mock.calls.map(
      (call) => call[0],
    );
    expect(requested).toEqual(["runs.json", `runs/${RUN_ID}.json`]);
  });
});

describe("clicking a word on the real page", () => {
  it("moves the audio element a reader actually hears", async () => {
    // `RunViewer` is tested against a fake transport, so this is the only test that
    // proves the browser half is wired up: click a word, and the real `<audio>`'s
    // `currentTime` lands on the returned timestamp.
    stubBundle();
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

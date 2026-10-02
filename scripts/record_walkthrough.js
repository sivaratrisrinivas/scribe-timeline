/**
 * Drives the real viewer and records it: `make walkthrough`.
 *
 * Nothing here is mocked or staged. It opens the built bundle over HTTP from a
 * subpath, exactly as a reader would, and moves through the beats the walkthrough has
 * to hit. If the page breaks, the recording breaks with it, and the failure is visible
 * in the video rather than hidden behind a fixture.
 *
 * Re-record it whenever `viewer/dist` changes. The committed file is a recording of
 * *this* evidence, so a video left behind after a re-export would show numbers the
 * repository no longer holds -- the one failure mode a video cannot fail loudly on,
 * because it still plays.
 *
 * The beats, in order, and what each is for:
 *
 *   1. The timing question, and the credit.  What is being checked, and who filed it,
 *      so nobody watching later mistakes this for the original report.
 *   2. One marker across conditions.  The same word, held identical, while only what
 *      precedes it changes -- the measurement, shown rather than described.
 *   3. The measured differences and the repeat variation.  The delta column, the
 *      spread column, and the manual control's own stated conclusion.
 *   4. The raw evidence and the rerun command.  One run record's untouched events,
 *      and the command that re-derives all of it without a credential.
 *
 * Two things this script does that the page does not:
 *
 *   - It injects a caption bar. Necessary because the recording has no narration, and
 *     a silent video cannot credit anyone.
 *   - It scrolls. Each beat scrolls to the part of the page it is describing, because
 *     the comparison table's deltas sit below the fold at this viewport and a beat
 *     about deltas that shows the page header is not a beat about deltas.
 *
 * The injected style is re-applied after every navigation, because a navigation
 * discards it -- which is why the first attempt recorded 66 seconds with no captions
 * at all, and looked fine until a frame was extracted.
 */

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

/**
 * Playwright is resolved by hand, because this file lives in `scripts/` and `make
 * walkthrough` installs the package into `viewer/`. Node's own resolution walks *up*
 * from this file looking for `node_modules`, so it finds the repository root and stops
 * -- it never looks sideways into a sibling directory.
 *
 * `createRequire` over the viewer's package.json is the fix: it resolves from the
 * directory the dependency was actually installed into. Both locations are tried, so
 * the script runs whether Playwright is in `viewer/node_modules` (the make target) or
 * in a checkout that installed it at the root.
 */
function loadPlaywright() {
  const { createRequire } = require("node:module");
  const here = path.join(__dirname, "..");
  for (const base of [path.join(here, "viewer", "package.json"), path.join(here, "package.json")]) {
    try {
      return createRequire(base)("playwright");
    } catch (error) {
      if (error.code !== "MODULE_NOT_FOUND") throw error;
    }
  }
  throw new Error(
    "playwright is not installed. Run `make walkthrough`, which installs it, or " +
      "`npm install playwright` inside viewer/.",
  );
}

const { chromium } = loadPlaywright();

// Defaults relative to this file, so `node scripts/record_walkthrough.js` works from
// anywhere without being told where the repository is.
const REPO = process.argv[2] || path.resolve(__dirname, "..");
const DIST = path.join(REPO, "viewer", "dist");
const OUT = process.argv[3] || path.join(REPO, "docs", "walkthrough.webm");
const MOUNT = "/scribe-timeline/";

/** Widest the comparison table goes before its columns start wrapping badly. */
const VIEWPORT = { width: 1280, height: 860 };

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".wav": "audio/wav",
};

/** Serve `dist` under a subpath, so the recording shows the page as it is published
 *  rather than as it happens to sit on a dev server rooted at the bundle. */
function serve(root) {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (!url.pathname.startsWith(MOUNT)) {
      res.writeHead(404).end("not found");
      return;
    }
    let rel = url.pathname.slice(MOUNT.length);
    if (rel === "" || rel.endsWith("/")) rel += "index.html";
    const file = path.join(root, rel);
    // Refuse anything that escapes the served root. Every path here comes from this
    // script rather than from a reader, so the traversal is not a live risk -- but the
    // server is the thing standing between the recording and the rest of the disk, and
    // a check that costs one line is cheaper than finding out what it was protecting.
    if (!file.startsWith(root)) {
      res.writeHead(403).end("no");
      return;
    }
    fs.readFile(file, (err, body) => {
      if (err) {
        res.writeHead(404).end("not found");
        return;
      }
      res.writeHead(200, {
        "content-type": MIME[path.extname(file)] || "application/octet-stream",
      });
      res.end(body);
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({ server, base: `http://127.0.0.1:${port}${MOUNT}` });
    });
  });
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/** The caption bar, plus the scroll-into-view helper both of them need. */
const CAPTION_CSS = `
  #beat { position: fixed; left: 0; right: 0; bottom: 0; z-index: 9999;
          background: #14161a; color: #fff;
          font: 600 18px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif;
          padding: 15px 22px; letter-spacing: .005em;
          border-top: 3px solid #4a7fd4; }
`;

async function caption(page, text) {
  await page.addStyleTag({ content: CAPTION_CSS });
  await page.evaluate((t) => {
    let el = document.getElementById("beat");
    if (!el) {
      el = document.createElement("div");
      el.id = "beat";
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
}

/** Scroll so the given element is in view, with the caption bar's height accounted
 *  for so a caption never covers the thing it is describing. */
async function show(page, selector) {
  await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) throw new Error(`nothing to scroll to: ${sel}`);
    const box = el.getBoundingClientRect();
    window.scrollBy({ top: box.top - 120, behavior: "instant" });
  }, selector);
  await wait(600);
}

async function main() {
  const { server, base } = await serve(DIST);
  console.log("serving", DIST, "at", base);

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: VIEWPORT,
    recordVideo: { dir: path.dirname(OUT), size: VIEWPORT },
  });
  const page = await context.newPage();

  // `domcontentloaded` plus an explicit selector wait rather than `networkidle`: the
  // audio element preloads, so the network is not reliably idle within any fixed
  // window, and a timeout here would abort a recording that was working fine.
  await page.goto(base, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="conditions-table"]', { timeout: 30000 });
  await page.waitForSelector('[data-testid="word-marker"][data-marker="true"]', {
    timeout: 30000,
  });

  // --- beat 1: the question, and whose finding this is ----------------------
  await caption(
    page,
    "The question: do Scribe v2 Realtime word timestamps drift ~100 ms per preceding VAD commit? " +
      "Filed by @wujin941005 — elevenlabs-python#849, 2026-08-19. This is an independent rerun, " +
      "and every run here is saved evidence: no capture was re-run and no API call was made.",
  );
  await wait(10000);

  // --- beat 2: one marker, held identical, across conditions ----------------
  // Scrolled to the track, because this beat is about one word staying put.
  await show(page, ".track");
  await caption(
    page,
    "One marker word, identical bytes and sample position in every condition. Only what precedes it changes.",
  );
  await wait(5500);
  for (const condition of ["vad_1", "vad_2"]) {
    await page.click(`nav.picker button:has-text("${condition}")`);
    await page.waitForFunction(
      () => document.querySelectorAll('[data-testid="word-marker"]').length > 0,
    );
    await wait(1);
    await show(page, ".track");
    await wait(4000);
  }

  // --- beat 3: the differences, the spread, and the control -----------------
  // Scrolled to the delta column, which is the figure the finding rests on.
  await show(page, '[data-testid="conditions-table"]');
  await caption(
    page,
    "vad_1 +100 ms, vad_2 +180 ms against the anchor. Three repeats each, every one identical — spread 0 ms.",
  );
  await wait(11000);

  await caption(
    page,
    "The control: manual_2 played byte-identical audio with the same commit count, but the runner chose the cut points. +0 ms.",
  );
  await page.click('nav.picker button:has-text("manual")');
  await wait(5000);
  await show(page, '[data-testid="control-conclusion"]');
  await wait(9000);

  // --- beat 4: the raw evidence, and the rerun command ----------------------
  await caption(
    page,
    "Every figure above is one the analysis wrote. The page subtracts nothing.",
  );
  await wait(4000);

  const runId = await firstRunId(base);
  // The `.json` suffix is not optional. The page builds this URL as
  // `runs/${runId}.json`, and leaving it off produced a 14-second stretch of
  // "not found" at the exact moment the video was promising the raw evidence.
  await page.goto(`${base}runs/${runId}.json`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.body.innerText.includes("session_started"), {
    timeout: 15000,
  });
  await caption(
    page,
    "Raw evidence: every run's untouched event stream is committed. Re-derive all of it, no account needed:  " +
      'make matrix ARGS="--from-saved evidence/runs/*.json"',
  );
  await wait(12000);

  await context.close();
  await browser.close();

  // Playwright names its own output file, and nothing here can ask it for a specific
  // name. So it is renamed afterwards -- which means a run interrupted before this line
  // leaves a second `.webm` in `docs/` with an unrecognised name, and
  // tests/test_walkthrough.py fails on any recording that is not the documented path
  // rather than letting a reader watch a stale one.
  const produced = fs
    .readdirSync(path.dirname(OUT))
    .map((f) => path.join(path.dirname(OUT), f))
    .filter((f) => f.endsWith(".webm") && f !== OUT)
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)[0];
  fs.renameSync(produced, OUT);
  server.close();

  const { duration } = probe(OUT);
  console.log(
    `wrote ${OUT} (${(fs.statSync(OUT).size / 1e6).toFixed(1)} MB, ${duration}s)`,
  );
}

async function firstRunId(base) {
  const body = await (await fetch(`${base}runs.json`)).text();
  return JSON.parse(body).runs[0].run_id;
}

function probe(file) {
  const result = spawnSync("ffprobe", [
    "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file,
  ]);
  return { duration: Math.round(parseFloat(result.stdout.toString().trim()) || 0) };
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

# scribe-timeline

An independent rerun of a reported Scribe Realtime bug, with the evidence committed and
a viewer that replays it for free.

**Status: the reported drift reproduces.** Four conditions × three repeats, measured
against the live API on 2026-10-02. The marker's returned timestamp moved **+100 ms**
with one preceding automatic (VAD) commit and **+180 ms** with two, and **+0 ms** under
manual commits — so the drift tracks each preceding commit rather than accumulating
regardless of who triggers it. Taken per commit, those two figures suggest a step nearer
90 ms than 100; that division is arithmetic on the deltas, not a measurement of a
per-commit step. Every figure here comes from `make matrix` and can be re-derived from the
saved run records at no cost.

**The original finding is [elevenlabs-python#849](https://github.com/elevenlabs/elevenlabs-python/issues/849),
filed by [@wujin941005](https://github.com/wujin941005) on 2026-08-19.** That reporter is
credited as the originator of this finding. This project adds runnable materials, a
dated independent measurement, and a viewer — it does not claim the observation. If the
drift did not reproduce, that would be the finding, and it would be reported here just
as prominently.

## Problem

Issue #849 reports that Scribe v2 Realtime word timestamps drift by roughly 100 ms for
every preceding automatic (VAD) commit, while the same audio under manual commits does
not accumulate the offset. The report is careful — a synthetic-PCM experiment, a table of
results, and an explicit note that the SDKs pass returned timestamps through unmodified —
but it is **not independently reproduced**. Three things follow from that:

1. **Nobody can act on it.** A suspected server-side behaviour change is described in
   prose. There is no runnable fixture, no raw event capture, no way for a maintainer to
   see the effect rather than read about it.
2. **The obvious response is unfalsifiable.** Timestamps can be checked against "where the
   audio was inserted" — but insertion point is not word onset. A reproduction that
   asserts the marker word *should* land at the clip's insertion offset has assumed its
   own answer, and will confirm a bug that isn't there or deny one that is.
3. **The premise may be stale.** An open issue can describe behaviour since fixed
   server-side. Until it is re-run and dated, nobody knows whether this is a live finding
   or a historical one.

## Public evidence

**One link, no login, thirty seconds:**
**<https://sivaratrisrinivas.github.io/scribe-timeline/>**

That page opens with the question and the answer, then the comparison: every condition,
its repeats, its spread, its median marker timestamp, and its delta against the anchor.
Below that sits the full timeline for one run — the audio, every returned word at the
position the server gave it, the marker's clip insertion point beside it, and the silence
each commit was cut from. Click a word to hear it.

It is built from files already in this repository. It makes no request other than for its
own bundle, which a test asserts, and building it spent no API credit.

**A 72-second walkthrough**, if you would rather watch than read (the issue asked
for 75; it is what the recording actually came out at):

[![The walkthrough: question, credit, marker across conditions, measured differences, raw evidence, rerun command](docs/walkthrough.webm)](docs/walkthrough.webm)

*(Click to play. [Or open the file directly](docs/walkthrough.webm) — it is committed, so
it plays straight from the repository.)*

It runs through the timing question and who asked it first, the same marker held across
conditions, the measured differences and the repeat variation, and then the raw event
stream and the exact rerun command.

## What I built

A Python runner that streams controlled synthetic audio through Scribe Realtime under both
commit strategies, and a React/TypeScript viewer that replays the captured runs as a
clickable audio timeline.

The measurement rests on one idea, chosen so it cannot beg the question:

> Hold the final marker's bytes and sample position **identical** across every condition.
> Vary only how many speech segments precede it. Compare **that one word's** returned
> timestamp across conditions.

The zero-preceding-commit condition is the anchor, and every additional preceding commit's
delta *is* the result. No timestamp is ever compared to an insertion point.

A second control guards the conclusion. The same audio with the same number of preceding
commits is streamed with commits requested explicitly rather than chosen by VAD. If that
run's timestamp moves too, the operative variable is commit count; if it does not, the
variable is VAD's triggering. Without it, "VAD causes drift" and "commits cause drift"
are indistinguishable.

There is exactly one test seam, at the **run-record boundary**:

| Above the seam | Below the seam |
| --- | --- |
| WebSocket transport | fixture synthesis |
| real-time pacing | offset computation |
| commit triggering | the viewer |
| event capture | every published number |

A **run record** is the seam's currency: one JSON document pairing a *fixture manifest*
(sample rate, layout, every marker's position **in samples**) with the *raw captured
events* and the *server's echoed configuration*. Everything below the seam is
deterministic and testable offline at no API cost — the fixture generator has no
ElevenLabs dependency, so the property the whole experiment rests on, a marker landing at
an exactly known sample, is verified without a key.

Run records carry no credentials by construction. Every model forbids extra fields, so a
key cannot be smuggled in as a named attribute, and the free-form event payload is walked
for credential-shaped keys at every depth and rejected outright. The published bundle is
checked the same way from the other direction: `tests/test_offline_viewing.py` plants a
key-shaped value in the environment, builds the whole bundle, and greps every byte written
for it.

## Reproduce

```sh
git clone https://github.com/sivaratrisrinivas/scribe-timeline.git
cd scribe-timeline
make setup     # uv sync + npm install + export the viewer bundle
```

That is the whole of it. `make setup` takes a fresh clone to a state where `make check`
passes, `make viewer` serves the evidence, and `make build` produces `viewer/dist`. No
account and no API key, at any point.

It does need network, once. `uv sync` and `npm install` fetch the Python and JavaScript
dependencies, which is not avoidable for any project. Everything after that — the gate,
the viewer, the build, and re-deriving the comparison — runs offline with no credential,
because its inputs are already committed.

```sh
make check         # lint, typecheck, both suites. No network, no API key.
make check-live    # the published-site checks (the only ones that touch the network)
make viewer        # serve the viewer locally (no network, no API key)
make build         # build viewer/dist: a directory of files, hostable as-is
make walkthrough   # re-record the ~72-second video from the built viewer
```

### The exact rerun command

Re-derive every published figure from the committed run records. No account, no API
credit, no network:

```sh
make matrix ARGS="--from-saved evidence/runs/*.json"
```

This runs the identical arithmetic the live run did, so every number in this README can
be checked against the committed evidence alone.

To capture fresh instead, you need `ELEVENLABS_API_KEY` and about four minutes of audio —
well under a cent:

```sh
make matrix        # all four conditions × three repeats
```

`make check` is the whole gate and needs no credentials. The key is read from
`ELEVENLABS_API_KEY` and is used only by `make fixtures`, `make capture`, and
`make matrix`. Copy `.env.example` to `.env` and export it, or export it directly — the
runner reads the environment and nothing reads the file.

The clips in `fixtures/` are committed because the measurement depends on them:
regenerating them between runs would change the experiment, so `make fixtures` is
deliberately explicit about overwriting them.

## Results

Each condition holds the marker's bytes and sample position identical and varies only how
many speech segments precede it. The zero-preceding-commit condition is the anchor; every
delta below is that condition's median marker timestamp minus the anchor's.

| condition | strategy | preceding commits | marker returned | delta vs anchor |
| --- | --- | --- | --- | --- |
| `vad_0` | VAD | 0 | 12,200 ms | — (anchor) |
| `vad_1` | VAD | 1 | 12,300 ms | **+100 ms** |
| `vad_2` | VAD | 2 | 12,380 ms | **+180 ms** |
| `manual_2` | manual | 2 | 12,200 ms | **+0 ms** |

Three repeats per condition, and all three returned identical timestamps in every
condition, so the spread is 0 ms throughout.

The original report states offsets from the clip's insertion point, which is a different
quantity from a delta between conditions — only the step between conditions is
comparable. Its figures were **+9 ms** with zero preceding commits, **+109 ms** with one,
and **+209 ms** with two, and about **+10 ms** across three manual commits.

### The +9 ms floor, and why this project does not reproduce it

The spec behind this work singles out the report's +9 ms floor as the figure that matters
most: an offset with *no* preceding commit is a constant baseline shift, which is a
different and stronger finding than per-commit drift. So it is worth being explicit that
this rerun does **not** reproduce it, rather than quietly omitting it.

Computed the same way — marker timestamp minus the sample its clip was inserted at — this
project measures **+200 ms** at the zero-commit anchor, not +9 ms. That is a real
disagreement, and it is not resolved here.

It is also not obviously a contradiction, because the quantity is not determined by the
server alone. It contains the clip's own 120 ms of leading silence, plus the word's onset
*within* the clip, which nobody knows precisely. Different fixtures with different padding
put the same server behaviour at different numbers. Which is exactly why this project
measures only the step between conditions: the floor moves with the fixture, and the step
does not.

The honest summary is that the two measurements agree on the *shape* — a per-commit step
under VAD, none under manual commits — and disagree on the absolute offset from an
insertion point. Anyone comparing the two sets of numbers should treat the +9 vs +200
disagreement as open rather than settled by this work.

| condition | claimed step | measured step | difference | verdict |
| --- | --- | --- | --- | --- |
| `vad_1` | +100 ms | +100 ms | +0 ms | reproduces |
| `vad_2` | +200 ms | +180 ms | −20 ms | reproduces |

Returned timestamps land on 20 ms steps across all twelve runs, so a difference within
one step of a claimed step is quantisation rather than disagreement. Both measured steps
are within one step of the reported ones. This 20 ms quantum is an observation from the
raw events, not a documented guarantee, and it is what sets the tolerance for calling a
measurement a match.

**The manual control is what makes this a finding rather than an observation.**
`manual_2` played byte-identical audio to `vad_2` with the same number of preceding
commits, differing only in who chose the commit boundaries — the server's voice activity
detection, or the runner at known samples. Its marker timestamp did not move. Offsets do
not accumulate under manual commits, so the drift seen under VAD is attributable to VAD's
commit triggering rather than to the number of commits.

### Three things earlier runs turned up

All three would have produced confident wrong numbers rather than errors:

- **The realtime API returns word timestamps in _seconds_, not milliseconds.** A marker
  at 12.0 s came back as `12.2`. Storing that in a field named `start_ms` would be wrong
  by 1000× and still look entirely plausible. The unit is now recorded on every run
  record and the raw values stay in the unedited event. Verified by moving the same marker
  to 6 s and getting `6.18` back.
- **`session_started` does not echo `commit_strategy`.** The first version defaulted it
  to `"manual"`, which would have made every VAD run look like a manual one — fabricating
  the experiment's key control. Absence is now recorded as absence.
- **Returned timestamps are quantised to 20 ms.** Every marker timestamp observed across
  twelve runs landed on a 20 ms multiple.

## Simplifications

These bound the claim, and they are the reason it is a controlled experiment rather than a
statement about the platform:

- **One model.** `scribe_v2_realtime`, and nothing else. No claim extends to another model.
- **One language.** English, synthetic speech. No claim extends to any other language.
- **Synthetic audio only.** Generated fixtures with a distinctive nonsense marker word, so
  the marker's expected onset is known to the sample. No customer audio, no microphone
  capture, no real speech.
- **Controlled streaming.** Real-time pacing against a fixed fixture, not production
  throughput. Twelve runs of one word on one day says nothing about behaviour under load.
- **No claim about production prevalence.** No claim about how often this happens, how
  severe it is, or who it affects. Twelve runs of one synthetic word cannot support a
  frequency, severity, or scope claim, and none is made here.

Also deliberately absent: no automatic timestamp correction. Applying a reported offset
blindly would corrupt valid results and destroy the diagnostic's credibility.

## What would falsify this

Stated in advance, so the finding can be closed rather than left open-ended. Any one of
these would count against it:

- **A rerun where the returned timestamps stop landing on 20 ms multiples.** The quantum is
  assumed when deciding that a 20 ms gap between a measured and a claimed step is
  quantisation rather than disagreement. If the timestamps were finer-grained, the
  `vad_2` step would be 180 ms against a claimed 200 ms — a real disagreement.
- **A rerun where the manual control also accumulates.** If offsets appeared under manual
  commits too, the operative variable would be the number of commits rather than VAD's
  triggering, and the conclusion drawn from the control would be wrong.
- **A rerun where the delta's interval includes zero.** The verdict rests on every
  condition's delta excluding zero across its repeats. A repeat that returned the
  anchor's timestamp would put the finding inside its own noise.
- **A condition returning a different commit count than intended.** The preceding-commit
  count is the independent variable. A run that did not actually deliver it is not the
  condition its name claims, and its delta would attribute the wrong cause.
- **A marker that matched something other than the intended word.** The match is exact
  text. A fuzzy or approximate match would make the figure a measurement of a different
  word.
- **The reported figures being measured against a different quantity than this project
  measures.** The report states offsets from an insertion point; this project differences
  one condition against another. If that conversion is wrong, the comparison is wrong.

What would *not* falsify it: a rerun landing one 20 ms step away from these figures. The
returned timestamps cannot express a finer step, so that is quantisation, not
disagreement.

## Next experiment

What this does not yet answer, in the order I would attack it:

1. **Is the step per commit, or per unit of elapsed audio?** Twelve runs fix the marker
   at 12 s and vary the count of preceding segments. Whether the offset tracks the number
   of commits or the total preceding duration is not distinguished, because both vary
   together. Separating them needs a condition with more preceding commits in less time.
2. **Does it compound over a long session?** The original report notes that the automatic
   ~36-second forced commit window adds further commits in long sessions, so error should
   grow in discrete steps rather than as a constant offset. A several-minute fixture with
   a marker at the end would show whether the steps keep coming, and whether the ~100 ms
   figure holds at twenty commits or saturates.
3. **Which parameter, if any, controls it?** The report found the server echoing VAD
   duration values that did not account for the observed step. Sweeping
   `vad_silence_threshold_secs`, `vad_threshold`, `min_silence_duration_ms`, and
   `min_speech_duration_ms` independently would either move the step — locating it — or
   confirm that none of them is the cause, which is itself worth knowing.
4. **Does the SDK path behave the same as the raw WebSocket?** This uses the ElevenLabs
   Python SDK on the grounds that it passes timestamps through unmodified. Re-running one
   condition over raw WebSocket would confirm that on current versions rather than
   inheriting the reporter's conclusion.

Each of these is scoped to the same seam: no API credential is needed to build or test the
fixture, the analysis, or the viewer for any of them.

---

## Design notes

**Marker positions are derived, not declared twice.** The composer reports where each
marker actually landed. A separately maintained marker position is a second source of
truth, and second sources of truth drift.

**Marker matching is exact-text, and fails loudly.** A fuzzy fallback would return a
plausible number for the wrong word. If the marker is absent, the run is an error — not a
zero. A word whose text matches but whose timing is missing is also a miss, for the same
reason: a substituted zero would be a timestamp the server never sent.

**The record's version is pinned, on both sides.** `schema_version` is a literal in the
Python model, and the viewer refuses any value but the one it implements, naming both.
Three declarations of that number are compared by test, so none can move alone.

**The two fields a reader must be told are required, not defaulted.** `match_rule` names
*which word was measured*, and `source_timestamp_unit` is the unit every converted figure
rests on. Both used to carry defaults, and both defaults would have answered a question on
the record's behalf: a record missing `source_timestamp_unit` would be read as `seconds`,
which is exactly the value that makes a wrong reading look right.

**A refusal leaves what was working alone.** The exporter derives everything before it
touches the bundle, and swaps the old one aside until the new one is complete. A corrupt
record names itself and stops the export; it does not cost a reader the eleven runs that
were fine — including when the export is interrupted part way through.

**A capture ends when the marker arrives, not when the first commit does.** Under VAD the
earlier segments commit first, so "a commit came back" is not the same thing as "the
measurement is possible". The wait is bounded by a commit cap and a timeout, and both are
reported as outcomes *distinct* from finding the marker.

**One number per condition is not a measurement.** Every condition keeps all its repeats
and reports the spread. The anchor's own spread is folded into every interval, because a
baseline that wobbles would otherwise manufacture drift in everything measured against it.

**The echoed config is recorded, not the sent values.** The original report found the
server echoing VAD durations that did not account for the observed offset. Sent and echoed
values are therefore not assumed to agree. Where the server says nothing — as it does
about `commit_strategy` — the record says nothing either.

**`logprob` is not a confidence.** The API returns a log-probability: negative and
unbounded below (−1.29 for the marker in synthetic speech). Storing it in a field
constrained to 0..1 would have rejected the real value.

## The viewer

`make viewer` serves a page that plays a saved run and draws what its timestamps claim.
Above the comparison sits the run timeline; three switches — commit strategy, condition,
repeat — move between runs, and the selection lives in `?run=<run_id>` so any one run can
be linked to. A bundle with no zero-preceding-commit anchor supports no comparison, and
the page says so rather than showing an empty table, which would read as "no drift found".

Four things it will not do:

- **Subtract anything.** The deltas on the page are read from `comparison.json`, which
  `scribe_timeline.analysis.compare` wrote; the page has no definition of a delta and
  could not compute one if it tried. Within a single run the marker's two positions are
  printed side by side and left un-subtracted, because one run is not a measurement.
- **Snap the returned word to where its clip was inserted.** The word is drawn where the
  server said it was — 12,380 ms under `vad_2`, not the 12,000 ms the clip was placed at.
  That difference is the observation.
- **Claim a commit boundary it cannot place.** The raw events say where one commit's words
  ended and the next one's began; they do not say where in between the server cut. The
  track draws the whole silence, and says that is all it knows.
- **Convert anything.** The API returned word timestamps in *seconds*; the run record
  converts them to milliseconds once, on ingest, and the commit boundaries are converted
  once more on export, in Python. The page converts nothing, and says so. The unconverted
  values stay in the record's raw `events`.

A run record carries no audio — it carries the *layout* — so `make viewer-export` rebuilds
the audio from the committed clips using the same `compose` the capture used, and refuses
to export a record whose clips no longer match it. Rebuilding from new clips would play
new audio under old timestamps and blame the server for the difference.

**A malformed bundle fails rather than renders.** A record that does not match the schema,
a host that answers for a missing file with its own index page, a WAV that will not load, a
`schema_version` this viewer does not implement — each is reported in place, with the file
named. None of them is rendered as a zero, a dash, or an empty table, because on this page
a substituted value is indistinguishable from a measurement.

The bundle is built with relative asset URLs, so the same directory works at a domain
root, under a repository path, or from a `file://` URL. `tests/test_publication.py` serves
`viewer/dist` from a subpath over HTTP and requests every reference the way a browser
would, because a site-absolute asset path works on a dev server and 404s on the published
site, with every file present and correctly named on disk.

## Layout

- `src/scribe_timeline/audio/` — the composer, the fixture family, speech checks
- `src/scribe_timeline/records.py` — the run-record contract
- `src/scribe_timeline/analysis/` — marker matching, and the comparison that produces
  every published number
- `src/scribe_timeline/capture/` — the capture plan, the network runner, the completion
  rule, and the two entry points (`probe`, `matrix`)
- `fixtures/` — the committed speech clips the measurement depends on
- `evidence/` — the reviewed subset of run records, and the comparison the README quotes
- `schema/run-record.schema.json` — generated from the models; do not hand-edit
- `src/scribe_timeline/viewer/` — rebuilding a run record's audio, and reading each run's
  commit boundaries off the audio clock, in the unit the record itself names
- `viewer/src/runRecord.ts` — the TypeScript projection of that schema, and the version of
  it this viewer implements
- `viewer/src/comparison.ts` — the exported report, parsed. The page reads the deltas; it
  never computes them
- `viewer/src/timeline.ts` — the record as geometry: every position on screen is computed
  here, where the sample/millisecond conversions are tested
- `viewer/src/playback.ts` — the one place a media element's seconds meet the record's
  milliseconds
- `scripts/generate_json_schema.py` — regenerates the schema (`make schema`)
- `scripts/export_viewer.py` — builds the viewer's static bundle (`make viewer-export`)
- `scripts/record_walkthrough.js` — re-records the walkthrough from the built viewer
- `docs/walkthrough.webm` — the 72-second walkthrough

Four test modules guard the promises rather than the code:

- `tests/test_offline_viewing.py` — the whole export and the whole re-derivation, run with
  every socket call made to fail and the key deleted, then with reading the key made fatal.
  This is what backs "free to view" and "re-derivable for nothing".
- `tests/test_static_bundle.py` — the built `viewer/dist`: that it carries the published
  evidence and nothing stale, that its comparison is the published one, and that it loads
  nothing from another origin.
- `tests/test_publication.py` — that the bundle survives being mounted below the domain
  root, that the Pages workflow gates the deploy on `make check` and cannot spend API
  credit, and — under `make check-live`, since it is the one test that touches the
  network — that the published site actually serves its page and everything the page asks
  for. A 404 there fails rather than skipping: that is the state the link must never be
  in.
- `tests/test_report.py` — that this README's figures are the evidence's, that its
  required sections are present, that it states what would falsify the finding, and that
  the rerun command it publishes actually runs.

`make check` fails if the committed schema drifts from the models, and a parity test
asserts the TypeScript parser agrees with the schema on field names, types, optionality,
and enum members for every model on both sides.

## Licence and conduct

This is an independent diagnostic contributed in good faith. It reports what was measured,
credits the original reporter as the originator of the finding, and claims nothing about
the platform beyond that. The reporter supplied the observation; this adds runnable
materials, dated evidence, and a free way to check it.

If the offset does not reproduce, that is reported as the finding. If a maintainer reads
this and finds it wrong, the evidence is in the repository and the rerun command is above.

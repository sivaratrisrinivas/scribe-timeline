# scribe-timeline

A reproducible diagnostic for Scribe Realtime word-timestamp offsets, built in
response to [elevenlabs-python#849](https://github.com/elevenlabs/elevenlabs-python/issues/849).

**Status: the full matrix has been run.** Four conditions × three repeats, measured
against the live API on 2026-10-02. **The reported drift reproduces**, at
approximately 100 ms per preceding VAD commit, and it does not appear under manual
commits. Every figure below comes from `make matrix` and can be re-derived from the
saved run records at zero cost.

### The result

Each condition holds the final marker's bytes and sample position identical and
varies only how many speech segments precede it. The zero-preceding-commit
condition is the anchor; every delta below is that condition's median marker
timestamp minus the anchor's.

| condition | strategy | preceding commits | marker returned | delta vs anchor |
| --- | --- | --- | --- | --- |
| `vad_0` | VAD | 0 | 12,200 ms | — (anchor) |
| `vad_1` | VAD | 1 | 12,300 ms | **+100 ms** |
| `vad_2` | VAD | 2 | 12,380 ms | **+180 ms** |
| `manual_2` | manual | 2 | 12,200 ms | **+0 ms** |

Three repeats per condition, and all three returned identical timestamps in every
condition, so the spread is 0 ms throughout.

Against the [original report](https://github.com/elevenlabs/elevenlabs-python/issues/849)
(+9 / +109 / +209 ms for zero / one / two preceding commits), the measured steps
are **+100 ms** and **+180 ms**. Returned timestamps land on 20 ms steps, so a
one-step difference is quantisation rather than disagreement; both measured steps
are within one step of the reported ones.

**The manual control is what makes this a finding rather than an observation.**
`manual_2` played byte-identical audio to `vad_2` with the same number of preceding
commits, differing only in who chose the commit boundaries — the server's voice
activity detection, or the runner at known samples. Its marker timestamp did not
move. So the offset tracks *VAD's commit triggering*, not the number of commits.

### Three things earlier runs turned up

All three would have produced confident wrong numbers rather than errors:

- **The realtime API returns word timestamps in _seconds_, not milliseconds.** A
  marker at 12.0s came back as `12.2`. Storing that in a field named `start_ms`
  would be wrong by 1000× and still look entirely plausible. The unit is now
  recorded on every run record and the raw values stay in the unedited event.
  Verified by moving the same marker to 6s and getting `6.18` back.
- **`session_started` does not echo `commit_strategy`.** The first version
  defaulted it to `"manual"`, which would have made every VAD run look like a
  manual one — fabricating the experiment's key control. Absence is now recorded
  as absence. Worth confirming with ElevenLabs whether this is documented.
- **Returned timestamps are quantised to 20 ms.** Every marker timestamp observed
  across twelve runs landed on a 20 ms multiple. Not documented by the API, so this
  is an observation from the raw events rather than a stated guarantee — but it is
  what sets the tolerance for calling a measurement a match, since the true step is
  only knowable to within one tick.

## Running it

```sh
make setup         # dependencies, and export the viewer bundle
make check         # lint, typecheck, both suites. No network, no API key.
make viewer        # serve the viewer locally (no network, no API key)
make viewer-export # rebuild the viewer bundle from saved records and clips
make fixtures      # generate the speech clips (needs the key, once)
make capture       # stream one condition and report what came back
make matrix        # run all four conditions x three repeats and compare them
```

`make check` is the whole gate, and it needs no credentials. The key is read from
`ELEVENLABS_API_KEY` and is used only by `make fixtures`, `make capture`, and
`make matrix`.

`make matrix` takes about four minutes and costs well under a cent. To re-derive
the comparison from records already on disk, spending nothing:

```sh
make matrix ARGS="--from-saved runs/*.json"
```

Rebuilding the report from saved records goes through exactly the same arithmetic
as the live run, so a reader can check the published numbers without an account.

The committed clips in `fixtures/` are the ones the measurement depends on:
regenerating them between runs would change the experiment, so `make fixtures`
is deliberately explicit about overwriting them.

## The viewer

`make viewer` serves a page that plays one saved run and draws what its timestamps
claim: the audio, each returned word at the position the server gave it, and the
marker's clip insertion point beside it. Click a word to hear it.

It shows one run, chosen with `?run=<run_id>`. Reading two conditions side by side
is the comparison's job, and a viewer that let a reader flip between single runs
would invite them to treat a pair of them as a measurement.

Three things it will not do:

- **Subtract the marker's two positions.** The gap between the insertion point and
  the returned timestamp is the measurement, and one run cannot produce a
  measurement. The two figures are printed side by side instead, so the arithmetic
  stays the reader's and stays visible.
- **Snap the returned word to where its clip was inserted.** The word is drawn
  where the server said it was — 12,380 ms under `vad_2`, not the 12,000 ms the
  clip was placed at. That difference is the observation.
- **Convert anything.** The API returned word timestamps in *seconds*; the run
  record converts them to milliseconds once, on ingest, and the page says both
  things. The unconverted values stay in the record's raw `events`. Every figure on
  screen is the one in the run record, unchanged since it was written.

A run record carries no audio — it carries the *layout* — so `make viewer-export`
rebuilds the audio from the committed clips using the same `compose` the capture
used, and refuses to export a record whose clips no longer match it. Rebuilding
from new clips would play new audio under old timestamps and blame the server for
the difference.

Everything the page shows comes from files already in the repository. It makes no
request other than for its own bundle, which a test asserts, and a run whose audio
could not be rebuilt is listed with no player rather than with a broken one.

## The question

Issue #849 reports that Scribe v2 Realtime word timestamps drift by roughly
100 ms for every preceding automatic (VAD) commit, while the same audio under
manual commits does not accumulate the offset.

The report is careful but unreproduced, and the obvious way to check it is
circular: you would compare a returned timestamp against the point where audio
was *inserted*. But insertion point is not word onset. A reproduction that
asserts the marker word "should" land at the clip's insertion offset has assumed
its own answer.

So this project measures something that cannot beg the question:

> Hold the final marker's bytes and sample position **identical** across every
> condition. Vary only how many speech segments precede it. Compare **that one
> word's** returned timestamp across conditions.

The zero-preceding-commit condition is the anchor. Every additional preceding
commit's delta *is* the result. No timestamp is ever compared to an insertion
point.

A second control guards the conclusion. The same audio with the same number of
preceding commits is streamed with commits requested explicitly rather than chosen
by VAD. If that run's timestamp moves too, the operative variable is commit count;
if it does not, the variable is VAD's triggering. Without it, "VAD causes drift"
and "commits cause drift" are indistinguishable.

The original reporter is credited as the originator of the finding. This adds
runnable materials and fresh dated evidence.

## How it is put together

There is exactly one seam, at the **run-record boundary**:

| Above the seam | Below the seam |
| --- | --- |
| WebSocket transport | fixture synthesis |
| real-time pacing | offset computation |
| commit triggering | the viewer |
| event capture | every published number |

A **run record** is the seam's currency: one JSON document pairing a *fixture
manifest* (sample rate, layout, every marker's position **in samples**) with the
*raw captured events* and the *server's echoed configuration*.

The consequence is deliberate: everything below the seam is deterministic and
testable offline, at no API cost. The fixture generator has no ElevenLabs
dependency, so the property the whole experiment rests on — a marker landing at
an exactly known sample — is verified without a key.

Run records carry no credentials. Every model forbids extra fields, so a key
cannot be smuggled in as a named attribute, and the free-form event payload is
walked for credential-shaped keys at every depth and rejected outright. The only
credential-named field is a boolean recording that nothing was stored. A redacted
payload would be silently incomplete evidence, so the guard fails loudly instead.

## Setup

```sh
make setup     # uv sync + npm install + export the viewer bundle
```

## Layout

- `src/scribe_timeline/audio/` — the composer, the fixture family, speech checks
- `src/scribe_timeline/records.py` — the run-record contract
- `src/scribe_timeline/analysis/` — marker matching, and the comparison that
  produces every published number
- `src/scribe_timeline/capture/` — the capture plan, the network runner, the
  completion rule, and the two entry points (`probe`, `matrix`)
- `fixtures/` — the committed speech clips the measurement depends on
- `schema/run-record.schema.json` — generated from the models; do not hand-edit
- `src/scribe_timeline/viewer/` — rebuilding a run record's audio, and refusing to
  when the committed clips no longer match what was captured
- `viewer/src/runRecord.ts` — the TypeScript projection of that schema
- `viewer/src/timeline.ts` — the record as geometry: every position on screen is
  computed here, where the sample/millisecond conversions are tested
- `viewer/src/playback.ts` — the one place a media element's seconds meet the
  record's milliseconds
- `scripts/generate_json_schema.py` — regenerates the schema (`make schema`)
- `scripts/export_viewer.py` — builds the viewer's static bundle (`make viewer-export`)

`make check` fails if the committed schema drifts from the models, and a parity
test asserts the TypeScript parser agrees with the schema on field names, types,
optionality, and enum members for every model on both sides — including that no
schema definition escapes the comparison.

## Design notes

**Marker positions are derived, not declared twice.** The composer reports where
each marker actually landed. A separately maintained marker position is a second
source of truth, and second sources of truth drift.

**Marker matching is exact-text, and fails loudly.** A fuzzy fallback would
return a plausible number for the wrong word. If the marker is absent, the run
is an error — not a zero. A word whose text matches but whose timing is missing
is also a miss, for the same reason: a substituted zero would be a timestamp the
server never sent.

**A capture ends when the marker arrives, not when the first commit does.** Under
VAD the earlier segments commit first, so "a commit came back" is not the same
thing as "the measurement is possible". The wait is bounded by a commit cap and a
timeout, and both are reported as outcomes *distinct* from finding the marker — a
capture that ended any other way is incomplete evidence and fails rather than
being written out.

**The number of commits is recorded, not assumed.** The preceding-commit count is
the experiment's independent variable, so each run's observed commit count is
reported next to the count the fixture intended. A run that returned a different
number is not the condition its name claims.

**One number per condition is not a measurement.** Every condition keeps all its
repeats and reports the spread across them. The anchor's own spread is folded into
every interval, because a baseline that wobbles would otherwise manufacture drift
in everything measured against it and blame the commits that followed.

**The manual control states its conclusion.** The control exists to answer one
question — do offsets accumulate without VAD? — so the report says so outright
rather than leaving it to be inferred from a table row.

**Earlier segments are unmarked.** They exist to induce a VAD commit; nothing
needs to match them. Marking them would put one word at two placements, and
exact matching cannot tell those apart.

**The echoed config is recorded, not the sent values.** The original report found
the server echoing VAD durations that did not account for the observed offset.
Sent and echoed values are therefore not assumed to agree. Where the server says
nothing — as it does about `commit_strategy` — the record says nothing either.

**The timestamp unit is recorded, not assumed.** The API returns seconds. The
record carries `source_timestamp_unit` so the conversion to milliseconds is always
checkable against the raw event.

**`logprob` is not a confidence.** The API returns a log-probability: negative and
unbounded below (−1.29 for the marker in synthetic speech). Storing it in a field
constrained to 0..1 would have rejected the real value.

## What is deliberately absent

No uploads, no microphone capture, no languages beyond English, no models beyond
Scribe v2 Realtime, and — importantly — **no automatic timestamp correction**.
Applying a reported offset blindly would corrupt valid results and destroy the
diagnostic's credibility.

No claim is made about customer impact, internal priorities, or how often this
behaviour occurs in production. Twelve runs of one synthetic word, on one model, on
one day: a controlled experiment establishing that the effect exists and pointing
at its cause. It is not a measurement of frequency, severity, or scope.

## Licence and conduct

This is an independent diagnostic contributed in good faith. It reports what was
measured, credits the original reporter, and claims nothing about the platform
beyond that. If the offset does not reproduce, that is reported as the finding.

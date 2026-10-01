# scribe-timeline

A reproducible diagnostic for Scribe Realtime word-timestamp offsets, built in
response to [elevenlabs-python#849](https://github.com/elevenlabs/elevenlabs-python/issues/849).

**Status: one condition captured.** The pipeline runs end to end against the live
API and the anchor condition has been recorded. **No claim is made about whether
the reported offset reproduces** — that needs the same word compared across
conditions, which is the next ticket.

### First result, and two things it turned up

The anchor condition (`vad_0`: no preceding speech segments, marker at 12.0s)
returns the marker at **12,200 ms**. That is coherent, not a finding: each clip is
padded with 120 ms of leading silence, so the word onset lands ~200 ms after the
clip's insertion point. It is a baseline, not a measurement.

Two things the first live run exposed, both of which would have produced confident
wrong numbers:

- **The realtime API returns word timestamps in _seconds_, not milliseconds.** A
  marker at 12.0s came back as `12.2`. Storing that in a field named `start_ms`
  would be wrong by 1000× and still look entirely plausible. The unit is now
  recorded on every run record and the raw values stay in the unedited event.
  Verified by moving the same marker to 6s and getting `6.18` back.
- **`session_started` does not echo `commit_strategy`.** The first version
  defaulted it to `"manual"`, which would have made every VAD run look like a
  manual one — fabricating the experiment's key control. Absence is now recorded
  as absence.

## Running it

```sh
make setup      # dependencies
make check      # lint, typecheck, both suites. No network, no API key.
make fixtures   # generate the speech clips (needs the key, once)
make capture    # stream one condition and report what came back
```

`make check` is the whole gate, and it needs no credentials. The key is read from
`ELEVENLABS_API_KEY` and is used only by `make fixtures` and `make capture`.

The committed clips in `fixtures/` are the ones the measurement depends on:
regenerating them between runs would change the experiment, so `make fixtures`
is deliberately explicit about overwriting them.

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

The original reporter is credited as the originator of the finding. This adds
runnable materials and, eventually, fresh dated evidence.

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
make setup     # uv sync + npm install
```

## Layout

- `src/scribe_timeline/audio/` — the composer, the fixture family, speech checks
- `src/scribe_timeline/records.py` — the run-record contract
- `src/scribe_timeline/analysis/` — marker matching over a returned transcript
- `src/scribe_timeline/capture/` — the capture plan, the network runner, the probe
- `fixtures/` — the committed speech clips the measurement depends on
- `schema/run-record.schema.json` — generated from the models; do not hand-edit
- `viewer/src/runRecord.ts` — the TypeScript projection of that schema
- `scripts/generate_json_schema.py` — regenerates the schema (`make schema`)

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
is an error — not a zero.

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
behaviour occurs in production. It is one controlled experiment.

## Licence and conduct

This is an independent diagnostic contributed in good faith. It reports what was
measured, credits the original reporter, and claims nothing about the platform
beyond that. If the offset does not reproduce, that is reported as the finding.

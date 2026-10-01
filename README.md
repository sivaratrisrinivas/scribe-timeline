# scribe-timeline

A reproducible diagnostic for Scribe Realtime word-timestamp offsets, built in
response to [elevenlabs-python#849](https://github.com/elevenlabs/elevenlabs-python/issues/849).

**Status: foundation only.** The run-record contract and the audio fixture
generator are built and tested. Nothing has been streamed to the Scribe API yet,
so this project currently makes **no claim about whether the reported offset
reproduces.** That question is the next ticket.

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
make check     # lint, typecheck, and both test suites
```

`make check` is the whole gate. It also verifies the committed JSON Schema still
matches the Python models that produce run records.

## Layout

- `src/scribe_timeline/audio/` — the composer and the fixture family
- `src/scribe_timeline/records.py` — the run-record contract
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
Sent and echoed values are therefore not assumed to agree.

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

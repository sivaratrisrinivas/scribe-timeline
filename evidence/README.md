# Published evidence

The run records and the comparison behind the numbers in the top-level README.

Everything here is committed deliberately, and every file was checked for
credential-shaped content before it was copied in. `runs/` (the live capture
output) is gitignored; this directory is the reviewed subset.

## What is here

- `runs/` — twelve run records: four conditions × three repeats, captured
  2026-10-01/02 against `scribe_v2_realtime`. Each pairs the fixture manifest with
  the raw events and the server's echoed configuration.
- `comparison.json` — the machine-readable comparison, exactly as
  `make matrix` wrote it.

## Re-deriving the comparison

No API key and no network access:

```sh
make matrix ARGS="--from-saved evidence/runs/*.json"
```

This runs the identical arithmetic the live run did. The published figures in the
README can therefore be checked from the committed evidence alone.

## The conditions

| condition | strategy | preceding commits | clip before the marker |
| --- | --- | --- | --- |
| `vad_0` | VAD | 0 | none |
| `vad_1` | VAD | 1 | `earlier` at 0.0 s |
| `vad_2` | VAD | 2 | `earlier` at 0.0 s and 6.0 s |
| `manual_2` | manual | 2 | identical audio to `vad_2` |

The marker clip sits at 12.0 s in every condition, byte for byte. `manual_2` is
the control: same audio, same number of preceding commits, but the runner asked
for the commit boundaries at known samples instead of letting the server's voice
activity detection choose them.

## Notes on reading these files

- **`api_key_present: true`** records that a credential was in the environment
  when the run happened. It is a boolean. The key itself is never stored — see the
  credential section of the top-level README, and the end-to-end check for it in
  `tests/test_offline_viewing.py`.
- **`schema_version: 1`** is a literal in the model, not a free integer. A record
  declaring any other version is refused rather than read under the wrong rules.
- **Word timestamps in `events` are in seconds.** The API returns seconds despite
  the field names; `words[].start_ms` in the same file is the converted value, and
  `source_timestamp_unit` says so on every record. The raw values stay in `events`
  so the conversion is always checkable.
- **`echoed_config.commit_strategy` is `null`.** The server does not echo it.
  `commit_strategy` at the top level is what was requested.
- **`Kolvig` in some transcripts** is the server's rendering of the fixture's
  earlier word (`Kalvik`). It is not the marker and nothing measures it; only
  `Zorblax` is matched, exactly.
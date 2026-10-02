# Outreach copy

Drafted for [#8](https://github.com/sivaratrisrinivas/scribe-timeline/issues/8). **Not
sent** — sending is a decision about someone's platform account, not an engineering one.

Every figure below is checked against `evidence/comparison.json` by
`tests/test_outreach.py`, which fails if a draft quotes a number the evidence does not
hold. If you edit a draft, re-run `make check`.

One link, used in all three:

**<https://sivaratrisrinivas.github.io/scribe-timeline/>**

Verify it in a fresh, logged-out browser window before sending. Not incognito — a real
private window. If it needs a login, or the asset paths 404 because the site is mounted
under `/scribe-timeline/`, stop and fix that first.

---

## Rules these drafts follow

They are rules about what may be claimed, and they come from the report's own
simplifications section. Breaking one of them is worse than not sending.

1. **Credit first.** [@wujin941005](https://github.com/wujin941005) filed the finding in
   [elevenlabs-python#849](https://github.com/elevenlabs/elevenlabs-python/issues/849) on
   2026-08-19. This is an independent rerun. Never lead with your own numbers.
2. **Only measured figures.** The deltas are +100 ms and +180 ms, three repeats each,
   every repeat identical, manual control +0 ms. Nothing else is measured.
3. **No prevalence claim.** Never "affects many users", "in production", "a known
   problem". Twelve runs of one synthetic word support none of that, and a single such
   sentence discredits the measured part.
4. **No accusation.** This is an experiment that found something, not a complaint about
   a company. "Worth a look" is the register; "bug report ignored" is not.
5. **The invitation is to check, not to agree.** The whole point is that a reader can
   rerun it.

---

## DM — for the original reporter

The most important audience. They did the work; this is an independent reproduction with
runnable materials, and they should hear about it from the person who built on it.

> Hey — I reran the timestamp-drift experiment you filed in
> [elevenlabs-python#849](https://github.com/elevenlabs/elevenlabs-python/issues/849) as
> an independent diagnostic, and reproduced it. Your finding is the one that made sense
> of it; this just adds runnable materials and dated evidence.
>
> Measured 2026-10-02, four conditions × three repeats: the marker word's returned
> timestamp moved **+100 ms** with one preceding VAD commit and **+180 ms** with two,
> against your reported +109 and +209. Returned timestamps land on 20 ms steps, so both
> are within one step of yours. The +180 is the interesting bit — dividing it by two
> *suggests* the step is nearer 90 ms than 100, though that is my arithmetic on two
> measurements rather than something the run measures directly.
>
> The part I'd flag as yours: `manual_2` played byte-identical audio to `vad_2` with the
> same commit count and came back at **+0 ms**, so the offset tracks VAD's triggering
> rather than the commit count. That control is what makes it a finding rather than an
> observation.
>
> Everything is committed and the rerun costs nothing:
> <https://sivaratrisrinivas.github.io/scribe-timeline/>
>
> If that reading of the per-commit step is wrong, I'd rather hear it from you than
> publish it.

Note on the ~90 ms figure: that is arithmetic on two measurements (180 ms over two
commits), stated as an inference and not as a result — and the draft says so inline, so
the number cannot be separated from its hedge by a reader skimming. The evidence measures
+100 and +180; it does not measure a per-commit step directly. If you would rather not
risk it, cut that clause — the rest stands without it.

## X — the post

Under the character limit, one claim, one link. No thread.

> @elevenlabs Scribe v2 Realtime: an independent rerun of the VAD word-timestamp drift
> reported in elevenlabs-python#849 (@wujin941005). It reproduces.
>
> One marker word, identical bytes and position, only the preceding commits vary:
> +100 ms at one, +180 ms at two. Same audio under manual commits: +0 ms.
>
> 12 runs, every repeat identical. Raw events committed; rerun costs nothing:
> https://sivaratrisrinivas.github.io/scribe-timeline/

## X — the alternative, if the first reads as a complaint

Softer, and drops the numbers to a single figure. Use one or the other, never both.

> Independent check of a reported Scribe Realtime behaviour change
> (elevenlabs-python#849, credited to @wujin941005).
>
> Same marker word, same sample position, varying only how many commits precede it. The
> returned timestamp moves under VAD and does not move under manual commits.
>
> Evidence and rerun command, no account needed:
> https://sivaratrisrinivas.github.io/scribe-timeline/

## Comment — for the issue thread, if you decide to post there

The spec lists filing upstream as out of scope, so this is drafted and **not posted**.
Decide separately whether to post at all; posting to someone's issue tracker is visible
to their employer and their customers.

> Independent reproduction attempt for [elevenlabs-python#849](https://github.com/elevenlabs/elevenlabs-python/issues/849),
> for anyone who wants to check it.
>
> I reproduced the drift described here and built a viewer for the captured runs.
> Crediting @wujin941005 as the originator — this adds runnable materials and dated
> evidence, not a new observation.
>
> Measured 2026-10-02, `scribe_v2_realtime`, synthetic audio, real-time pacing, four
> conditions × three repeats:
>
> | condition | strategy | preceding commits | marker returned | delta vs anchor |
> | --- | --- | --- | --- | --- |
> | `vad_0` | VAD | 0 | 12,200 ms | — (anchor) |
> | `vad_1` | VAD | 1 | 12,300 ms | +100 ms |
> | `vad_2` | VAD | 2 | 12,380 ms | +180 ms |
> | `manual_2` | manual | 2 | 12,200 ms | +0 ms |
>
> The reported offsets (+9 / +109 / +209 ms) are stated against the clip's insertion
> point, which is a different quantity from a delta between conditions; only the step is
> comparable. Measured steps are +100 and +180 against claimed +100 and +200, and
> returned timestamps land on 20 ms multiples across all twelve runs, so both are within
> one quantisation step.
>
> The control is the part I'd point at: `manual_2` is byte-identical audio to `vad_2`
> with the same number of preceding commits, differing only in who chose the cut points.
> It did not move.
>
> Raw events committed, rerun needs no account:
> <https://sivaratrisrinivas.github.io/scribe-timeline/>
>
> Simplifications: one model, one language, synthetic audio, controlled streaming. No
> claim about production prevalence — twelve runs of one synthetic word cannot support
> one, and I am not making it.

---

## Before sending

- [ ] The link opens in a fresh private window, with no login, and the page draws its
      comparison table rather than a blank page.
- [ ] `make check` passes.
- [ ] The first sentence credits the reporter.
- [ ] No draft claims frequency, severity, customer impact, or production behaviour.
- [ ] Nothing says "bug", "broken", or "ignored" about a maintainer.

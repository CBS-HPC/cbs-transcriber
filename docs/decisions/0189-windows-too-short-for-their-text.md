# 0189 — Passage timing windows too short for their text are not aligned

Accepted 20 November 2026. Amends ADR 0056 and ADR 0058.

"Repair all" for 60 untimed passages of a 2,202-passage UCloud Transcriber (large-v3) result
failed after six units, and a single passage spent three minutes loading models before it
finished without timing. Every one of those passages had its own saved interval of 1.0 s
(one of 0.28 s) for 9–23 words: 51–101 characters. These were also the passages the
transcriber itself had not timed.

The pinned aligners (Danish xls-r-300m and English base-960h, both wav2vec2) emit one frame
per 20 ms (320 samples at 16 kHz over a 400-sample receptive field): 49 frames in 1.0 s.
WhisperX's CTC alignment places at most one character per frame, counting interior spaces.
With more characters than frames its backtrack always fails and it returns no words, so the
local job ran a pointless alignment and reported nothing.

## Decision

- **Alignable windows only.** A fresh "Add word timing" or "Repair all" request aligns a
  window only if its characters fit its aligner frames: `len(" ".join(text.split()))` at most
  `floor((floor(seconds × 16000) + 1 − 400) / 320) + 1`. The extra sample keeps the bound
  generous; the rule only refuses windows that can never align. It applies to a passage's own
  interval and to the ADR 0058 search window between timed neighbours alike.
- **The window is not widened.** A passage's own bounds stay its search window (ADR 0056/0058);
  the neighbours' word gap was no wider in the observed result. No timing is invented.
- **Truthful outcomes.** One passage is refused with its window length and character count.
  Repair all leaves such passages out and says so; if nothing remains, the refusal names the
  cause instead of offering the whole-result enhancement, which aligns bounded passages in the
  same intervals. The editor mirrors the rule: such a passage's tag reads "Too short to time"
  (inactive, with the reason), and the bulk tag counts them separately.
- **Frozen jobs.** Units frozen before this decision (or retried) whose window cannot align are
  skipped without starting a worker or decoding audio. Their passages stay unchanged, and the
  job ends with "Complete word timing for N of M passages. K of M passages were not aligned…"
  or, when every unit was skipped, a message naming the short windows. The re-derivation checks
  of frozen units (routing, UCloud repair bundles) keep the earlier rule, so their identities
  are unchanged.
- **Activity stage.** The worker reports "Aligning words…" once the aligner is ready, before the
  first window, not only after it. The CBS Runtime rule that suppresses a repeated "loading"
  stage for cached models is unchanged.

## Consequences

A result whose saved intervals are much shorter than their speech cannot be repaired by passage
timing; correcting those intervals (or a new transcription) is needed first. The 20 ms rule is
tied to the pinned aligners; a future aligner with another frame rate must restate it. The
"Repair all" failure itself was a separate Windows file race (B468), fixed independently.

## Verification clarifications — 20 November 2026

Character counts use Unicode code points after Python `str.split` whitespace joining.
The browser must mirror that exact whitespace set, including NEL and U+001C–U+001F,
while retaining BOM as literal text (B487). This normalization is only an admission
calculation; saved passage text is unchanged.

Windows advisory progress may retain its pending file if a reader also holds that
name. Only that PermissionError is suppressed; required output errors still fail
normally. A real held-reader regression proves both behaviors (B481/V242).

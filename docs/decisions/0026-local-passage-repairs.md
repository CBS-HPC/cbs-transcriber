# 0026 — Local passage repairs and a reusable alignment worker

Accepted: 21 October 2026. Extends 0016/0017/0025; whole-result and UCloud jobs keep their
existing paths. The user requested a small action beside missing-word-timing warnings and
a repair-all action beside the missing-passage count.

Scoped enhancement freezes the selected saved result and ordered passage IDs. Accept only
local alignment with cached models: no speaker detection or implicit downloads. Require actual
positive passage intervals, each at most 60 seconds / 120 words / 1200 characters. Untimed or
larger passages use full-result enhancement; do not invent bounds from text length.

FFmpeg decodes only the selected intervals. Requests/checkpoints remain clip relative; restore
recording offsets once before validating and merging. Keep per-passage artifacts for retry.
Append one combined result when the operation completes, preserving every other passage's
text, words, timings, edits, originals and speaker labels. Retain source selection until the
user chooses Review repair. Comparison actions identify their own saved result.

One enhancement queue owns one stdin-controlled subprocess for bounded jobs. Retain one
alignment model, keyed by interpreter/language/device, for five idle minutes. Identity change,
cancellation, failure, idle expiry or shutdown closes its process tree. Requests are sequential;
whole-result jobs continue using independent subprocesses. No HTTP listener, setup token,
implicit network access or automatic model download is introduced.

Review resolved: an unknown passage field must not become whole-recording work; validate offsets
after retry, preserve unselected restoration baselines and never silently replace reviewed text.
Tests cover selected windows, immutable sources, idempotence, UI review/playback, reuse,
configuration changes, idle expiry, cancellation and shutdown. Real accuracy remains model
dependent; the first CPU repair can still have substantial startup cost.

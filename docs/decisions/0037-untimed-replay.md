# 0037 — Listening without passage timestamps

Accepted: 22 October 2026. Extends 0030.

The fallback button label is amended to **Replay** by [0039](0039-fixed-compute-model-settings.md);
the explicit tooltip and full-recording playback behavior remain unchanged.

Word timestamps are unnecessary for replay of a bounded passage. An unbounded passage,
however, cannot identify its own audio interval. Some partial enhancements from untimed
transcriptions contain such fragments, with no bounded frozen source to recover.

Keep the existing priority: actual passage bounds, then the exact verified frozen source
context described in 0030. If both are absent and the recording has a finite positive
duration, offer **Replay recording**, from zero to that duration. Its tooltip explicitly
describes whole-recording playback. The button does not pretend to isolate a sentence.
If playback is not prepared and no duration exists, keep the action disabled.

This is a view-only listening fallback. Do not populate replay_windows, interpolate times,
highlight the untimed passage, enable bounded passage repair, or alter any transcript/result
version, original, snapshot or export. Normally timed passages and frozen-context replay
retain their existing behavior. Browser checks cover untimed/partial results, real playback,
unchanged stored intervals and retained exact-source replay.

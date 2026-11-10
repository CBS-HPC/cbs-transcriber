# 0114 — Evaluate individually valid saved word timings

Accepted 30 October 2026. This changes evaluation projection only; it never edits transcript
text, word timing, speaker labels or saved history.

A partially timed current word map can contain usable words beside invalid or untimed words.
Treating the whole passage as untimed undercounts coverage; passing every raw interval to a
strict scorer can instead abort scoring or count timing outside the owning passage.

When converting a saved result, retain an interval only for an exact current literal word
with finite, non-boolean, non-negative, positive and monotonic bounds within its known owning
passage. Invalid timing becomes null for that word, preserving its text, speaker and valid
neighbors. An edited stale map remains unavailable. Direct explicit researcher annotations
retain their existing strict validation rather than silently repairing supplied timings.

Evaluation of fresh CBS Runtime jobs also validates the actual accepted original-input
admission envelope and frozen identity; legacy receipts retain their own existing checks.
Synthetic tests cover normalized partial maps, out-of-passage and zero-duration words,
ordering and strict annotation behavior. They do not establish real timing accuracy.

# 0047 — Automatic local or UCloud enhancement

Accepted 22 October 2026. Extends 0023 and 0046.

After an OpenAI transcription, missing word timing or speaker labels are usually too slow to
produce on a researcher's CPU for full interviews. Very short recordings are faster locally,
because a UCloud job adds minutes of scheduling, transfer and runtime checks. The user asked the
app to coordinate this.

`execution: "auto"` is the dialog default. It is resolved per recording when work is queued, and
the frozen job always records a concrete `local` or `ucloud` place plus `routing.reason`. Nothing
moves between computers later. Activity shows the reason.

Rule (`routing.py`, heuristics to revisit with measured benchmarks):

1. Local limit: 10 audio minutes for word timing only, 3 minutes when speaker labels are
   requested, ×6 when the local device is CUDA.
2. Within the limit: this computer if its WhisperX runtime is ready; otherwise UCloud if usable.
3. Beyond the limit: UCloud if a Provider Settings connection is approved for the project and
   its runtime is prepared (read-only stamp check) or preparation was explicitly requested;
   otherwise this computer, labelled slow, with the UCloud reason.
4. If neither place is usable, the request fails before any job is created.

Passage repairs always run locally (one bounded window per passage); explicit UCloud repair
requests are rejected. Explicit `local` or `ucloud` choices remain available and unchanged.
Protected approval, frozen-source and no-overwrite rules are those of the chosen place.

Limits: thresholds are estimates, not measurements on CBS hardware; the reviewed benchmark work
(T19) should calibrate them. The UCloud check adds a few read-only API calls at queue time.

## Amendment, 23 October 2026: researcher rules (T68)

The limits above are now defaults. Each library stores its own rules (settings row 5): word
timing minutes and speaker minutes (0–360), the CUDA factor (1–20) and the fallback for a
longer recording when UCloud is unusable: `local` (run here, labelled slow; the default) or
`stop` (refuse before any job is created, naming the reason). The rules are read once per
request; each frozen routing reason states the limit that applied. Explicit Local/UCloud choices
and passage repairs are unaffected.

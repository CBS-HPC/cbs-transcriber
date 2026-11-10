# 0030 — Partial alignment and replay context

Accepted: 21 October 2026. Extends 0003, 0016 and 0026.

An alignment can locate some words while leaving others uncertain. Previously, splitting a
bounded sentence into timed and untimed fragments removed replay from the latter. Retain a
partially aligned passage's actual source bounds and text instead. Keep `word_timing=false`
as the completeness flag and continue marking the passage as incomplete. Playback may highlight
individually valid words; missing, zero-duration, unordered and out-of-passage intervals remain
unhighlighted. Free-text rewrites disable all old word playback until restoration. Timed word
correction still requires complete timing. Fully timed speaker splitting is unchanged.

An originally unbounded passage cannot acquire bounds merely from partial word coverage.
Its reliable runs still split from unlocated gaps. No interpolation or estimated word times
are introduced.

Existing saved fragment results remain immutable. Result/recording API reads may expose a
separate `replay_windows` map from fragment IDs to actual frozen source passage bounds. Require
an owned enhancement job, matching frozen source identity, and an exact ordered partition of
the frozen passage text against fragment restoration baselines. Current source edits do not
alter that evidence. Uncertain mappings expose no window. Label this action **Replay source
passage**: it replays the whole original passage, not an inferred fragment interval. The map
does not change stored segment timestamps, word intervals, snapshots or exports.

Enhancement completion keeps the reviewed result selected, as required by 0007. Show a direct
**Review enhanced result** action when a child result is available. Completion while editing
must be noticed after the draft settles without discarding the draft or changing media state.

Single-file and Transcribe selected controls now expose the same native/optional feature
choices as the existing batch dialog. Native capabilities are checked and locked; selecting
missing features opens the existing Local/UCloud processing settings before submission. The
durable atomic ASR-to-enhancement workflow from 0023 remains authoritative.

Regression coverage includes partial bounded replay, word highlighting and restoration in both
panes, legacy frozen-context matching without writes, deferred result discovery, and single/bulk
feature chaining with mocked services. No model inference is required by these tests.

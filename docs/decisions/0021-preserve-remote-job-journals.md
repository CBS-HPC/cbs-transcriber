# 0021 — Preserve unresolved remote job journals

Accepted 20 October 2026 following the implementation audit.

## Context
Deleting a recording cascades its jobs. Local failure/interruption does not prove
that an owned UCloud allocation has stopped. An uncertain submission can also lack
its remote ID. Losing that journal removes the local recovery and stop path.

## Decision
Block recording/project deletion while a remote journal is unresolved, independently
of local queue state. Persist observed terminal states and acknowledged stop requests.
Allow explicit reconciliation of uncertain submissions: attach a remote ID only after
read-only retrieval verifies the exact unique local-run name, or record the user's
explicit confirmation that inspection found no allocation. Neither action submits work.
Retain original intent and the reconciliation receipt in SQLite. A fresh submission
after explicit retry resets that receipt before its POST, protecting subsequent failures.

## Consequences
Cancel without a known ID cannot imply that an uncertain allocation was stopped.
Users can inspect the named job, reconnect and stop it without restoring upload approval.
Deletion stays local and does not silently contact or terminate UCloud jobs. Existing
successful, observed terminal and acknowledged-stop jobs do not block deletion.
Unresolved/active work remains visible in Activity alongside the most recent 200 jobs.

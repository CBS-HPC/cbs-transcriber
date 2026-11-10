# 0162 — Recover transient native output reads on the recorded allocation

Accepted 10 November 2026. Extends ADRs 0045, 0149, 0152 and 0155. The user's terminal
twelve-file allocation produced all twelve JSON files, but one member failed before local
raw capture; the other eleven results were saved. A later read and the normal parser accepted
that exact remaining output. The original generic exception did not preserve its subtype or
HTTP status, so its specific cause remains unknown.

Retry only output listing pages, download-session creation and GET of the selected output.
Use at most three attempts, with cancellable two/five-second backoff, for 502/503/504 or
timeout/network/remote-protocol failures. Repeat the same read against the recorded allocation;
never reuse partial transfer bytes. Authentication, missing output, malformed selection/JSON,
size violations, Stop and source/approval guard errors do not enter this automatic retry path.

Preserve all existing fresh guards, exact candidate matching, separate transfer credentials,
byte limits, producer idempotency, raw preservation and accepted-request drain. No submission,
upload, cleanup or missing-output resubmission gains retry authority.

The native batch journal may retain `output_read_errors`: at most 64 current member IDs with
only allowlisted `stage`, nullable integer `http_status` and attempt count 1..3. This is an
observation, not a control or capture receipt. Reject arbitrary fields/text/foreign identities.
Project a curated message only for a failed member without a saved result; successful capture
clears its own diagnostic. Retain normal batch state and ownership after errors.
If a later attempt reaches parsing/validation, clear its old read diagnostic so that a stale
HTTP failure cannot hide the current import problem; preserve other members' observations.
Optional early-publication listing captures guard exceptions explicitly before soft read-failure
handling, so a one-time source/approval denial cannot be swallowed by a later successful check.

The failed native batch's button is labelled **Retry failed**. It reconnects to the existing
allocation and preserves completed members. Genuinely missing output still requires a later
explicitly confirmed new-run design, with source/approval admission, failed-only membership,
successor lineage and separate old-folder disposition. Do not turn an unavailable read or
uncertain submission into automatic ASR resubmission. Cleanup remains receipt-checked and
terminal-only; neither Retry nor this diagnostic map permits purging UCloud Trash.

Synthetic tests cover transient reads, exhaustion, partial buffers, nonretryable errors,
Stop/guard propagation, hostile diagnostics and recovery of one member while retaining eleven
exact saved results without a new allocation or upload. Real loaded-build recovery acceptance
and the broader directory-cleanup review remain separate.

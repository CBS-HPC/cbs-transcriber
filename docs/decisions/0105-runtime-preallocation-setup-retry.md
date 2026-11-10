# 0105 — Explicit retry of proven pre-allocation Runtime setup failure

Accepted 30 October 2026 after a five-file batch failed uploading its first public setup
script. The saved owner proved no allocation POST or private input publication; the old
recovery path required a remote job ID and exposed only Stop.

Offer **Retry setup upload** only for a failed/interrupted owner with exact no-post proof:
no job ID, submission intent/attempt, uncertainty, reserved capacity, Stop/close/discard/release
or reconciliation evidence; exactly initial attempts with actual empty dictionary receipts
and no saved output/result. An initial interrupted attempt may qualify after startup recovery
only under the same proof. Any present output receipt or malformed/falsy non-object receipt
refuses this path.

An explicit Retry checks current code/source identity and Protected approval, then one
BEGIN IMMEDIATE/CAS transaction rechecks eligibility, increments the coordinator epoch and
queues the same owner/member jobs. Frozen params, identity, source identity, task IDs and
attempt counters are retained exactly. Normal claim reacquires capacity; Retry itself makes
no UCloud request. Accepted or uncertain submissions continue using their existing controls,
never this reset. This is not automatic resubmission after a lost POST.

Setup failures retain only allowlisted public filenames, safe stage/code and observation
time. Do not persist arbitrary exception URLs, token bodies or websocket payloads. The old
message cannot reconstruct whether a past failure was a reset, timeout or closed connection.

Five-member coordinator, restart, stale/concurrent CAS, malformed receipt, source, approval,
capacity and existing no-second-POST/control checks are synthetic. The live failed batch was
inspected read-only; no Retry, upload or allocation was performed during implementation.

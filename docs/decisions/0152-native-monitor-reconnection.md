# 0152 — Reconnect native monitoring after transient failures

Accepted 9 November 2026 after a real status poll returned HTTP502 while its three-file
allocation remained RUNNING. Implemented with mocked polling, real local queue shutdown and
synthetic library-reopen verification. No live UCloud acceptance run was started for this change.

Once the remote job ID is durably recorded, retry only retrieve_job reads on gateway
502/503/504 and transport timeout/network/protocol errors. Use cancellable waits of 2, 5, 10
and then at most 30 seconds within the existing monitoring deadline; cap each read timeout
by that remaining budget. A successful read resets the backoff and restores canonical stages.

Keep the local batch running with a clear connection/reconnecting message during the outage.
No retry may resubmit the allocation, rerun a transcription, repeat an uncertain mutation or
terminate the remote job merely because monitoring failed. Authentication refusals remain
explicit errors; 429 is not retried without reliable Retry-After metadata in the released SDK.

At deadline exhaustion, emit a typed MonitoringInterrupted with an honest may-still-be-running
message. Preserve that state through the queue finalizer, the remote ID, frozen settings and
all transfer/capture receipts. Existing Retry reconnects to the same allocation. Explicit Stop
and shutdown retain their distinct existing termination/preservation behavior.

The MVP handoff is explicit: close after acceptance, then relaunch and use Retry on that card
to retrieve the same remote job. A broader cleanup dashboard and automatic recovery of every
historical job on startup are deferred; a local connection error is never disposal consent.

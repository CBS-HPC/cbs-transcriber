# 0149 — Bounded parallel native batch transfers

Accepted 9 November 2026. Implemented and verified with mocked transfers, cancellation,
capture and recovery. Real-provider throughput and large-batch resource acceptance remain open.

Upload and transcript download may overlap across up to four files in one native batch.
Keep remote GPU submission after every input has uploaded and verified. This changes transfer
scheduling only, not frozen model, allocation, source, consent or output-selection identities.

Each network task owns a separate UCloud control client and transient transfer session.
Do not share a client's mutable authentication/session state across threads. Workers report
only their own file's progress and return verified transfer outcomes. The coordinator alone
merges uploaded IDs/hashes and publishes capture evidence and locally committed results.

Admit at most four outstanding downloads so completed response bytes remain bounded while
the coordinator saves results. Existing raw output and confirmed upload receipts remain
retry skips. Stop admitting on error/cancellation; drain every accepted task while retaining
the serving library lease, preserve successful receipts/raw bytes, then propagate the outcome.
No failed upload path submits a GPU job; no retry silently creates another allocation.

Keep ownership, credential scopes, endpoint checks, exact output matching, lost-reply capture
and confirmed Trash cleanup. File labels distinguish waiting, transferring, transferred,
saving and complete; a finished upload does not remain displayed as an active transfer.

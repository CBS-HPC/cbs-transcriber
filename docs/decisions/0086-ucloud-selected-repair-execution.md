# 0086 — UCloud execution of selected repair bundles

Accepted 28 October 2026 for a bounded T90 slice. One fixed-language align-only bundle uses
one separately frozen MIG singleton through the existing API connection, preparation/readiness,
approval and owned-job lifecycle. No SSH, remote credentials, implicit downloads or full-original
upload. Existing local single-passage behavior and frozen jobs remain supported.

At most 200 validated clip units carry exact input hashes and source/text mappings. Remote
execution records immutable per-unit acceptance/start/output receipts and reuses a cached
aligner. Uncertain or failed inference is not automatically replayed; explicit new requests
may target unresolved units without overwriting an earlier allocation's identity.

Stop/failure/shutdown recovery harvests and durably preserves available unit outputs before
any owned-folder Trash. Unresolved accepted/output obligations retain their folder. Publish
one final realign_output after terminal harvest, reporting completed/unresolved/uncertain
counts honestly. The existing revision/text/bounds-guarded editor remains the only timing
writer; terminal failed/cancelled jobs may apply validated partial output through a narrowly
scoped new-protocol exception, retaining their honest job status and every unselected passage.

Automatic routing requires selected-clip workload, approved prepared connection and valid
source proof. It cannot prepare a runtime or rewrite old jobs. New multi-unit requests use
the union of selected windows and the saved processing limits; a single small unit stays local.
The first public slice requires an already-ready original-v1 receipt. Legacy or uninspected
recordings retain the explicit local/Stop fallback; repair never starts a full-file inspection.

Public integration extracts selected clips in the worker, records preparation receipts and
commits a bundle before dispatch. Restricted selected_ids enqueue retains the exact full
history-version proof while narrowing only a passage_timing/realign singleton input. The
atomic Apply writer commits the timing version and job receipt together. A repeated response
uses that exact receipt; current word maps, completeness and current-map markers join text,
bounds, result and revision guards so older output cannot overwrite newer timing.

Stop, Retry, reconcile and cleanup dispatch through repair-specific fenced paths; generic
local-only timing checks and stale parameter replacement cannot process these jobs. Retry
reconnects an existing allocation or completes preparation for a first allocation, preserving
uncertain submission and terminal no-repeat rules. Public mocked API/Store/worker/browser
checks and independent review are recorded separately from real UCloud/Linux acceptance.

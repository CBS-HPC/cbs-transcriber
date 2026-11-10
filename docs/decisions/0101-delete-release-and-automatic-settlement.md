# 0101 — Delete releases owned UCloud folders; safe old obligations settle automatically

Accepted 30 October 2026 under the user's request that Delete override blocked batches,
stop their activities, and permanently resolve safe old leftovers automatically. Extends
ADR 0092 and supersedes ADR 0066's prohibition on deletion-consented disposition only for
the explicitly confirmed batch scopes described here.

Deletion previews name the exact server, owned folder and shared-file impact. The browser
must confirm that fingerprint before cancellation or remote disposition; cancelling the
confirmation keeps the local recordings and makes no remote request. A shared allocation
with surviving unsaved recordings keeps its folder for their recovery. Deleting an entire
project or the last relevant recording authorizes moving only its allocation-owned folder
to UCloud Trash, including unwanted outputs of the explicitly deleted recordings.

Delete stops captured work and drains workers first. A logical allocation-control reservation
then excludes Stop/Retry and automatic cleanup while consent and current membership/identity
are checked. Network I/O runs outside queue, snapshot and SQLite writer locks. Terminal
remote state is required before Trash. A changed recording/result, new job, pending recovery,
changed folder or enlarged scope refuses stale deletion. Runtime consent durably closes
admission and fences producers/recovery before any folder removal.

Remote failure or an uncertain submission does not prevent batch-owned local deletion.
The independent allocation journal survives with its frozen identity, deletion decision,
deleted-member receipt and cleanup diagnostic. Activity retains a cleanup card, even when
every project/job row has gone. Retry repeats only cleanup, never transcription or submission.
Uncertain submissions retain explicit Find job/No job created reconciliation. Receipt-aware
recovery skips deleted members but keeps the immutable membership and attempts; surviving
recordings can still import their existing output. Archival/restored owners remain inert.

The idle Runtime lane checks at most one due retained owner per pass, at startup and later
passes, with a ten-minute retry interval per owner. Existing successful cleanup is retried
only under its saved policy. Failed Runtime batches can settle after fresh exact terminal
and owned-folder proof plus ADR 0066's bounded preservation of every available attempt's
stage/output bytes. Complete unsaved results still require recovery first; missing originals,
changed or unavailable evidence, uncertain submissions and unknown ownership retain the
obligation. Automatic decisions are explicitly identified separately from researcher consent.

Clear finished remains a visibility action. Neither manual nor automatic release purges
UCloud Trash, removes shared runtime/model folders, starts jobs or widens a saved folder scope.
Legacy single-job journals still need resolution before local deletion because their job rows
are the only durable control receipts; this change covers native and CBS Runtime batch owners.

Verification uses synthetic libraries, mocked UCloud and browser requests only. Real-library
schema activation and live UCloud acceptance are separate operations.

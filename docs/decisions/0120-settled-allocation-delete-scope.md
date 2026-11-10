# 0120 — Retain settled allocation membership without repeating cancellation

A project can contain a terminal, already-cleaned shared allocation whose immutable
membership includes a recording and local job deleted earlier. Expanding that historical
membership for cancellation made a later project Delete fail on the absent job.

Keep the full allocation membership in the deletion snapshot and revalidate it normally.
Skip shared cancellation expansion only when the allocation has no local work requiring
Stop, its recorded remote state is terminal, and its owned remote cleanup is confirmed
trashed or deleted. Continue to cancel directly owned active work. Uncertain or active
allocations, incomplete cleanup, preserved outputs and all existing release and recovery
guards retain their previous treatment; missing member receipts are not invented.

This decision changes neither the persistence format nor authority to dispose of remote
outputs. Synthetic sequential-delete, unresolved-owner and changing-cleanup tests exercise
the boundary; actual project deletion must still be verified in the running application.

# 0112 — Preserve uploads when publication outcome is uncertain

Accepted 30 October 2026. Refines managed recording publication and cleanup without changing
the SQLite schema, the selected storage location or queued media-job ownership.

An upload publication may commit a recording and its media job, then raise while returning the
result. Deleting the upload directory merely because the caller did not receive success can
remove the original of that durable recording. A synthetic commit-then-raise reproduction
demonstrated the loss.

On a failed publication, query the captured recording ID under the existing snapshot exclusion.
Remove the newly owned upload directory only when a successful ownership query proves the row
absent. Preserve bytes when the row exists or ownership cannot be read, then propagate the
original error. Never infer absence from a lost reply or database exception. Preserve existing
path/ownership guards and do not recursively remove another recording's directory.

Synthetic tests cover committed, absent and unreadable outcomes; real filesystem and process
crash durability are separate acceptance work. A retained unknown folder may require later
reconciliation, but preserving an original takes precedence over speculative cleanup.

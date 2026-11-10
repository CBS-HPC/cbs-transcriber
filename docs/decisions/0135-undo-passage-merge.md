# 0135 — Undo a proven passage merge

Accepted 3 November 2026.

With the automatic Working policy in ADR 0133, Undo is a manual edit: its
version and the selected Working decision commit together under the same head
and role-revision fence. A stale decision rejects the entire undo.

Offer Undo merge on a current passage only when the saved history proves which adjacent
passages were joined. Historical merges made before this feature are eligible. Validate the
integrity and result ownership of the merge version and its immediate predecessor, verify the
recorded direction and adjacent IDs, and recompute the entire merge. A historical action label
alone does not prove a merge. The current merged passage must exactly match that output, and
the removed passage ID must not already exist in the current transcript. If proof is missing,
damaged, ambiguous, or the merged passage has changed, do not offer the operation; reject a
stale request with guidance to review saved versions.

Undo restores the two exact predecessor passage dictionaries, including text, timestamps,
speaker IDs and word maps. Preserve all unrelated current passages and current speaker names.
Check active result and revision and append a `passage_merge_undo` history version in the same
transaction as both result and recording projections. Preserve existing Working role semantics;
the operation does not change a pinned version. History is not rewritten. Exact pre-merge
passages can also be recovered through the existing whole-version history where direct undo is
unavailable, with the existing implications for other edits.

The asynchronous eligibility lookup and the write-side recheck inspect at most 500 recent
versions and 100 merge records, each snapshot at most 8 MiB, and 32 MiB of snapshot bytes in
total; a historical context is at most 1 MiB. A limited search reports that fact and does not
claim a merge is unrecoverable.
These limits keep editor checks bounded; full manual edit history, broader selective undo and
live performance acceptance remain separate work.

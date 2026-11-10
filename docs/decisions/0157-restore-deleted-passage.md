# 0157 — Restore one proven deleted passage

Accepted 9 November 2026. The user wants individual passage deletion recoverable, like Undo
merge/split, while retaining later edits elsewhere. Use immutable result history; do not add
deleted text to the current transcript or create an inactive producer-passage schema.

Extend the existing bounded inverse lookup with deleted-passage descriptors and short literal
previews. Display them outside the transcript, excluded from playback, search, exports and
manual review counts. No additional history request is needed. POST restore binds the active
result, recording revision, exact deletion version and ordinary Working choice.

Verify deletion/predecessor ownership and hashes, recompute exact historical Delete, and
require a unique currently absent passage ID. Immediate surviving anchors must still be
adjacent, or the original boundary anchor must remain at that boundary. A formerly sole
passage can return only to an empty transcript. Consecutive deletions restore newest first.
Missing or ambiguous placement refuses; never guess an insertion point.

Restore the complete original dictionary, preserving all other current dictionaries and
current named speakers. Append passage_delete_undo with deleted_version_id atomically through
the common revision/Working writer. Newer verified passage presence invalidates an old delete
proof even if a later merge/split removes that ID again. Whole-version restoration forms a
conservative barrier. Corrupt/unavailable newer evidence cannot revive an older candidate.

Share the existing 100-structure/500-version/32-MiB discovery budget with merge/split undo.
Keep Apply fresh, refuse dirty UI drafts without losing the caret, and fence late replies
against route/result/view changes. No schema migration or provider request is introduced.

Verification: 173 backend/structural cases, seven focused MVP browser cases and an independent
normal-operation lifecycle reproduction passed. A separate report/complete-backup/isolated-
restore chain preserves the exact restored payload, context and original media without claiming
domain transformation verification in the portable reader.

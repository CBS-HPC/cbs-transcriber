# 0196 — Passage Undo and Redo within saved history

24 November 2026. The colleague's pilot should let a researcher walk through the saved
edits of one passage. The recording toolbar still steps through the active result's
history. Restore original and the proven structure inverses retain their separate roles.

A passage row opens its Undo and Redo controls on demand. Their labels name the next
saved action using the same wording as the recording toolbar. The first pilot covers
text, word times and eligible changes to an existing speaker assignment. It walks the
recent history up to a split, merge, delete, recording-level restoration or other
unproven scope boundary. Other proven individual passage edits preserve this passage's redo.

A step proves an exact predecessor and successor pair whose sole content difference
is the selected passage. It replaces only that passage in the current head snapshot.
The writer rechecks the active result, head, revision, current passage and selected
version. Working choices follow the existing policy. Notes, review marks, shared
speaker names and unselected passages retain their current values. Undo and Redo are
versioned edits so recording-level Undo can reverse them. The database schema is unchanged.

Use bounded history metadata and read exact payloads only for the offered steps. A
missing or damaged link, mismatched passage or bounded history refusal leaves the
saved result available in History. No asynchronous alignment is triggered.

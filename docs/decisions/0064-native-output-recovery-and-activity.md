# 0064 — Strict native output recovery and canonical batch Activity

Accepted 27 October 2026. Amends ADR 0045. No database schema change.

Read-only inspection of the failed four-file allocation showed remote SUCCESS and four JSON
outputs named `<input-job-UUID>_large-v3_da.json`. The importer previously accepted only
`<UUID>.json` or `<UUID>.<extension>.json`, so it rejected every successful output.

Keep those earlier names and additionally match the exact input UUID, actually submitted
model and language suffix. New submissions checkpoint resolved model/language parameters
before allocation, including native aliases and inherited template values. Legacy runs use
their frozen settings. For auto language, allow only known Whisper language tokens; do not
guess Danish or use loose substring matching. Require exactly one candidate inside the owned
output tree. Mismatched or ambiguous files fail closed. Retry reconnects to the saved remote
allocation and skips completed members; it never repeats successful ASR to recover an import.

Cleanup retains safe stage/code/status/time diagnostics, including prerequisite, missing raw
response, hash/parse validation, flush, connection, Trash and confirmation failures. Never
persist upstream bodies, credentials or transcript text as diagnostics. Failed prerequisites
do not contact Trash. Shared cleanup still requires every member's validated durable result.
A pending receipt establishes unverified removal, not proof that the remote files remain.
Refused ownership/opt-in prerequisites preserve the previous obligation state while recording
diagnostics; they cannot manufacture a new pending cleanup for a retained or unowned run.

Activity continues receiving per-recording jobs for editor state. It adds canonical shared
state/progress/message/update time and ordered safe member outcomes, then renders one card
per allocation. Stop/Retry/cleanup operate on the batch; a completed member cannot make a
still-running batch look finished. Terminal remote allocations need no Stop action merely
because local output import failed. Expandable outcomes survive polling. Pending cleanup,
uncertain submission and nonterminal allocations remain under Needs attention, beyond the
recent-history limit and after Clear finished. Clearing never deletes a journal or result.

27 October presentation refinement: older finished obligations collapse into one counted,
closed group after clearing; opening it exposes the existing cards/actions. Polling preserves
expansion, while Clear and page reload return to the compact view. No persistence or ownership
semantics change.

Synthetic tests reproduce the four-output naming failure, retry without allocation/upload,
partial success, ambiguous names, unsafe cleanup, safe diagnostics and Activity lifecycle.
Actual recovery of the user's existing allocation and older cleanup receipts remains a
separate operation after safe backend activation; automated results do not establish that
those live receipts have been resolved.

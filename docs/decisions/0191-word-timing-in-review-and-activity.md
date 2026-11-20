# 0191 — Word timing in review and Activity

Accepted 20 November 2026 (user decisions while testing MVP v2; post-v2 work). Amends
ADR 0148 (manual passage review), ADR 0052 (Activity hides finished realignments) and the
"Repair all" count of ADR 0056. Implemented with synthetic backend, restore and browser
verification; real-library acceptance remains separate (H15c).

Testing MVP v2, the user found passages with missing or newly added word timing hard to find.
A per-passage "Add word timing" job vanished from Activity when it finished, because finished
realignments were hidden as routine, and the ready timing is added only when the recording is
open with no draft or dialog. The "Repair all N incomplete passages" count did not change when
one passage was sent for timing.

## Decision

- **Missing timing marks Needs review.** In a result meant to have word timing, a passage
  lacking complete word timing is automatically **Needs review**, so the Show filter and Next to
  review find it. "Meant to have word timing" mirrors the editor's `expectsWordTiming`: the
  result's source capability is words/words_speakers, its options requested alignment, or any
  of its passages carries words or original words. Speakers-only and untimed-by-design results
  are never flagged. A passage with no letter or digit has nothing to time and is not flagged.
  The reason shown is "word timing missing".
- **Added or rebuilt timing marks Needs review again.** When Add word timing, Repair all or
  realignment Apply changes a passage (one `timing_realign` version), the passage becomes
  **Needs review** with the reason "timing added", even if it was Checked or explicitly Not
  checked before.
- **Derived, not stored.** Automatic marks are computed when review progress is read; no
  review row is written for them and nothing is migrated (schema stays 19). The system is the
  truthful actor: each passage view carries `automatic` (`timing_added`, `word_timing_missing`
  or null); rows remain the researcher's own decisions.
  - A row bound to the passage's exact current hash is authoritative and always wins. A
    researcher who sets Checked (or Not checked) on an untimed passage keeps that mark until
    the passage content changes again; a removed automatic mark is never invented again for
    the same content.
  - Without such a row (no row, or a stale one), "timing added" applies when the passage's
    current fingerprint equals the fingerprint a timing version recorded for it; otherwise
    "word timing missing" applies when timing is expected and incomplete. The existing
    stale-Checked rule is unchanged.
- **Timing evidence.** Each new timing successor (local Apply and the UCloud repair Apply)
  records `timing_added`, a map of the passages it actually changed to their exact resulting
  passage SHA-256, in its version context, in the same transaction. Skipped passages and
  no-change saves record nothing. The lost-reply proof reconstructs the successor and refuses
  evidence that differs; malformed evidence fails review reads closed. Versions written before
  this decision carry no evidence and raise no "timing added" mark (no backfill). Reads use only
  the newest 1,000 timing versions and 20,000 entries (4 MiB) of this evidence, never payloads,
  jobs or files.
- **Activity keeps finished word timing.** Completed word-timing jobs stay in Activity until
  Clear finished, with their outcome first: "Word timing added to passage 222" (the number when
  the recording is open; otherwise a count), noting passages that changed or stayed incomplete;
  "Ready, added when you next open the recording" (or, while it is open, "…when no passage is
  being edited"); "Not added: the passage changed"; or "Not added:" with the worker's own reason.
  Failures keep their existing cards. The local worker's own completion message is no longer
  replaced by "Complete".
- **Counts update.** Passages already queued, aligning or ready leave the "Repair all" count at
  once, and its tooltip says how many are already being timed. Applying timing re-renders the
  count; a passage whose words could not all be timed truthfully stays incomplete.

## Preserved

Hash binding, independent row revisions, stale-Checked → Needs review, drafts and late-reply
fences, deletion fingerprints (rows only), complete backups and portable restore (no schema or
validator change; restore reproduces the same derived marks), and ADR 0180: review marks stay
outside Undo. Undoing a timing change returns the passage to its earlier content, so its earlier
mark (or "word timing missing") applies again; Redo restores "timing added" unless the
researcher has marked that content since.

## Consequences

Review counts now include automatic marks, so a newly opened word-timing result with many
untimed passages starts with them under Needs review. Opt-in edit realignments also leave a
finished card in Activity per edit until cleared. Timing applied before this decision is not
flagged retroactively.

## Review-dropdown amendment, 20 November evening (V257)

The user retired the separate Review suggested tag. The existing native status dropdown
shows a disabled, selected display suggestion: **Uncertain** for current visible model hints
at the selected level, otherwise **Timing missing** for the server's automatic missing-timing
reason. Uncertain wins when both apply. An exact-content researcher mark, including explicit
Not checked, always replaces that suggestion. Derived labels never become stored/API states;
manual writes retain the same revision/hash fences. Explicitly choosing Not checked on an
unstored default now records that deliberate choice instead of treating it as a no-op.

Timing added preserves its existing Needs review label and reason. The API's three review
states, progress counts and Show/Next behavior stay authoritative. Show Uncertain may still
include a model-flagged passage with a manual mark; its tooltip explains that distinction.
The dotted text decoration, chosen hint level, drafts, caret, native keyboard handling and
late-reply fences remain. Untimed-by-design results receive no missing-timing suggestion.

Implemented on `codex/night-review-dropdown-20261120` at `9b56e20`;58 focused browser cases
passed with unchanged source/test bytes. The cached Spylls0.1.7 host/child test qualification
and its initial runner failures are recorded separately (B517), not claimed as a normal
installed-worker or screen-reader acceptance. No backend/schema change or preview activation.

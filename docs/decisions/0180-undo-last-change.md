# 0180 — Undo last change by exact predecessor restoration

Accepted 19 November 2026. The user chose every recommended answer of the
[history/undo proposal](../proposals/history-undo-2026-11-18.md) (decisions in
[editor-review-decisions](../work-plans/editor-review-decisions-2026-11-12.md) section 4).
Slice 1 (this record) is implemented behind the `undo_last_change` release-profile feature:
on in `full`, hidden in `mvp` until the user decides whether MVP v2 shows it.

## Decision

One **Undo last change** button undoes the newest content change of the result under review,
one step per click, back to the result's start. One **Redo** re-saves what the newest Undo
removed and stays available only until the next other change. Both append ordinary successor
versions (`change_undo`, `change_redo`) through the shared revision-fenced writer
(`result_actions.append_edit`); nothing in history is removed or rewritten.

- **Step.** The step is the newest version V whose payload hash differs from its predecessor
  P. Same-hash versions (no-change saves, Trash/Restore markers, no-change conflict
  observations) are skipped. A `change_undo` (context `undone_version_id`,
  `restored_from_version_id`) jumps the walk to the restored version, so repeated Undo walks
  back; a `change_redo` (`redone_version_id`, `undo_version_id`) is an ordinary change whose
  label is the re-saved action. Redo follows `change_redo` → its Undo's predecessor, so
  undo, undo, redo, redo walks forward again. Sequence 1 or `legacy_observed` is the start.
- **Proof.** Apply binds result, revision, head version and the offered V; a newer save is a
  409 and the browser reloads the newest state so the label changes. V's hash must equal the
  head's, P must pass its integrity check; P's exact segments and speakers are re-saved, so
  no later work can be lost (there is none). Merge/split/delete steps must also agree with
  the existing proofs (ADR 0135/0147/0157) reconstructing P exactly, otherwise refuse.
- **Every saved content change counts**, made by the researcher or applied at their request:
  text, word timing, speakers, structure, added word timing, Apply safe updates, conflict
  decisions, wording adoption and version restore. Review marks (ADR 0148), Working choices
  (ADR 0093/0133) and notes are separate and never undone; marks keep following the passage
  hash, so an exact undo can make an earlier Checked mark valid again.
- **Working.** Undo/Redo are manual edits: on the Working head Working follows; otherwise the
  usual Set-as-Working/keep choice applies, bound to head and role revision.
- **Bounds.** Metadata of the last 500 versions only, Undo/Redo links read at most 64 KiB,
  a step's context at most 1 MiB, payloads at most 8 MiB; beyond them the button says to use
  History. Offering a step reads one extra payload (P or the redo target) to report passage
  counts; three or more changed passages, or a version restore, ask for confirmation.
- **Deleted passages.** The shared structural scan records `change_undo`/`change_redo` as
  passage-presence rows (not barriers), so a deletion that an Undo already reversed (for
  example split → delete → undo → undo) is never offered again.
- **Browser.** Toolbar buttons beside Versions, refreshed with the merge-undo-options reply for
  the exact visible result/revision/head; disabled while a draft is unsaved, a save is
  pending or a comparison is open; no global Ctrl+Z (typing undo stays in the text box).

## Consequences

Every undo stores a full snapshot, as version restore does, and inherits lineage context
(for example `timing_artifacts`) exactly like other manual edits. A queued word-timing job
for undone text never applies because its passages no longer match (ADR 0052). Slice 2, the
History change list with "Revert this change" ([ADR 0181](0181-history-change-list.md)), and
slice 3, the branch view ([ADR 0182](0182-branch-view.md)), followed the same day.

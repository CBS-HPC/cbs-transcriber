# History, Undo last change and change list — design proposal, 18 November 2026

Status: proposal only. It answers topic 4 of
[editor-review-decisions-2026-11-12](../work-plans/editor-review-decisions-2026-11-12.md)
and TODO "Track and roll back manual edits". Nothing here is implemented or authorized.

## In short (for researchers)

- Every saved change already creates a new saved version; nothing is overwritten. We propose
  one **Undo last change** button whose label says what it will undo ("Undo last text edit",
  then "Undo last merge", …). It saves the earlier state as a *new* version, so the undo also
  appears in History and can itself be redone or restored.
- It always undoes the newest change in the result you are reviewing, one step at a time. It
  returns to the exact state saved just before that change, so it never guesses.
- **Versions → History** becomes a change list that shows what changed, when and where, with
  added and removed words highlighted. A Git-like picture of results, versions and the Working
  marker can follow later.
- You decide five choices at the end. Each has a recommended answer.

## 1. What exists today

| Change (History action) | Dedicated undo today |
|---|---|
| Text edit (`free_rewrite`, `word_correction`), word timing edit, Restore original | None; only whole-version restore |
| Passage speaker (`passage_speaker`), speaker rename (`speaker_rename`) | None (app.py:2507-2556; no undo in app.js) |
| Merge (`passage_merge`) | **Undo merge**, ADR 0135 |
| Split (`passage_split`) | **Undo split**, ADR 0147 |
| Delete (`passage_delete`) | **Deleted passages → Restore passage**, ADR 0157 |
| Add word timing (`timing_realign`, ADR 0052) | None; UI says "History can undo it" (app.js:12817) |
| Apply safe updates (`result_merge`), conflict decisions, wording adoption | None; whole-version restore |
| Restore as newest version (`version_restore`) | None; it is a whole-version replacement |
| Review marks (ADR 0148) | Not versions; a row is overwritten (passage_review.py:244-251) |
| Working designation (ADR 0093/0133) | Separate role events; Mark Working in History |

Conditions of the three proven inverses (passage_undo.py):
- **Merge** (78-106): the merge is recomputed. The current merged passage must be byte-identical
  to its output, and the removed passage ID must be absent.
- **Split** (109-138): both halves must still be unchanged and adjacent. The split is
  recomputed with its recorded policy.
- **Delete** (141-176, 356-403): the passage ID must be absent and its neighbours still
  adjacent. Consecutive deletions restore newest first. A newer `version_restore` acts as a
  barrier (204-208).
- All three may reach older changes while keeping unrelated later edits. They share a budget of
  100 structural rows, the last 500 versions, 8 MiB per snapshot and 32 MiB in total (17-20).
- All three write through one atomic writer, `append_edit` (result_actions.py:82-140). It does
  the revision compare-and-swap, updates both mirrors, appends the version and applies the
  Working fence.

What History already offers:
- 25 versions per page with sequence, a raw action name (app.js:4225-4233), time, actor, hash
  and a Working badge (app.js:4239-4276).
- The list API leaves out context and text (app.py:1693-1714; ADR 0031).
- Selecting a version shows its **full text, with no diff** (app.js:4309-4357).
- **Download**, **Restore as newest version** (app.py:1738-1755) and **Mark Working**
  (app.js:4409-4464).

## 2. "Undo last change"

**What counts as a change.** Recommended: every saved change to the reviewed result's content,
whether made by you or applied on your request. That covers text, word timing, speakers,
structure, added word timing, Apply safe updates, conflict decisions, wording adoption and
version restore. Not covered:
- review marks (no stored earlier state);
- Working choices (separate role history, ADR 0093);
- notes (separate notes versions).

A "manual edits only" rule would mean Undo stops at the first processing change, because an
exact step back cannot skip a change.

**How it works: exact step back.** The change to undo is the newest version V whose content
differs from its predecessor P.
- Versions with unchanged content are skipped: `save_no_change` (store.py:633-640), Trash and
  Restore markers (store.py:2446-2452), and no-change conflict observations (ADR 0093).
- A previous Undo record points back past the version it undid. Undo therefore walks steadily
  back through the result's history.
- Proof: the current head hash must equal V's hash, and P passes its integrity check. Saving P's
  content again is then the exact inverse of V. Nothing else can be lost, because no later
  change exists.
- The walk reads only version metadata, within the existing 500-version and 1 MiB-context
  bounds. Apply reads one extra payload (P).

**Label.** The server returns V's action, and the browser maps it to fixed text in the style of
`historyAction` (app.js:4225-4233):

| Action | Label |
|---|---|
| text and word edits | "Undo last text edit" / "Undo last word timing edit" |
| `original_restore` | "Undo last Restore original" |
| `passage_speaker` / `speaker_rename` | "Undo last speaker change" / "Undo last speaker rename" |
| `passage_merge`, `passage_split`, `passage_delete` | "Undo last merge / split / delete" |
| button-made undos | "Undo last 'Undo merge' / 'Undo split' / 'Restore passage'" |
| `timing_realign` | "Undo last added word timing" |
| `result_merge` | "Undo last safe updates" |
| `result_conflict_review` / `wording_adopt` | "Undo last conflict decisions" / "Undo last wording adoption" |
| `version_restore` | "Undo last version restore" |
| unknown action | "Undo last change" |

The tooltip gives the version number, the time and the affected passage. Changes to several
passages, and version restores, ask for confirmation with counts first.

**When undo is refused or unavailable.**
- *Start of history.* Sequence 1 (creation or import) or `legacy_observed` disables the button:
  "Nothing earlier to undo in this result" or "earlier history unknown".
- *Stale view.* The request binds result, revision, head version and V. If another window,
  another edit or a background timing Apply has saved in between, the server refuses with 409
  and the button relabels to the new newest change.
- *Integrity failure* (hash or mirror disagreement): refuse without writing, using the existing
  "Preserve the library" messages (passage_undo.py:30-58).
- *Beyond the scan bound:* "Use History for older changes".

There are no "later edits" to work around. A newer edit simply becomes the step to undo.

**Always appended, never deleted.** Undo appends `change_undo`, with `undone_version_id` (V) and
`restored_from_version_id` (P), through `append_edit`. Redo, if chosen, appends `change_redo`,
which re-saves V's exact content. Both are added to `EDIT_ACTIONS` (history.py:14-33), and so to
the research report's action vocabulary (research_reports.py:39-48).

**Working.** Undo is a manual edit (ADR 0135):
- On the Working head, Working follows automatically (result_actions.py:101-103;
  working_manual.py:30-36).
- Otherwise the usual "Set as Working / keep" choice appears, bound to head and role revision. A
  stale choice rejects the whole undo (ADR 0133).
- Undo never reverts a Working *choice*. Earlier Working versions are chosen in History.
- An undo also stops pending processing from moving Working automatically, as any edit does
  (ADR 0133 source fence).

**Review marks.** Undo never writes marks. They stay bound to passage hashes: an exact undo can
make an earlier Checked mark valid again, while a mark made after the change reads Needs review
(ADR 0148; passage_review.py:182-193).

**Drafts.** The button is disabled while a passage draft is unsaved or a save is pending (same
fences as deleted_passages.js:32-45). Typing undo stays with the browser inside the text box (12
November decision 1), so the first slice adds **no** global Ctrl+Z.

**Late replies.** Responses are fenced by the view epoch, recording, result and revision
(AGENTS.md "Guard asynchronous editor responses"; app.js:6779).

**Timing jobs.** A queued word-timing job for undone text never applies: its passages no longer
match (ADR 0052; local_timing.py:436-447).

**Reuse of the existing inverses.**
- Use the same writer (`append_edit`) and the same integrity read (`passage_undo._read_current`).
- For merge, split and delete, also run the existing proofs (`_merge_inverse`, `_split_inverse`,
  `_delete_proof` with `_restore_index`) on (P, V, current). If a proof does not reproduce P
  exactly, refuse. No merge, split or delete logic is rewritten.
- Selective reverts of older changes keep calling the unchanged undo-merge, undo-split and
  restore routes (app.py:2386-2429; passage_restore_routes.py).
- Add the step and redo blocks to the existing `merge-undo-options` response (passage_undo.py:300-323).
  It is already refreshed on every revision (app.js:6766-6866), so no additional request is
  needed (the ADR 0157 pattern).
- Add `change_undo`/`change_redo` to the bounded structural scan (passage_undo.py:181-190) as
  passage-presence rows, not barriers. They move only one linear step at a time, so a passage
  they re-add or remove is always seen before any older deletion proof is used.

## 3. The change list

For researchers: each row reads like "Version 42 · Text edit · passage 12 · 14:02 · Working".
Undone rows are dimmed and tagged "undone by version 44". Opening a row shows **What changed**:
removed words struck through, added words highlighted, plus speaker, timing or structure notes
("merged with the next passage", "word timing changed for 37 words"). The existing **Full
text**, Download, Restore and Mark Working stay.

For developers:
- *List.* Extend `GET …/versions` (app.py:1693-1714) with an allowlisted, bounded context
  subset: `segment_id`, `adjacent_segment_id`, `direction`, `restored_from_version_id`,
  `deleted_version_id`, `undone_version_id`, `redone_version_id`, `requested_action`, plus the
  merge summary counts (history.py:284-324).
- *Never in the list:* processing or artifact receipts, reasons or text (ADR 0031). Keep 25 rows
  per page (app.js:4247).
- *Diff.* Add a new read-only `GET …/versions/{id}/changes` that compares V with P:
  - Read and verify both rows in one snapshot, then compute after closing it (ADR 0031 pattern).
  - Match passages by ID; each passage is unchanged, text, speaker, timing-only, added or
    removed.
  - Use stdlib `difflib.SequenceMatcher` over whitespace tokens, with equal runs collapsed to a
    few words of context. Show speaker names with `default_speaker_names`.
- *Bounds:*
  - each payload ≤ 8 MiB (passage_undo.py:19), ≤ 10,000 passages (passage_review.py:14);
  - ≤ 200 changed passages listed, with a `truncated` flag (as result_merges.py:115-119);
  - ≤ 2,000 words per side per passage, otherwise "passage rewritten";
  - oversize gives "too large to compare; download both versions".
- *Placement.* Evolve the existing History dialog (index.html:1636-1699) rather than adding a
  panel. Put Undo/Redo beside **Versions** in the transcript toolbar (index.html:299-306).
- *Later.* Compare any two versions of one result, or a version with the Working version.

## 4. Later: branch view (sketch only)

- **Lanes and nodes.** Each saved result is a lane and each version is a node. Collapse edit
  runs ("12 text edits").
- **Branch points.** A derived result branches from its exact source version
  (`source_version_id`, store.py:2169-2249). If the source is unknown (`source_version_unknown`),
  draw the link dashed.
- **Merge edges.** Apply safe updates, conflict decisions and wording adoptions draw edges from
  their recorded base, incoming and source versions (history.py:261-363).
- **Tags.** A **Working** tag sits on the pinned version and a **Reviewing** tag on the active
  result. Trashed lanes are greyed. Undo/redo appear as revert nodes.
- **Data.** Metadata only, with no text. Bounded by result count, paginated versions and the
  1,000 role-event limit (result_roles.py:22).
- **Actions.** Clicking a node opens the change list. Only existing actions are offered: Open for
  review, Compare, Mark Working.
- **Unclear.** How complete source links are in older libraries. Show the gaps honestly.

## 5. Options and recommendations

- **Selective revert of older changes.**
  - (a) Only Undo last change + History. This drops ADR 0157's "restore an old deletion and keep
    later edits".
  - **(b) Recommended:** move the proven Undo merge/split/Restore passage into the change list as
    "Revert this change" on those rows, and remove the per-passage buttons and the Deleted
    passages section.
  - (c) Later: single-passage text/speaker reverts, using the same exact-match proof.
- **Redo.**
  - **Recommended: yes.** One Redo button, available only until the next other change (standard
    editor behaviour). It uses the same exact-hash proof. Without it, an accidental undo needs
    History.
- **Reach.**
  - **Recommended:** back to the result's start, one step per click, within the 500-version
    bound.
  - Alternatives: only this session (not reliable; the actor is "Local user (identity
    unverified)", ADR 0031), or a fixed last 20.
- **Review marks.**
  - **Recommended: not undoable.** Marks are single-click and already react to content undo.
  - Making them undoable needs an append-only mark-event table: a schema-20 migration with
    backup, portable restore and report changes (ADR 0148).

## 6. First implementable slice

**Backend.**
- New `change_undo.py` with step discovery and apply for undo and redo, reusing passage_undo
  helpers and `append_edit`.
- `POST …/transcripts/{rid}/undo-last` and `…/redo-last`, with body `{revision, head_version_id,
  version_id, working_choice}`.
- Add both paths to `manualTranscriptEdit` (app.js:807-818) so the Working choice applies.
- History allowlist: add `change_undo`, `change_redo`, `undone_version_id`, `redone_version_id`.
- Add both actions to the structural scan as presence rows.
- Gate behind a new feature key (ADR 0145). Whether MVP v2 shows it is a release decision.

**Frontend.**
- The toolbar button with live label, disabled states and confirmations, refreshed in
  `refreshMergeUndoOptions`.
- History labels for undo/redo rows.
- Per-passage buttons and Deleted passages stay until slice 2 moves them, unless you choose (a)
  above.

**ADR.** New ADR 0180, "Undo last change by exact predecessor restoration". Update the SPEC
invariants (SPEC.md:131-149), TODO, review-guide and CONTEXT ("change", "undo step").

**Tests (synthetic).**
- Label and step table for every action; skipping no-op, Trash and Restore markers; undo-redo
  chains; Redo cleared by a new change; start, legacy and bound limits.
- Exact payload equality, stale revision/head, and corrupt hash/mirror refusals without writes.
- Working: auto-follow, consented/keep, and stale role revision (as test_working_manual_policy.py).
- Agreement with the merge, split (both policies) and delete proofs.
- Deleted-passage offers after undo sequences (split→delete→undo→undo must not duplicate text).
- Checked marks returning or going stale; a timing Apply refused after undo.
- Research report and portable backup/restore preserving `change_undo` rows.
- Browser: label sequence "text edit → merge", draft-disabled, two-window 409 relabel, late
  replies.

**Slices 2 and 3.** Slice 2 is the change list, the diff endpoint and relocation of selective
reverts. Slice 3 is the branch view.

**Risks.**
- **Surprise.** Undo may undo another window's or a requested timing/merge change; the label and
  time say which.
- **Discarded work.** Deep walk-back removes many later changes from the current text (they stay
  in History and Redo).
- **Undo after timing.** The undo version still carries the inherited lineage field
  `timing_artifacts` (result_actions.py:9-27), as `version_restore` already does.
- **Scan budget.** Undo rows consume the deleted-passage scan budget. The existing "Some older
  passage merges or splits could not be checked" note (index.html:491-499) covers this.
- **Storage.** Every undo stores a full snapshot.
- **Performance.** Long-transcript save times are about 2.5 s (ADR 0159).

## 7. Questions for you

1. Older merges, splits and deletions: keep **Revert this change** in the change list
   (recommended), or offer only **Undo last change** plus History?
2. Add one **Redo** button that works until your next change (recommended), or no Redo?
3. How far back: to the result's beginning, one step at a time (recommended), only changes since
   you opened the recording, or the last 20?
4. What counts: every saved content change, including added word timing and Apply safe updates
   (recommended), or only your manual edits (Undo then stops at a processing change)?
5. Review marks: keep them outside Undo (recommended), or make them undoable (needs a database
   change)?

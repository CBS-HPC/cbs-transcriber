# Editor/review workflow decisions — 12 November 2026

User decisions from the MVP v2 editor/review discussion (continuation plan, "Next user
discussion"). Root records them here; SPEC/TODO entries follow when each piece lands.

## 1. Spoken words highlighted while typing (Text editor) — decided

- On by default for everyone; no setting or switch to turn it off. The
  `cbs-text-playback-prototype` localStorage gate is removed.
- While the draft differs from the saved text, words in the unchanged prefix/suffix keep
  highlighting; only the changed region pauses. New or changed words never receive times.
- Caret, selection, focus, scroll and browser undo are never moved while typing.
- Implementation: branch `claude/editor-review-v2` (in progress).

## 2. Review filter — decided

- A **Show** filter: All / Not checked / Needs review / Checked / Uncertain /
  **Missing word timing** (the passages Add word timing / Repair all target).
- Combines with search; Next to review follows the filter.
- Model uncertainty only suggests; it never sets or changes a manual mark.
- Implementation: branch `claude/editor-review-v2` (in progress).

## 3. Passage re-transcription — parked

- The user parked passage Re-transcribe. Re-running the same model on the same audio adds
  little; real-world value is limited to skipped/garbled stretches and other-language
  passages, while most remaining errors are names/terms (spelling/vocabulary support) or
  overlapping/poor audio.
- If revived: the NVIDIA Parakeet UCloud Transcriber model (`nvidia/parakeet-tdt-0.6b-v3`) is
  the preferred default re-transcriber, offered only when the catalog confirms it supports the
  passage's language; warn when model and language equal the passage's producer; show the new
  wording as a per-passage suggestion, never an automatic replacement; changed passages start
  as Needs review and carry a visible re-transcribed tag that never implies approval.

## 4. History and rollback — direction agreed, scope pending

- The user prefers one **Undo last change** over separate Undo split / Undo merge / Restore
  buttons. Its label names the next step (for example "Undo text edit", then "Undo merge"),
  walking back through this result's history; undo itself is recorded, never erasing history.
- The user wants changes visualised like Git: a change list with word-level diffs, and a
  branch-style view of results/versions with the Working marker.
- Open: whether per-action buttons are removed or kept inside the change list for selective
  (non-latest) reverts, Redo, and how far back Undo may reach.
- 18 November: [design proposal](../proposals/history-undo-2026-11-18.md) with recommended
  answers to five questions; nothing is implemented until the user chooses.

## UCloud key change — decided and fixed

- Saving a new UCloud key (Settings or first-use hand-off) reconnects automatically: Save runs
  the connection check with the new key before saving, and also rechecks a saved key that was
  never checked. Empty provider lists say "Check the connection in Settings" when a key exists
  but is unchecked. Branch `claude/ucloud-save-check`.

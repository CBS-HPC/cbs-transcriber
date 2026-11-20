# 0181 — History change list and "Revert this change"

Accepted 19 November 2026 as slice 2 of the [history/undo proposal](../proposals/history-undo-2026-11-18.md)
(user decision 1: older merges, splits and deletions are reverted from the change list, and
the per-passage buttons and Deleted passages section then go away). Implemented behind the
same `undo_last_change` release-profile feature as [ADR 0180](0180-undo-last-change.md): on
in `full`, hidden in `mvp`, where the per-passage buttons and Deleted passages stay.

## Decision

- **List.** `GET …/versions` keeps its fields and adds `change`: an allowlisted, bounded
  subset of the version context (passage and neighbour identities, direction, restored/
  deleted/undone/redone version links, requested action, and integer summary counts of Apply
  safe updates or conflict decisions). Never text, reasons, receipts or private metadata
  (ADR 0031). Rows read "Version 42 · Text edit · passage 12 · Working transcript"; a version a
  later Undo removed is dimmed and tagged "undone by version 44"; "redone by" and "can be
  reverted" tags follow the same rules.
- **What changed.** New read-only `GET …/versions/{id}/changes[?against=]` compares a version
  with its predecessor (or any version of the same recording). Both rows are read and
  integrity-checked in one snapshot; the comparison runs after it closes. Passages match by
  identity: changed (text, speaker label, word/passage timing), added or removed; text uses
  standard-library whitespace-token diffs with long equal runs collapsed to three words of
  context. Speaker renames are listed once, not per passage. Bounds: 8 MiB per payload,
  10,000 passages, 200 listed passages (`truncated`), 2,000 words per side (`rewritten`);
  larger versions say "download both versions". The browser builds the diff from text nodes.
- **Revert this change.** The merge-undo-options reply (feature on) lists `revertible`
  merge/split versions; deletions keep their `delete_version_id`. Only those exact proven
  versions offer the button, which calls the unchanged Undo merge, Undo split and Restore
  passage writers (ADR 0135/0147/0157) with the same Working choice and structure lock, so
  later edits elsewhere are kept. With the feature on, the transcript no longer shows per-
  passage Undo merge/Undo split buttons or the Deleted passages section.

## Consequences

The scan's proven merge/split tuples carry their own version identity last; existing
consumers are unchanged. Browser tests for the per-passage entry points run under the MVP
profile, where those controls remain. Comparing two arbitrary versions in the browser is
offered only through the branch view ([ADR 0182](0182-branch-view.md)).

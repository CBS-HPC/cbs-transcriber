# 0182 — Branch view of results and versions

Accepted 19 November 2026 as slice 3 of the [history/undo proposal](../proposals/history-undo-2026-11-18.md)
("a Git-like picture of results, versions and the Working marker"). Implemented behind the
`undo_last_change` release-profile feature ([ADR 0180](0180-undo-last-change.md)): a
**Branches** tab in the Versions dialog, hidden in `mvp`.

## Decision

- **Data.** New read-only `GET /api/recordings/{id}/version-graph` returns metadata only:
  for at most 50 results (`limited`), each result's label, provider kind/model, creation time,
  Trash state, total version count and its newest 200 versions (`truncated`) with sequence,
  action, time and Undo/Redo links; the active result and Working version. No text, speaker
  names, reasons, receipts or paths.
- **Branch points.** A derived result branches from its first version's exact
  `source_version_id` when that version belongs to this recording; `source_version_unknown`
  or an unverifiable source is returned as `"unknown"` and drawn dashed.
- **Merge edges.** Apply safe updates and conflict decisions draw an edge from their validated
  `incoming` reference, wording adoption from its `source`, only when the referenced version
  belongs to this recording and the named result. Evidence that does not validate is omitted.
- **Drawing.** Lanes in result creation order, versions in one chronological order across
  lanes. Runs of three or more ordinary versions collapse into one numbered node (click to
  expand); first/last, Working, linked and Undo/Redo versions always show. Working is a filled
  node with a tag, Undo/Redo are diamonds, Trash lanes are greyed, the reviewed result is bold.
  Nodes are keyboard-focusable buttons with full labels.
- **Actions.** Selecting a version shows its "What changed" (ADR 0181) and only existing
  actions: Open for review and Compare with current for another live result (the ordinary
  result/compare selectors with their draft guards) and Mark this version Working (the
  existing fingerprinted preview/apply).

## Consequences

The view is bounded by result count, the version window and the shown references; gaps stay
visible rather than guessed. Older libraries may lack source links, so their results start
unconnected.

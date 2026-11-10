# 0156 — Preserve known word timing in ordinary Text edits

Accepted 9 November 2026. The user explicitly requested timing retention for the MVP:
unchanged words retain exact intervals, changed words inherit their original interval, and
new words remain untimed. Automatic interpolation and alignment remain deferred.

Introduce PATCH segments/{id}/edit-v2 for ordinary Text saves with the server-fixed policy
conservative-word-map-v1. Keep legacy edit/edit-v1, historical replay and explicit Full-profile
realignment semantics unchanged. Store the policy in the new edit version's validated context;
repin the captured transcript validator and semantic worker used by portable verification.

Use bounded all-optimal token alignment. Retain exact dictionaries and timing for unchanged
words, shifting character offsets only as needed. A uniquely corresponding replacement may
inherit its own valid source interval. An inserted word receives null start/end. Preserve
passage bounds, named speakers, unrelated dictionaries and the ordinary revision/Working fences.

Repeated or moved wording, ambiguous correspondence, malformed mappings and exceeded budgets
must refuse atomically rather than discard valid timing or guess an interval. Keep the draft
so the user can make a smaller edit. Do not lend an interval mapped to only part of a lexical
word to a whole replacement. An already-stale map may save untimed with an explicit warning;
that exception does not authorize clearing a valid map on an ambiguous edit.

The user also requested double-clicking a rendered word to open Text; explicit Word and timing
remains available. Text Replay retains the draft/caret without a duplicate playback preview.
The word-timing editor retains its saved-text preview (ADR 0150).

Verification: 52 final policy/API/contained checks, 228 legacy/history/adoption/structure
regressions, focused browser tests and an independent exhaustive 14,641-pair alignment oracle.
These establish synthetic behavior, not human timing accuracy or live acceptance.

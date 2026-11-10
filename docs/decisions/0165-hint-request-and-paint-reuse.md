# 0165 — Share live hint requests and repaint decorations

Accepted 10 November 2026. Extends ADRs 0150 and 0159 without changing mutation responses,
stored content, manual-review state or processing admission.

The rc5 Save/Split audit observed one mutation render followed by another complete transcript
render when model hints arrived. Duplicate same-context loads could also issue several GETs.

Share a pending hint request only for the exact lifecycle epoch, recording, saved result,
content revision and editor generation. Latest-load fencing prevents a superseded reply
replacing newer cached data. Remove only that request's pending entry on completion/failure;
a failure does not poison later retries.

Apply hints to current summaries, flags and changed word-mark paragraphs. Retain passage and
manual-control nodes; preserve active drafts, search, delegated word events and playback.
Skip repaint while editing/dirty; closing the editor can apply current cached decorations.
Comparison alignment may relayout without changing the comparison payload.

One existing full mutation render and the full API response remain. This removes the extra
hint-driven rebuild; it does not claim delta mutation responses or instantaneous long saves.
Browser regressions cover real Save/Split, changed/failed/delayed hints, duplicate requests,
late generations, draft/selection retention, manual-review counts and timing tags.

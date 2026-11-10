# 0022 — Automatic comparison and shared search

Accepted: 21 October 2026. Supersedes the search/manual-anchor UI in 0011 and 0013;
the whole-transcript matching algorithm and read-only API remain unchanged.

Selecting a comparison result automatically aligns matching passages. Remove the separate
phrase form, occurrence selectors and Align these occurrences button. Use the transcript's
existing search field for both panes, labelled Find in both transcripts during comparison.
Highlight literal, case-insensitive matches as the user types, preserving Unicode offsets
and word-seeking buttons. Search text is never interpreted as a regular expression.

Keep all comparison passages, gaps and aligned rows visible during search, even when a query
occurs in only one result. This preserves the context needed to compare omissions and wording.
Single-transcript search continues filtering passages. Entering and leaving comparison retains
the query and applies the corresponding view behavior. Guard view changes against unsaved
edits and late responses. Clearing the query removes search marks while alignment remains.

Update search marks in the existing passage elements rather than rebuilding the comparison
on each keystroke. Retain word controls, playback state, aligned rows and unsaved edit fields.
Reuse the existing local alignment response and DOM marks; no new dependency, inference,
database migration or transcript mutation is required. The recording-scoped phrase/anchor API
remains available for existing clients, without a manual anchor workflow in the browser.

Chromium acceptance covers automatic alignment, split passages, one-sided and repeated search,
literal punctuation after emoji, Danish text, mode transitions, seeking and draft protection.

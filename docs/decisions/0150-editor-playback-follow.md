# 0150 — Follow saved words while editing

Accepted 9 November 2026. Implemented and verified with synthetic browser playback and draft checks.

Opening the passage editor replaces the ordinary passage-text nodes that playback normally
highlights. The initial implementation retained a compact Saved text for playback preview
inside both editor modes, using the same valid word mapping/rendering as ordinary playback.
The user rejected that duplicated panel in Text mode on 9 November. Keep Replay there and
show the saved-text preview only in Word and timing. Highlighting in one editable Text area
remains part of the broader redesign; do not imply it is already implemented.

Highlight only existing reliable word intervals. With Follow playback enabled, reveal the
current word by scrolling the preview internally; do not move the text/timing input caret,
selection, focus or draft, and do not scroll the entire editing workspace automatically.

Explain that playback follows saved wording when an unsaved text or word draft differs.
Never assign the draft invented timing. Legacy free rewrites still lose stale highlighting;
new Text edits follow ADR 0156's explicit conservative-retention policy. Missing/partial timing
keeps its existing limits.
No provider, alignment or dictionary call is introduced by opening or replaying the editor.

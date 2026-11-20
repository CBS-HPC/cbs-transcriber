# 0188 — Uncertainty decoration inside native Text drafts

Accepted weekend-plan scope, implemented 20 November 2026 on the separate post-v2 editor
branch. This complements the existing Show uncertainty control and ADR 0187 spelling layer;
it does not introduce a new editor or change the review workflow.

## Decision

Display model uncertainty as amber dotted decoration in a pointer-free layer aligned with
the existing native textarea. Spelling remains red and distinct. Uncertainty does not require
word timestamps, and neither decoration claims a corrected word or verified timing.

Use only saved uncertainty spans that still map to unchanged draft text. Preserve Unicode
code-point offsets, CRLF interpretation and unchanged prefix/suffix correspondence; edited
spans lose stale decoration instead of receiving guessed confidence. Recompute from current
hint state and the existing Show uncertainty toggle. No new persisted preference is needed.

The textarea retains ownership of typing, selection, caret, focus, native Undo, IME, keyboard
and scroll. Repaint decorations in place; never rebuild it for a delayed hint or toggle update.
Keep the mirror aligned on resize and disconnect its observer when the editor closes. Replay
highlighting continues through the existing valid word map, without inventing timing or
bringing back the rejected duplicate saved-text panel.

## Verification and limits

Seven new browser cases exercise untimed uncertainty, independent spelling, draft edits,
Unicode/CRLF offsets, selection/focus/native Undo and delayed decoration/toggle behavior.
The final 58-case editor/spelling browser gate passed. A synthetic screenshot was visually
reviewed. See the dated verification receipt for backend/engine coverage and its cached
Spylls harness limitation. Real users, real OS IME/macOS input, Firefox/Safari, accessibility and installed-worker
acceptance remain separate. The branch was not loaded or released.

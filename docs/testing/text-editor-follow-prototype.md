# Review Text-editor word following

10 November 2026: introduced as an opt-in prototype on `codex/post-mvp-work` behind the
`cbs-text-playback-prototype` browser key. 12 November 2026: the user made it the default for
MVP v2, with no setting or switch; the key is no longer read and can be left or removed.

The native textarea stays the sole editable surface. A noninteractive, screen-reader-hidden
mirror marks only verified saved-word ranges. It never replaces the input value, caret,
selection, scroll position or undo history, sends text anywhere or saves a transcript.
When the draft differs from the saved text, the mirror follows the draft: words wholly inside
the common code-point prefix keep their offsets, words wholly inside the common suffix shift
by the length change, and words touching the changed middle, or whose letters were extended,
are not highlighted. New or changed words never receive a time. The mapping is rebuilt once
per draft change; playback ticks only look up cached word nodes. Follow playback does not
scroll the textarea for 1.5 seconds after typing. Word and timing retains its separate preview.

## Focused checks and remaining review

`tests/test_browser_text_playback.py` covers default-on behavior, replay while retaining a
selection, native typing/Undo/Redo, unchanged prefix/suffix and changed/new words,
missing/stale intervals, internal scrolling and the typing pause, Follow off, resize and a
5,004-word Unicode/newline/tab/long-token fixture. Existing Text, playback, double-click,
readability and structural gates remain relevant.

This is synthetic Chromium on Windows evidence, not Mac/Safari/Firefox, IME, high-contrast,
screen-reader, browser-zoom or representative research-user acceptance. A prefix/suffix
mapping keeps only the text before the first and after the last change; words between two
separate edits stay unhighlighted until the draft is saved. No timing quality guarantee is
inferred from these checks.

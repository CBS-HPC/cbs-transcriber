# Review the Text-editor word-following prototype

10 November 2026. Development branch `codex/post-mvp-work` only. This is not in the installed
or published MVP 0.1.0, and it does not settle the broader editor layout or workflow.

The prototype keeps the native textarea as the sole editable surface. A noninteractive,
screen-reader-hidden background marks only verified saved-word ranges. It never replaces
the input value, creates draft timing, sends text anywhere or saves a transcript by itself.
Different unsaved wording pauses highlighting until Save or native Undo restores correspondence.
Word and timing retains its existing separate preview; Text has no duplicate saved-text panel.

## Opt in on a disposable development library

Start this branch separately with synthetic data and an unused loopback port. In that page's
browser developer console, run:

```javascript
localStorage.setItem("cbs-text-playback-prototype", "1");
location.reload();
```

Open Text editing and Replay. Follow playback controls internal scrolling; typing focus,
selection and native undo remain in the textarea. No permanent prototype checkbox or new
production setting was added. Revert with:

```javascript
localStorage.removeItem("cbs-text-playback-prototype");
location.reload();
```

## Focused checks and remaining review

`tests/test_browser_text_playback_prototype.py` covers default-off/revert, replay while
retaining a selection, native typing/Undo/Redo, missing/stale intervals, internal scrolling,
Follow off, resize and a 5,004-word Unicode/newline/tab/long-token fixture. Existing Text,
playback, double-click and structural gates remain relevant. Exact run results belong in
the branch verification receipt.

This is synthetic Chromium on Windows evidence, not Mac/Safari/Firefox, IME, high-contrast,
screen-reader, browser-zoom or representative research-user acceptance. Discuss readability,
scrolling and editing experience with the colleague before enabling it by default. No
long-transcript performance improvement or quality guarantee is inferred from these checks.

# Recording layout follow-up — 27 October 2026

User-requested revision to T81: Review result sits below the title on the left, followed by
Compare with and Versions on the same row. The later alignment revision places Versions and
Find in transcript at the transcript area's right edge, with equal dropdown/button heights.
Controls wrap on narrow screens; existing IDs and selection/search behavior are unchanged.

Validation: `uv run pytest tests/test_browser_review_layout.py -m browser -o addopts=''`:
**3 passed**. This includes live result selection, result Trash/Restore, the single-result
comparison guard, and audio/video comparison playback visibility. Desktop (1920px) and narrow
(760px) screenshots were inspected; search has the normal 10px control gap and no horizontal
overflow was observed. Prettier 3.6.2, Ruff, formatting and whitespace checks pass.

Ignored evidence: `test-results/review-layout-left-controls.xml`,
`test-results/review-compact-desktop.png`, `test-results/review-compact-small.png`.
Tests used synthetic media in temporary libraries. The running backend was not restarted.

The changes touch only the recording header/search layout and its existing browser tests.
Concurrent Runtime discard work in the same HTML/CSS files was preserved.

Further user revision: removed the redundant saved-result summary beneath the selectors and
reduced the header margin from 28px to 16px. The transcription controls now begin 12px below
the result controls on desktop. The same three browser cases passed again (110.77s), with
new assertions for the absent summary and compact spacing. Desktop and 760px screenshots,
scoped Prettier 3.6.2, Ruff lint and formatting were checked again. No backend restart was
needed for this static layout change.

Final requested order: Review result, Compare with, Versions. The button now reads exactly
"Versions" without an ellipsis. The same three browser cases passed after this swap (49.53s),
including explicit order/label checks; scoped Prettier and Ruff checks passed. These repeated
three-case runs are the same tests, not nine distinct cases.

Right-edge alignment revision: the result and transcription control rows now share the
transcript column's width, including its actual 1150px details-column and 850px stacking
breakpoints. Search and Versions end at that column's right edge; comparison uses the combined
transcript area. Versions and both dropdowns have matching 35px surfaces. Controls wrap
instead of overlapping when space is limited. No JavaScript positioning was added.

All three focused browser cases passed (56.52s), including edge/height checks at 1920, 1440,
1100 and 760px and comparison/resized playback. Desktop and narrow screenshots were inspected;
no horizontal overflow was observed. Scoped Prettier 3.6.2, Ruff lint and formatting pass.
The running backend and library remain untouched; a page refresh loads these static changes.

Transcribe centring revision: shared automatic flex margins distribute equal free space on
either side of Transcribe between the feature controls and right-aligned search. All three
focused browser cases passed (107.12s); the wide-screen assertion measures both gaps within
2px, while breakpoint/comparison checks retain their earlier coverage. Scoped formatting and
lint checks pass. The desktop screenshot was inspected.

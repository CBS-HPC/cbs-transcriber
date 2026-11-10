# 0024 — Export settings and visible timing gaps

Accepted: 21 October 2026.

Move the promotional export card to a shared Export Settings dialog. Open it from a reviewed
result, comparison pane or project. Project downloads choose the active reviewed result per
recording or every saved result, and use a ZIP with manifest.json and EXPORT REPORT.txt.
Read result rows with one SQLite SELECT, isolated to the project. Numbered archive directories
prevent duplicate-name collisions; sanitize member basenames. Never export original media or
change library data. A spooled temporary file bounds ZIP memory; close it after download.
Report every missing transcript or format-incompatible result; fail explicitly if none can
be exported. No silent omission.

Keep NVivo Windows/Mac and readable text compatibility. Add UTF-8 SRT/WebVTT, CSV and JSON:
- SRT follows numbered cues and comma milliseconds documented by the Library of Congress:
  https://www.loc.gov/preservation/digital/formats/fdd/fdd000569.shtml
- WebVTT follows UTF-8, header, dot milliseconds and voice spans:
  https://www.w3.org/TR/webvtt1/
- Subtitle cues require ordered positive millisecond intervals. Do not interpolate missing
  timing or split long passages into invented timings; these are passage-level exports.
- CSV quotes fields and prefixes formula-like text cells with an apostrophe. JSON keeps exact
  current text, names and usable words, excluding original versions and provider/runtime setup.
  It is a current-view data export, not a complete library backup.

Word-timed result views count and visibly flag passages whose word_timing is false, including
both comparison panes and rewritten passages. Determine expected word timing from saved provider/
enhancement metadata or imported word data. Segment-only results do not receive these warnings.
Explain free-text invalidation separately. This exposes the existing conservative timing fallback;
it neither fabricates intervals nor renormalizes saved results. Partial reliable-word playback
remains a future design decision.

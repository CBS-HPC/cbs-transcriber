# 0010 — Explicit NVivo transcript formats

Accepted: 20 October 2026. Clarifies the export boundary in 0003 and supersedes
the assumption that the reference scripts' readable output is a timed NVivo Windows transcript.

The supplied scripts and guides create a readable INTERVIEW document with separate speaker
or timestamp headings. They do not establish NVivo's media-transcript import schema.
The old export contained no tabs, so it failed the documented Windows text import requirements.
The user requires Windows and Mac support and permits rewriting the scripts' functionality.

Offer three explicit formats. Windows uses one tab-delimited row per segment with
Timespan, Speaker and Content headers; Mac uses documented speaker-colon text and F5 timestamp
markers. Windows timespans and Mac markers retain actual segment boundaries at the documented
tenth-second precision. Both download as UTF-8 with BOM, with platform-specific filename suffixes.
Readable text retains INTERVIEW and the original consecutive grouping workflow.

Flatten cell/paragraph whitespace only in exported copies; retain all words, punctuation,
speaker mappings and selected-result edits. Native exports require a known start for every
segment and reject missing timing before returning a file. Never omit untimed passages or
fabricate their timing. Readable text remains available for these results.

Keep existing no-format API links and project-folder text snapshots as readable text for
compatibility. The UI explicitly chooses a format and supplies that parameter; native formats
do not group segments together because that would discard passage timing or speaker changes.
Provide platform-specific import instructions, including Windows header/column mappings.
Mac's documented colon-space parsing can treat transcript text as a speaker; disclose this
limitation and require users to review the imported speaker fields.

Regression coverage checks actual download structure, result isolation, first/last passages,
Unicode, whitespace, timestamp rounding, start-only timing and explicit untimed fallback.
Chromium checks all three downloads and the UI controls. No desktop NVivo installation was
used to validate an actual import; both platform import workflows remain an external acceptance
check, not a claim made by our automated tests.

Sources: [NVivo Windows import](https://help-nv.qsrinternational.com/20/win/Content/files/import-audio-video-transcripts.htm),
[NVivo Mac import](https://help-nv.qsrinternational.com/20/mac/Content/files/import-audio-video-transcripts.htm).

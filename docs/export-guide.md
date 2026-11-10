# Export transcripts and import them into NVivo

Open **Export Settings** for a recording or project. Save transcript drafts first; exports
use saved changes. Select recordings and one or more formats. Project exports use exact
**Working** versions by default, with reviewed/all-result choices available. The shown
collection limits a project export. Its report identifies exported and skipped results/formats.

**Include original recordings** starts unchecked. Enable it to include selected originals
beside transcripts, or deselect all formats for an originals-only ZIP. Recordings without
transcripts show a reason and become selectable when originals are included; they are not
automatically selected. Empty selections disable export.

Multiple files/formats use a ZIP organized by recording names, with collection folders where
applicable. A single-format recording download stays an ordinary file when originals are
excluded. A current-view export is not a complete backup or full history collection.

## Background ZIP downloads

Preparation appears in **Activity**. You can close Export Settings and continue editing;
choose **Download ZIP** when ready. **Cancel preparation** stops an unfinished ZIP, while
**Discard ZIP** removes a temporary download. Both preserve recordings and saved transcripts.
Keep the app running until preparation/download finish. Prepared ZIPs are temporary and are
removed when the server stops. Use [complete backups](portable-backups.md) for preservation.

## Choose a format

| Format | Use and timing requirements |
| --- | --- |
| Word (.docx) | One paragraph per passage with start time, speaker and text. Suitable for comments/tracked changes; Word edits do not flow back into the app. |
| Readable text (.txt) | An INTERVIEW document with speaker/timestamp headings. Retains untimed passages. |
| NVivo for Windows (.txt) | Tab-delimited Timespan, Speaker and Content rows. Requires passage start times. |
| NVivo for Mac (.txt) | Speaker-colon paragraphs with F5-style timestamp markers. Requires passage start times. |
| SRT / WebVTT | One subtitle cue per passage; every passage needs valid start/end times. Long passages may need subtitle editing. |
| CSV | Text/speaker rows, including untimed passages. Formula-like text is prefixed with an apostrophe for spreadsheet safety. |
| JSON | Current text, displayed speakers and usable word timing, including untimed passages. Excludes original wording and provider settings. |

Native NVivo files include a UTF-8 BOM and use tenths of a second. Tabs/newlines within a
passage become spaces. Saved originals and full-precision timings stay unchanged. Grouping
applies to readable text; native rows retain each passage's own speaker/timing. Missing start
times block native export with an explanation; readable text/Word/CSV/JSON remain alternatives.
No export invents alignment.

## NVivo for Windows

1. Import the matching original recording into your NVivo project.
2. Open that media and turn on **Edit**, then **Import Rows**.
3. Choose the app's **NVivo for Windows (.txt)** file and **Tab Delimited Line**.
4. Tick **File includes header row**. Map `Timespan`, `Speaker` and `Content` to those fields;
   create the Speaker custom field if necessary.
5. Choose UTF-8 if encoding detection needs correction. Check the first/last passages,
   speaker fields and synchronization against the original media.

## NVivo for Mac

1. Import the matching original recording into your NVivo project.
2. Open that media and turn on **Edit**, then **Import → Files → Transcript Rows**.
3. Choose the app's **NVivo for Mac (.txt)** file. Follow your NVivo version's requirements
   for an empty transcript and choose UTF-8 if needed.
4. Check speaker fields: the importer may interpret colon-space inside transcript wording
   as a speaker label. Check first/last passages and synchronization too.

The formats follow the official
[Windows transcript guide](https://help-nv.qsrinternational.com/20/win/Content/files/import-audio-video-transcripts.htm)
and [Mac transcript guide](https://help-nv.qsrinternational.com/20/mac/Content/files/import-audio-video-transcripts.htm).
Format/browser checks and synthetic fixtures do not establish actual desktop NVivo acceptance;
that remains pending. Use the separate [NVivo test plan](work-plans/mvp-nvivo-test-plan-2026-11-11.md).

## API and research exports

Explicit download formats use `format=nvivo-windows`, `format=nvivo-mac` or `format=text`
with the selected `result_id`. Older API links without a format and project-folder snapshots
retain readable text. Version-specific research downloads and metadata-first methods reports
are described in [research history](research-history.md); reports are hidden in MVP.
The longer-term [AI/NVivo roadmap](ai-analysis-nvivo-roadmap.md) remains planned work.

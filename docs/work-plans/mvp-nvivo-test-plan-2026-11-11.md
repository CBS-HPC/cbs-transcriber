# NVivo test plan for the MVP tester — November 2026

This is a companion to [the MVP plan](mvp-2026-11-11.md). It asks one question: do the
CBS Transcriber exports work in the NVivo you actually use? Plan about 90 minutes, after the
app walkthrough. Use only the synthetic test pack and public or consented test recordings,
never real interview data. Use the released **MVP 0.1.0** and record the exact NVivo build.
Nothing here has been tested in desktop NVivo yet; see the NVivo acceptance item in TODO.md.
Your results are the first real evidence.

## 0. Before you start

The maintainer prepares:
- **Synthetic pack.** Run `scripts/build_nvivo_validation_pack.py` as described in
  [the validation pack guide](../nvivo-refi-validation-pack.md) and give the tester the ZIP. It holds a
  12-second clock WAV plus Danish and English transcripts whose exact correct values are known.
- **Her own test project in CBS Transcriber.** From the MVP walkthrough, it needs:
  - one short recording (~5 min) and one long recording (30–60 min) transcribed with UCloud large-v3;
  - one OpenAI text-only result without timing;
  - two collections;
  - speakers renamed, for example "Interviewer" and "Participant".
- **A results folder** on the local disk (not OneDrive) for screenshots and the results sheet.

Write down once:
- the NVivo product name and full version/build, from Help → About;
- the licence edition;
- Windows or Mac, with the OS version;
- the NVivo interface language;
- the CBS Transcriber version shown in Settings;
- the date.

Use a **new, empty NVivo project for each format** so that results do not mix.
Name each project, for example, `MVP-WIN-native-1`. Never test in a real research project.

## 1. What the app exports, and the NVivo route for each

In the app, use **Export Settings** beside the transcript (one recording) or in the sidebar
(whole project).

| App format (file name ends with) | What it is | NVivo route to try (record the exact menus you used) |
| --- | --- | --- |
| NVivo for Windows (`.nvivo-windows.txt`) | Tab-separated `Timespan / Speaker / Content`, one row per passage, tenths of a second, UTF-8 | **Windows NVivo.** Import the audio/video first and open it. Turn on **Edit**, then **Import Rows**. Choose **Tab Delimited Line** and tick **File includes header row**. Map Timespan, Speaker (create this custom field if needed) and Content (README.md:579-582) |
| NVivo for Mac (`.nvivo-mac.txt`) | `#hh:mm:ss-t#` time markers, then `Speaker: text` paragraphs | **Mac NVivo.** Import and open the media. Turn on **Edit**, then **Import → Files → Transcript Rows** (README.md:583-586) |
| Word document (`.docx`) | One paragraph per passage: time, speaker, text | Import as an ordinary **document** (Files). It is readable and codable, but not linked to the audio timeline |
| Readable text (`.txt`) | `INTERVIEW` document grouped by speaker or by timestamp; keeps untimed passages | Import as a **document**. Not linked to the audio |
| Project ZIP (Export Settings in the sidebar) | Chosen formats in named recording folders, grouped by collection, plus `EXPORT REPORT.txt` and `manifest.json`. Originals are excluded by default; Include original recordings adds selected audio/video | Unzip, read `EXPORT REPORT.txt`, then import each file by the route above |

The app does **not** offer the following, so do not test them as app features:
- REFI-QDA `.qdpx`/`.qdc` exports (planned, T88);
- transfer of codes or annotations to NVivo;
- re-import of NVivo edits back into the app.

SRT, VTT, CSV and JSON are not NVivo routes.

## 2. Test cases

Mark each case PASS, FAIL, BLOCKED, NOT_SUPPORTED or NOT_RUN (see §3).
If you need a workaround, record it as a separate attempt. Do not count it as a PASS.

### Part A — Synthetic pack (controlled answers, about 20 min)

Follow WIN-01…WIN-03 on Windows, or MAC-01…MAC-03 on Mac, from
[the validation pack](../nvivo-refi-validation-pack.md) (§Windows, §macOS). Use `danish/` first.
Fill in its `manual-results.csv`. What to expect:
- four rows and exactly two speakers;
- times 0.0–2.0, 2.0–6.0, 6.1–10.0 and 10.1–11.9 s;
- æ, ø, å, emoji and accents unchanged;
- `Hun sagde:` stays inside the text and does not become a new speaker (an important Mac check).

Optional: WIN-04 or MAC-04 (English, start-only times) and BOTH-01 (project ZIP report).

### Part B — Your own exports (about 60 min)

| ID | Do this | Expected result |
| --- | --- | --- |
| N1 Timing | Import the short recording plus its native transcript. Click 5 rows at the start, middle and end; play each | Audio starts at the spoken words, within about 1 s. No row is shifted later and later through the file |
| N2 Speakers | Look at the Speaker column or labels | "Interviewer" and "Participant" appear, not `Speaker_01`. Consecutive turns by the same person stay separate rows |
| N3 Danish | Find words with æ ø å Æ Ø Å, quotes and dashes | Identical to the app. If text looks garbled, re-import choosing **UTF-8** and record that (README.md:591) |
| N4 Long interview | Repeat N1 with the 30–60 min recording | Every passage is imported; the last row is near the end of the audio; NVivo stays usable. Note the import time |
| N5 Edits | In the app: edit one passage, merge two, split one, delete one, then export again. Import into a **new** NVivo project | Edited wording present; merged passage is one row; split passage is two rows with sensible times; deleted passage absent |
| N6 Untimed | Try NVivo for Windows or Mac export of the OpenAI text-only result | The app refuses with a clear message suggesting Readable text. Readable text then imports as a document with all text present. Nothing missing silently |
| N7 Project ZIP | Export the project (Working version per recording, NVivo format) and unzip | One folder per collection. `EXPORT REPORT.txt` lists exported and skipped recordings, with reasons. Two files import as in N1 |
| N8 Coding | Create 2 codes. Code 3 passages in a timed transcript and 1 paragraph in the `.docx` document. Open the code and play a coded media passage | Coding works as usual. Coded transcript text plays the right audio |
| N9 Re-import | In a **copy** of the N8 project, import the N5 (edited) export into the same media file | Record only what NVivo does: are rows replaced or appended, is coding kept or lost? The app never syncs back from NVivo |
| N10 Word | Open the `.docx` in Word, then import it into NVivo as a document | Times and speakers are readable; text is codable; æøå are correct |
| N11 Readable | Export Readable text twice (Group by Speaker, Group by Timestamp); import both | Headings make sense; every passage is present, including untimed ones |
| N12 Other platform | If a Mac or Windows NVivo is also available, repeat N1–N3 with the other native format | Same as N1–N3; check colon handling especially on Mac |
| N13 REFI (information only) | Does your NVivo offer codebook (`.qdc`) or REFI project (`.qdpx`) export or import? | Note yes or no and the menu path. This informs later work (T88); it is not a pass or fail |

## 3. Results template

Copy this table into Word or Excel. Use one row per case per platform.

| Case | Platform + NVivo version | File imported | Result | What happened (your words) | Screenshot file(s) | Minutes |
| --- | --- | --- | --- | --- | --- | --- |
| N1 | Windows 11, NVivo … | `2026-11-11 - short.nvivo-windows.txt` | PASS / FAIL / BLOCKED / NOT_SUPPORTED / NOT_RUN | | `N1-1.png` | |

- **PASS:** worked as expected without any manual fixing.
- **FAIL:** wrong, missing or garbled result.
- **BLOCKED:** you could not get that far (say why).
- **NOT_SUPPORTED:** your NVivo has no such feature.
- **NOT_RUN:** skipped.

Name screenshots by case, for example `N5-merged-row.png`. Include the NVivo window and, where
relevant, the same passage in CBS Transcriber.

## 4. How to report a problem

1. Take a screenshot straight away, and write down the exact NVivo menu path and any message text.
2. Note the time and the CBS Transcriber version from Settings.
3. Keep the exported file. Attach it only if it comes from the synthetic pack or public test audio.
4. Send your results sheet and screenshots to the maintainer through the agreed Teams chat or email,
   the same day if a problem blocks further testing.
5. The maintainer reproduces the problem and records it in TODO.md (NVivo acceptance item) or
   SPEC §B bug memory. Format fixes need their own regression test before the next build.

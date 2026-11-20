# Human tests before public release

Started 20 November 2026 at the user's request. This list collects checks that only a person
can do: on real devices, with real UCloud/Hugging Face accounts, real recordings and real desktop
software. Automated tests use synthetic data and mocked providers; they show the code behaves
as designed, not that it works on someone's machine or account. Add a check here whenever a
change is verified only synthetically and still needs a real-world look.

Each check keeps a status line: **Not run**, **Passed** or **Failed**, with the date, device,
app version and a short note. A pass covers only what was actually tried (for example one
Windows PC and one batch size). Checks marked **paid** start credit-using UCloud work and need
the user's approval each time; checks marked **later** belong to features still hidden in MVP v2.

## Installation and updates

### H1 · Fresh install on a clean Windows PC

- **Do:** On a Windows x64 PC without the app, follow README "Install and start" with the
  release wheel and constraints file; start `cbs-transcriber`.
- **Pass when:** Installation finishes without changing Conda or global PATH, the browser opens
  at http://127.0.0.1:8765, and a short recording can be uploaded, played and exported.
- **Status:** Not run. (MVP 0.1.0 installed on the developer PC only.)

### H2 · Desktop and Start menu shortcut on Windows

- **Do:** `cbs-transcriber --create-shortcut --shortcut-location desktop --profile mvp`, then
  double-click it; repeat without a location for the Start menu.
- **Pass when:** The shortcut starts the installed app with the MVP profile and the same
  library; creating it again refuses to overwrite.
- **Status:** Not run (only a disposable-folder check so far).

### H3 · Apple Silicon Mac (macOS 14 or later)

- **Do:** Install, start, create the `.command` launcher, save a UCloud key and transcribe a
  short clip locally on the CPU.
- **Pass when:** Everything in H1/H2 holds on the Mac; keys are stored in Keychain.
- **Status:** Not run.

### H4 · Intel Mac

- **Do:** Install and start.
- **Pass when:** Core, API and UCloud work; local transcription is plainly reported as
  unavailable on this Mac rather than failing later.
- **Status:** Not run.

### H5 · Update from 0.1.0 with an existing library

- **Do:** Save edits, finish or stop jobs, make a complete backup, stop the app, install the new
  wheel and constraints with `--reinstall-package cbs-transcriber`, start it.
- **Pass when:** The library opens (with any migration backup made first), and projects,
  transcripts, Working versions, notes and review marks are unchanged.
- **Status:** Not run.

### H6 · Uninstall

- **Do:** `uv tool uninstall cbs-transcriber`.
- **Pass when:** The tool is gone; libraries, project folders and saved credentials remain, as
  the uninstall guide says.
- **Status:** Not run.

## UCloud batches

### H7 · Automatic reconnect after closing the app (paid)

- **Do:** Start a small native batch (2–3 short files). When Activity shows it waiting in UCloud's
  queue or transcribing, stop the app with Ctrl+C. Start it again later.
- **Pass when:** Without pressing Retry, Activity picks the same batch up again, follows it and
  saves each result once; the UCloud working folder is cleaned up afterwards.
- **Why human:** Startup reconnect (T141, ADR 0174) is new in v2. On 12 November the overnight
  batch needed an explicit Retry; this preview is the reconnect's first real use.
- **Status:** Not run.

### H8 · Closing the app while files upload (paid if resubmitted)

- **Do:** Start a batch of several larger files and stop the app while Activity shows uploads.
  Start again and press Retry on the interrupted batch.
- **Pass when:** Only files without a confirmed upload are uploaded again (a half-uploaded file is
  replaced), one UCloud job is submitted, and no file is transcribed twice.
- **Status:** Not run.

### H9 · Closing the app while results download

- **Do:** Stop the app while Activity reports results being saved; start again.
- **Pass when:** Saved files stay saved, the rest are fetched and saved once, and nothing in the
  project is half-written.
- **Status:** Not run.

### H10 · Early uploads, upload limits and plain status (paid)

- **Do:** Transcribe a selection where some files inspect slowly. Try Settings → UCloud "Files
  uploaded at once per batch" (1–8) and "UCloud Transcriber batches at once" (1–4).
- **Pass when:** Each file uploads as soon as its own inspection passes, the limits hold, and
  Activity reads plainly ("Waiting in UCloud's queue for a GPU · 12 min", "Transcribing on UCloud ·
  5 of 12 files reported done").
- **Why human:** ADR 0183 is verified with mocked UCloud only.
- **Status:** Not run.

### H11 · Stop a running batch (paid)

- **Do:** Press Stop in Activity on a queued or running batch.
- **Pass when:** The UCloud job ends, credit use stops, and the working folder moves to UCloud
  Trash; results already saved stay.
- **Status:** Not run on v2.

### H12 · Delete a recording that is in a running batch (paid)

- **Do:** Delete one file of a running batch and confirm the impact warning.
- **Pass when:** The warning names the other files affected, the shared job stops, the other
  files keep their saved results, and Activity shows the outcome.
- **Status:** Not run.

### H13 · Credentials in the operating system's store

- **Do:** Save the UCloud key and Hugging Face token, restart the app and the PC.
- **Pass when:** Both are kept in Windows Credential Manager / macOS Keychain, never shown in
  full, and still work after the restart.
- **Status:** Not run on v2.

## Local processing

### H14 · Local transcription and first model download

- **Do:** With a Hugging Face token saved, transcribe a real interview locally (CPU; GPU where
  available).
- **Pass when:** Model preparation shows in Activity, the transcript has timing and speakers, and
  the app stays responsive while it runs.
- **Status:** Not run on v2.

### H15 · Large batches and long recordings

- **Do:** Add 20+ files including a recording over two hours.
- **Pass when:** Media preparation finishes, the app stays usable, and memory/CPU stay reasonable.
- **Status:** Not run.

### H15a · Passage word timing on a real result (Add word timing / Repair all)

- **Do:** On Windows, open a real result with untimed passages (for example "Per og Kim") and
  use Repair all, then Add word timing on one passage whose interval is plausible.
- **Pass when:** Passages whose saved interval is far too short for their text show "Too short
  to time" and are not sent; Repair all runs every remaining unit without the "Local file
  operation failed" error; Activity moves from "Loading local models…" to "Aligning words…";
  timing appears for passages where speech is found, and the job message names any passages
  left unchanged. Only synthetic tests and an offline replay of the 20 November job cover this.
- **Status:** Not run.

## Editing and review

### H15b · Very large library stress test (later, after MVP v2)

- **Do:** Load a large public speech dataset from Hugging Face (any language) into a throwaway
  library: many files (for example 1,000+ short clips) plus some multi-hour recordings. Import
  the dataset's own reference transcripts as results, so scale can be tested without paying
  for transcription; transcribe only a small subset. Then upload, prepare media, browse, edit,
  search, export, back up, restore and delete at that size.
- **Pass when:** Nothing crashes or hangs; lists, Activity and the editor stay responsive; memory
  and disk use stay bounded; every export, backup and deletion completes.
- **Dataset:** to be chosen with a verified licence, pinned revision and hashes, downloaded
  outside the repository and never in routine tests. Candidates to check: LibriSpeech (many
  short read clips), AMI meetings (long, several speakers), Earnings-22 (long calls),
  VoxPopuli or Common Voice (very many clips).
- **Status:** Not run. Requested 20 November; not needed before the MVP v2 release.

### H16 · Editing a long real transcript

- **Do:** Edit, split, merge and delete passages in a 600+ passage transcript.
- **Pass when:** Each action feels quick enough for daily work, drafts and the scroll position
  stay put, and Undo merge/split still work.
- **Status:** Accepted for MVP 0.1.0 speed on 10 November; not re-run on v2.

### H17 · Typing, replay and other browsers

- **Do:** Replay passages while editing; type Danish characters with dead keys or an input method;
  repeat in Firefox and on a Mac (Safari/Chrome).
- **Pass when:** Highlighting follows speech, typing is never interrupted, the caret and native
  Undo behave.
- **Status:** Verified on Chromium/Windows only.

### H18 · Review marks and Working versions in real use

- **Do:** Review a real transcript with Not checked / Needs review / Checked and Next to review;
  set a Working version and export the project.
- **Pass when:** Counts and marks stay correct after edits and restarts; the result list shows
  "· Working" on the right result and the export uses it.
- **Status:** Not run.

## Exports and NVivo

### H19 · NVivo import on Windows and Mac

- **Do:** Import the app's NVivo exports into desktop NVivo on Windows (TSV) and Mac (text with
  timestamps and speakers).
- **Pass when:** Every passage arrives with its timestamps and speakers, and untimed exports are
  refused clearly.
- **Status:** Not run (synthetic acceptance pack only).

### H20 · Large exports with original recordings

- **Do:** Export a project with "include original recordings" totalling several GB.
- **Pass when:** The browser download completes, the ZIP opens, and the app stays usable while it
  is prepared.
- **Status:** Not run.

## Library, backup and restore

### H21 · Backup and restore to a new library

- **Do:** Make a backup from Library, restore it into a new library, switch to it; repeat on a Mac.
- **Pass when:** The restored library opens with every project, transcript, version and note.
- **Status:** Not run on a real library.

### H22 · Switching libraries and two browser tabs

- **Do:** Switch library while a second tab is open on the old one.
- **Pass when:** The stale tab is refused clearly and nothing is written to the wrong library.
- **Status:** Not run.

### H23 · Permanent library deletion on awkward folders

- **Do:** On a throwaway library, try deletion with a OneDrive folder, a file open in another
  program and a read-only file.
- **Pass when:** Blockers are shown and explained; nothing outside the library is touched.
- **Status:** Not run.

### H24 · Project deletion in Activity (later)

- **Do:** After the post-v2 change, delete a project with many recordings, press Keep project
  during "Stopping jobs", then repeat and close the app before the point of no return.
- **Pass when:** Only that project is inactive, Keep project keeps it, and closing the app early
  keeps it too (decided 20 November).
- **Status:** Not built yet. The v2 dialog can still be closed during the final step.

## Accessibility and browsers

### H25 · Screen readers

- **Do:** Use NVDA (Windows) and VoiceOver (Mac) to create a project, upload, edit, review and
  export.
- **Pass when:** Every control and dialog is announced by name, focus never gets lost, and errors
  can be recovered from.
- **Status:** Not run (automated name/keyboard checks only).

## Quality and institutional checks

### H26 · Danish and English accuracy

- **Do:** With approved recordings, compare transcripts against reviewed versions per model.
- **Pass when:** The measured accuracy, timing and speakers are acceptable for research use.
- **Status:** Not run.

### H27 · Institutional sign-off

- **Do:** CBS IT/DPO review of UCloud data classification, credentials, backups and licences
  (Spylls, dictionaries, models).
- **Pass when:** Written approval for public use.
- **Status:** Open.

## Hidden in MVP v2 (later)

- **Undo last change, History change list and Branches** (ADR 0180–0182): try on a real library
  before they are shown.
- **Spelling suggestions** (ADR 0185 and its redesign): dictionary download in Activity,
  on-demand suggestions and vocabularies.
- **Full GPU when no MIG is free** (ADR 0184, paid): the ask appears only when capacity reads
  agree, nothing switches without the click, and Retry returns to the MIG.

# CBS Transcriber

CBS Transcriber is being developed as a CBS-supported transcription service for
interview-based research. The aim is accessible Danish/English transcription, synchronized
review and research exports using UCloud and other approved processing options, with manageable
operating and support costs. See the [service purpose and pilot criteria](docs/cbs-deployment-proposal.md).

The [review workflow](#review-workflow) covers transcript editing, word timing, speaker labels,
versions, and recoverable result Trash. Trash hides results from ordinary choices and exports;
it is not permanent data erasure.

The app includes immutable transcript/notes history and a metadata-first methods
report. See [implemented history and its limits](docs/research-history.md) and the
[research workflow roadmap](docs/research-workflow-roadmap.md) for remaining provenance work.
The longer-term [AI analysis and NVivo roadmap](docs/ai-analysis-nvivo-roadmap.md) plans shared
UCloud AI Platform/OpenAI analysis, reviewed coding/summaries and REFI-QDA interchange;
these planned features leave advanced qualitative research in NVivo.
[Topics & sentiment research](docs/sentiment-topic-mapping-design.md) defines the separate
evidence/review workflow; automatic topic or sentiment inference is not included.

A local Python app for organising recordings, importing UCloud transcripts, transcribing
through OpenAI-compatible APIs, reviewing timed text, and exporting transcripts for NVivo.

## Install and start (Windows and macOS)

Install the versioned **GitHub release wheel with uv**. This route needs no Git, source clone,
Node.js or separate FFmpeg installation. The repository is
[CBS-HPC/cbs-transcriber](https://github.com/CBS-HPC/cbs-transcriber).

The MVP release is **v0.1.0**. A fresh isolated Windows wheel environment passed core startup,
media preparation, transcript import/export and playback-range checks. These checks used an
existing Python on the development PC; real clean-machine Windows and Mac acceptance remains
pending. See the [release verification](docs/verification-2026-11-10-mvp-release.md).

| Computer | Installation scope |
| --- | --- |
| Windows 10/11 x64, standard CPython 3.13 | Core app and CPU local inference packages. |
| Apple Silicon, macOS 14+, standard CPython 3.13 | Core app and CPU local inference packages; Apple GPU/MPS is not supported. |
| Intel macOS, standard CPython 3.13 | Core/API/UCloud, imports, review and exports; incompatible local inference packages are excluded. |

Run Terminal natively on Apple Silicon; Rosetta can select Intel packages. Other operating
systems, architectures and free-threaded Python are outside this release's installation guide.
MVP hides unfinished features; it does not reduce the dependency download. Allow about **6 GB**
for the installation, plus download/cache space, recordings and optional model weights.
Keep libraries and source checkouts outside OneDrive/iCloud.

### 1. Provide uv and Python once

First check whether uv is already available in your terminal:

```text
uv --version
```

If this reports **0.9.29 or later**, skip the uv installer below. If the command is missing
or older, use CBS-approved software routes on a managed computer; prerequisite installation
may need IT. The [official uv installers](https://docs.astral.sh/uv/getting-started/installation/) are:

Windows, in **PowerShell**:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS, in **Terminal**:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After installing uv, close and reopen the terminal and check its version again. Then install
Python once:

```text
uv --version
uv python install 3.13
```

Use **uv 0.9.29 or later**. Later uv releases still require installation acceptance; the
version floor is not a claim that every future resolver behaves identically.

If Windows still reports an older uv after installing a newer one, run `Get-Command uv -All`.
Conda or another installation may appear first on PATH. To use the official installer's copy
directly, run `& "$env:USERPROFILE\.local\bin\uv.exe" --version` in PowerShell and use that
same prefix in place of `uv` in the commands below. Do not reinstall just because another
copy appears first.

`uv python install 3.13` installs a uv-managed Python for your user account. It can be reused
by uv tools/projects; it does not replace your existing Conda or system environment. CBS
Transcriber gets its own isolated environment in the next step. See [uv's Python guide](https://docs.astral.sh/uv/guides/install-python/).

### 2. Install the matching wheel and constraints

Use the [v0.1.0 release](https://github.com/CBS-HPC/cbs-transcriber/releases/tag/v0.1.0).
This same command works in PowerShell and Terminal without a GitHub login:

```text
uv tool install --python 3.13 --no-config --link-mode copy --constraints "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs-transcriber-0.1.0-constraints.txt" "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs_transcriber-0.1.0-py3-none-any.whl"
```

Always use the wheel and constraints from **the same release**. uv creates its own isolated
app environment. The constraints carry the release's runtime version pins, UCloud client
wheel and supported CPU PyTorch wheel choices. They do **not** make `uv tool install` consume
the repository's `uv.lock` or establish its complete artifact-hash guarantees. Do not add a
global PyTorch index or replace the constraints with a hand-edited dependency list.
`--link-mode copy` installs independent file copies, avoiding Windows Cloud Files/hardlink
errors such as error 396. It uses more disk space than hardlinks; it does not affect
transcription speed. The terminal's current folder does not select uv's tool/cache folders.

If release access is still private, sign into GitHub in your browser and download these two
assets from the release page instead:

- `cbs_transcriber-0.1.0-py3-none-any.whl`
- `cbs-transcriber-0.1.0-constraints.txt`

Open a terminal in their download folder and run:

```text
uv tool install --python 3.13 --no-config --link-mode copy --constraints "cbs-transcriber-0.1.0-constraints.txt" "./cbs_transcriber-0.1.0-py3-none-any.whl"
```

A missing/private release can return 404. Contact the maintainer rather than putting a GitHub
token in a command. Installing Git or GitHub CLI is unnecessary for this browser-download route.
Installation downloads Python packages; it does not start transcription, contact your
transcription provider or automatically download inference model weights.

### 3. Start the app

```text
cbs-transcriber
```

The MVP release packages its profile. To select it explicitly, run `cbs-transcriber --profile mvp`.
The browser opens at **http://127.0.0.1:8765**. Keep the terminal open while you work;
press **Ctrl+C** there to stop. macOS may ask for Keychain access when you save API keys.
If the command is not found, run `uv tool update-shell`, reopen the terminal and try again.
Save edits and stop any earlier running copy before launching the newly installed app.
Only one process can open a given library; changing ports does not remove that restriction.

Optional: create a Windows Start menu or macOS Applications launcher, then exit without
starting the app:

```text
cbs-transcriber --create-shortcut --profile mvp
```

For a Desktop launcher, add `--shortcut-location desktop`. Creation is explicit and refuses
an existing same-name launcher. See [launcher options and removal](docs/installation-shortcuts.md).

### First start


- **Where your data goes:** new installations keep everything in one visible folder in your home
  directory: `CBS Transcriber/Library` (database, do not edit) and `CBS Transcriber/Projects`
  (your projects). If you already have a library elsewhere, the app finds it or asks which one to
  open; switch any time with **Library** in the sidebar. **Library → Backups → Create complete
  backup** (after finishing or stopping processing) seals the database, original recordings and
  available research evidence into `<library>/backups/portable-<id>/`; save its receipt
  elsewhere, because restoring needs its commit SHA-256. Restore always creates a separate new
  library. Keys and tokens are never included. UCloud storage/retention controls are technical
  previews: real UCloud transfers and CBS IT/DPO approval are still pending. See
  [complete library backups](docs/portable-backups.md). Some advanced backup/restore controls
  are hidden in MVP; see [Release profile](#release-profile).
- **Connect a transcription service** under **Settings**: an OpenAI API key, and/or a
  UCloud access token. Keys are stored in the Windows Credential Manager or macOS Keychain.
- **Optional:** a Hugging Face read token (Settings → Hugging Face) is needed only to
  download the speaker-detection model the first time. Accept the terms of
  pyannote/speaker-diarization-community-1 with the token's account, then **Check token**:
  it checks the token and terms and downloads and verifies the speaker model on this computer.

### Later starts, updates and removal

- **Start:** run `cbs-transcriber` or use the launcher. There is no need to reinstall Python.
- **Update:** save edits, finish or stop jobs, create a complete library backup and stop the
  app first. Obtain the next approved release's wheel and matching constraints, then repeat
  its installation command with `--reinstall-package cbs-transcriber`. This reinstalls the app;
  dependencies follow that release's constraints. Avoid an unqualified `uv tool upgrade`:
  it does not select the next approved wheel and companion file for you.
- **Reinstall v0.1.0:** use the same v0.1.0 assets and install command, adding
  `--reinstall-package cbs-transcriber`. `--force` also permits replacing executable names;
  use it only after checking that the conflicting command belongs to this app.
- **Launcher after an update:** recreate it if the installed environment was moved or
  replaced. Remove only the existing named launcher first; creation never overwrites it.
- **Remove the app:** stop it, then run `uv tool uninstall cbs-transcriber` and remove its
  optional launcher. Libraries, project recordings and separately cached models remain in
  their existing locations. Do not delete the library when removing the app.

An update does not require a real transcription or model download to verify installation.
Never synchronize or replace the environment of an app that is still running.
[uv's tool guide](https://docs.astral.sh/uv/guides/tools/) explains isolated tool installation
and upgrades.

### Developer/source fallback

Developers who need a source checkout can provide Git through an approved route and clone
into a fresh writable local folder outside OneDrive/iCloud:

```text
git clone --branch main https://github.com/CBS-HPC/cbs-transcriber.git "CBS Transcriber"
cd "CBS Transcriber"
uv sync --locked
uv run --locked cbs-transcriber --profile mvp
```

`main` is development source, not a promise of the published release's exact code. Use a
verified release tag/commit when reproducing that release. Source launchers use
`uv run --locked cbs-transcriber --create-shortcut --profile mvp`.
To update a stopped, backed-up clone, use `git pull --ff-only`, then `uv sync --locked`.
Preserve local changes; do not reset them or copy a `.venv` from another installation.

A [source ZIP](https://github.com/CBS-HPC/cbs-transcriber/archive/refs/heads/main.zip) is a
secondary source fallback without Git: extract into a fresh local folder containing
`pyproject.toml`, `uv.lock` and `src`, then use the same `uv sync --locked` and `uv run` commands.
Update ZIP installations in a fresh folder, preserving external libraries and projects.
See [earlier source-wheel checks](docs/verification-2026-10-29-ucloud-release-wheel.md) for
their offline package-plan evidence; these do not establish fresh wheel-install acceptance.

### If installation or startup stops

| What you see | Next step |
| --- | --- |
| Release 404 or sign-in prompt | Confirm release availability with the maintainer. For private access, download the assets through your signed-in browser; never embed a token in a command. |
| `uv` is not recognized | Reopen the terminal after installation and check `uv --version`; ask IT if the prerequisite is restricted. |
| uv still reports the old version | On Windows, run `Get-Command uv -All`; use `& "$env:USERPROFILE\.local\bin\uv.exe"` explicitly if Conda or another copy appears first. |
| Hardlink failure, Cloud Files error 396 | Repeat the matching release install command with `--link-mode copy`. If a partial install left the app's executable, add `--force` after confirming it belongs to CBS Transcriber. Do not delete your library or clear the whole cache. |
| `cbs-transcriber` is not recognized | Run `uv tool update-shell`, then reopen the terminal. |
| Wheel or constraints file not found | Open a terminal in the folder containing both matching release assets and check their filenames. |
| No compatible package or constraints refuse installation | Check standard CPython 3.13, supported architecture and macOS 14+ for Apple Silicon. Do not remove the constraints to force installation. |
| Permission denied | Use a writable user location and ask IT about managed-device restrictions. |
| Browser did not open | Keep the terminal open and visit `http://127.0.0.1:8765`; read any terminal startup error first. |
| Port 8765 is occupied | Use the already-running CBS app. If a different application owns that port, use `cbs-transcriber --port 8766`. |
| This library is already open | Save edits, finish or stop jobs, and press Ctrl+C in the earlier app's terminal. Closing its browser tab alone does not stop it. Then start the installed app again. Do not delete the library lock file. |

After installation, creating projects and importing/reviewing existing transcripts needs no
GitHub login or transcription-service key. Configure service keys under **Settings** when
requesting transcription. Optional model preparation requires the selected runtime setup.

### Options

```text
cbs-transcriber --port 8766 --no-browser
cbs-transcriber --choose-library
cbs-transcriber --data-dir 'D:\My local transcription library'
cbs-transcriber --profile mvp
```

For a source checkout, prefix these commands with `uv run --locked`.

### Release profile

A release profile decides which unfinished features are offered ([ADR 0145](docs/decisions/0145-release-profile.md)).
`full` (the default on `main`) offers everything. `mvp` hides passage Re-transcribe, Topics &
Sentiments, the Research Report, CBS Runtime transcription, UCloud word timing/speakers and
repairs, Automatic rules, the Python executable field, Realign after saving, comparison Use
wording, UCloud backup upload/retention, the restore panel and custom/UCloud vLLM providers.
Existing results, exports and Activity recovery controls stay available; hidden new work is
refused with a plain message.

The `--profile` option wins over `CBS_TRANSCRIBER_PROFILE`, which wins over the packaged
`src/cbs_transcriber/catalogs/release.json`. A release branch builds the MVP variant with one
command, then commits the file:

```powershell
uv run python -m cbs_transcriber.release_profile mvp --label "MVP rc1"
```

Settings names a non-full profile for bug reports. Run `--profile full` (for example to restore
a backup) on an MVP build; `--create-shortcut --profile mvp` keeps the choice in the shortcut.

### Technical notes

The browser opens at **http://127.0.0.1:8765**. Keep the terminal running; Ctrl+C stops the app.
The app opens the library you used last. If several libraries are found on this computer and
none was used before, it first asks which one to open, including libraries a packaged app such
as Codex kept in its own app-data folder. Switch libraries any time with **Library** in the
sidebar (finish or cancel running jobs first). `--choose-library` always asks at start.
`--data-dir` opens a library just this once; it does not change which library opens next time.
Dependencies and FFmpeg are installed in the local `.venv`; Windows x64 and native Apple
Silicon also receive WhisperX 3.8.6 and CPU PyTorch. Intel macOS excludes that local inference
stack. No Node.js build step is needed. The first installation downloads substantial inference packages
and can take several minutes. Model weights are separate and never downloaded by installation.

```powershell
uv run cbs-transcriber --port 8766 --no-browser
uv run cbs-transcriber --choose-library
uv run cbs-transcriber --data-dir 'D:\My local transcription library'
```

After updating the app, save open edits, restart the server from the updated checkout,
and refresh the browser tab. HTML, application JavaScript and CSS use `Cache-Control: no-store`
so refreshed tabs load the current interface.

## macOS notes and deployment planning

Install as described in [Install and start](#install-and-start-windows-and-macos).
Apple Silicon's locked local inference stack requires macOS 14+ and runs on CPU;
Apple GPU/MPS acceleration is not implemented. Intel Macs install the core app with
OpenAI/UCloud transcription, import, review and exports, excluding incompatible pinned
WhisperX/PyTorch wheels. Local enhancement explains that limitation and offers UCloud;
an alternate Intel inference runtime is not validated.

Both Mac dependency plans pass an offline cross-platform dry run, and native macOS folder
selection has mocked cancellation/Unicode tests. Actual Mac installation, Keychain, playback
and cancellation acceptance remain pending. Windows remains the locally exercised platform.
Optional launchers for wheel and source installations use `--create-shortcut`; see the
[shortcut guide](docs/installation-shortcuts.md). A signed installer and a shared CBS
ChatGPT Work skill/plugin remain in the [deployment discussion](docs/cbs-deployment-proposal.md);
no installer/plugin is released.

## Model catalogs and native UCloud setup

CBS Runtime also offers fixed-Danish **Edda v0.2 turbo**, **Edda v0.2-large** and **Røst v3**.
They require explicit preparation of their pinned source and converted model files; selecting
a profile does not download models or start a GPU job. Their implementation has synthetic
conversion/checkpoint coverage; real inference, comparative quality and CBS licence clearance
remain separate acceptance tasks. Existing Whisper choices and saved results are unchanged.

New WhisperX requests retain valid timing estimates, including low-score intervals, and keep
uncertainty hints for review. Word coverage and complete-passage coverage are displayed separately;
neither measures accuracy against the audio. Existing frozen jobs retain their original timing
policy. Automatic second-pass alignment remains deferred.

**Settings** has OpenAI API, UCloud, Hugging Face and Local Processing tabs. Connect OpenAI with
an API key or UCloud with its token, then choose enabled models. UCloud's **Available models**
lists models by Transcriber and CBS Runtime. **Advanced settings** holds
Protected eligibility and connection overrides. Custom and UCloud vLLM
configuration is deferred; existing saved configurations and frozen jobs remain preserved.
OpenAI and UCloud Transcriber offer their model catalog after saving the connection. Choose
the model when starting transcription. Under **Available models** in the basic tab, uncheck
models to hide them from new-run choices. Existing results and queued jobs are retained.
New setups initially select GPT Transcribe, UCloud Whisper large-v3 and Danish Edda large; the
other models can be enabled here. GPT Transcribe returns text, so word timing and speaker labels
require separate enhancement. These choices are defaults, not quality guarantees.
These tabs have no model or Saved provider/Add/Delete selectors. **Check model availability** appears
directly in the OpenAI tab. Custom and UCloud vLLM retain named configurations.
Setup/save errors appear inside the dialog and retain unsaved fields. UCloud discovery reports
the failing step and safe HTTP/connection details without displaying upstream response bodies.
UCloud vLLM can be configured offline; saving does not deploy a speech model. Connect a running
speech endpoint under Advanced settings until the hosting workflow is available.

[OpenAI and UCloud Transcriber presets](docs/provider-models.md) share an editable JSON file.
Native [UCloud Transcriber Batch](docs/ucloud-transcriber.md) now supports token-first discovery:
unique project/app/GPU/storage choices fill automatically, with named choices for ambiguity.
New native Transcriber jobs use a **B200 1g MIG**, with no automatic GPU fallback;
unavailable or ambiguous allocations block new work. Template-bound native configurations
are rejected for new work; reconnect without the template to use the fixed MIG policy.
Reconnect older configurations before starting new jobs; existing frozen runs retain their identity.
Native token ownership metadata can resolve the project automatically; unavailable metadata
retains explicit selection. Saved project choices are preserved.

Previously saved WhisperX language settings remain effective for new full transcriptions;
the compact model list does not expose a per-model language control. A model with no saved
choice uses the saved Local Processing language. Existing **Automatic (experimental)** settings
remain supported for full transcriptions and prepare both aligners when
timing is requested; unsupported or uncertain language retains the transcription with passage
playback and an explicit missing-timing outcome. This is one recording-wide language decision,
not mixed-language alignment. The detector threshold is uncalibrated; quality evaluation and
Automatic for existing-text/passage repairs remain pending. Existing queued jobs and other
models retain their settings. New transcription requests use the recording language if set,
then the project default, then existing model settings. Set a default when creating a project
or in Project Settings, and override it on a recording card or in its transcript workspace.
The searchable catalog lists standard base-language names, but model support varies. Known
incompatible choices are refused; other choices are unverified and may fail at preflight or at
the provider. New timing, realignment and repair requests use the recording language, then the
project default, then the previously saved Local Processing language. When a stage requires a
language, unsupported choices are reported rather than silently replaced. A stored language is
a configured choice, not detected evidence.

Application discovery checks the group's Batch flavor even when search shows the interactive
default. The native model list offers the supported catalog presets.
Advanced templates remain supported. New configurations default to remote input/output
cleanup after validated local save: the run folder moves to your UCloud Trash (UCloud storage
offers no permanent per-file delete), so empty Trash in UCloud to free the space. Activity shows
any failure reason with a cleanup-only retry. A pending receipt means removal is unverified;
it does not prove files are still present. Selecting several recordings for the same UCloud
model starts **one** shared UCloud job; each recording still receives its own result.
The app uses your existing released UCloud API package as a dependency.

If `uv sync` reports Access denied when removing an empty
`cbs_transcriber-<version>.dist-info` folder in a OneDrive environment, check that
specific folder's Read-only attribute after stopping the app. Clear the attribute
on that folder, remove it only if empty, and rerun `uv sync --locked`. Keep the
repair confined to `.venv`; retain filesystem permissions and project/library files.

## Review workflow

1. Create a project, choose its recording classification and storage folder, then add
   audio/video files. Conversion runs in a local background queue.
2. Select a recording and import its UCloud JSON/CSV, or open Settings and configure an API.
   Choose **Provider**, then **Model**, beside **Transcribe selected** or
   in recording review. One saved UCloud Transcriber connection exposes all its supported
   catalog models. Transcribe with another model to create an additional comparison result.
3. Click a timed word or passage to seek. Use the replay button to play just that passage.
4. Open **Edit** or double-click a rendered word for **Text** editing. Unchanged words retain
   exact timing; a uniquely matched replacement keeps its interval, and added words stay untimed.
   Ambiguous edits refuse saving and retain the draft so you can make a smaller change.
   Choose **Word and timing** to select a word by number and text, use **Edit word** to correct
   it, or change its start/end time. Changing modes does not realign text. **Restore original**
   recovers the passage baseline. Use **Merge up/down** to join adjacent passages, or **Delete passage** to
   remove one from the current result (earlier text stays in History). Choose a speaker if their
   labels differ. Use **Edit**, place the cursor between words and choose **Split at cursor**. When a
   passage is only partly timed, the split uses the timing of the words next to the cursor;
   if they have none, play the passage and pause where it should split first.
   Save changed text first. Word-timed passages retain real word intervals; otherwise position
   playback at the intended boundary within the passage before splitting. Untimed passages
   stay untimed. Usable partial word timings survive structure changes, including corrected words;
   each split half is checked separately for complete timing. Earlier structure and originals
   remain available in **History**; each split
   half has its own restore baseline. **Undo merge** and **Undo split** restore proven unchanged
   structures without reverting edits elsewhere. **Deleted passages** lists proven deletions
   separately with **Restore passage**; it restores one passage while preserving later edits.
   Changed or missing placement anchors can require restoring consecutive deletions newest first.
   When an individual inverse is unavailable, the exact earlier version remains in History.
   To bring an enhancement's timing or speakers back into the source you are reviewing, open
   **Versions → Results**, choose **Preview merge** beside that enhancement, inspect the safe
   updates and skipped/conflicting passages, then choose **Apply safe updates**. Your wording,
   original passages and named voices stay intact. An unknown incoming speaker does not erase
   an established label. The enhancement stays unchanged, and the merge adds a new History
   entry with verified parent references in the research report. Results whose creation changed
   passage boundaries or wording cannot be merged automatically; compare those manually.
5. For a video, switch **Audio / Video** without losing position, speed, or playing state.
6. Unnamed speakers appear as Speaker_01, Speaker_02, ... (as in UCloud Transcriber); the
   speaker list shows only the result under review. Rename speakers and choose **NVivo for Windows**, **NVivo for Mac**, or **Readable text**.
   Both NVivo formats retain timed passages; readable text supports speaker/timestamp grouping
   and starts with `INTERVIEW`. All formats preserve the first and last passages.

**Collections** group a project's recordings (interview waves, sites, pilots): the chips above
the cards show **All**, each collection, **Unsorted** and **＋ New collection**. Select cards and
use **Move to** to place them; uploads made while a collection is shown go into it. While a
collection is shown, the sidebar offers **Collection Settings** (rename, notes, delete; deleting
keeps the recordings as Unsorted), and Export Settings and the Research Report cover only that
collection. Project ZIPs put each collection in its own folder. Files on disk never move.

Each recording card includes **Description / notes**. Expand it to edit and save recording
metadata; the same notes are available inside recording review. Notes have their own revision
and history, separate from transcript edits.

On a recording's page: **Review result** with **Versions…** (tabs **History**, this result's
saved versions, and **Results**, the recording's other transcriptions and Trash), then
**Compare with**, then the row that chooses **Provider** and **Model** and starts **Transcribe**.
Results can be moved to Trash and restored there. Trash retains text, timings, notes, history,
source files and receipts; removing the active result selects another live result if one exists.
The sidebar holds **Export Settings…** and **Topics & sentiment…** for the open recording, and
**File Settings** (download the original, **Import transcript…**, delete the recording); on a
project page it holds **Export Settings…**, **Research report…**, **Topics & sentiment…** and
**Project Settings**. Rename a recording with the pencil beside its title.

Each provider run and transcript import is a separate saved result. Choose **Review result**
to edit or export it; choose **Compare with** to display another result beside it. Both views
follow the same audio timeline, and selecting another result retains playback position/speed.
The comparison pane is read-only. Edits and speaker names belong to their selected result.
**Play transcript**, beside **Follow playback**, plays continuously from the current position;
per-passage **Replay** stops at that passage's end. Follow playback scrolls to the current
passage; enabling it immediately reveals the passage at the current playback position.
Word timing is not required for passage replay. If neither passage bounds nor a verified source
interval exists, **Replay** plays the full recording, as explained by its tooltip; it does not
locate or highlight the untimed fragment. Saved timestamps and exports are unchanged.
Text editing keeps **Replay passage** without a duplicate text panel. In **Word and timing**,
a compact **Saved text for playback** preview follows reliable word timing without moving the
input cursor. Unsaved wording is not assigned invented timing.

Use **Not checked**, **Checked** and **Needs review** to track manual passage review. The
counter shows checked progress for this saved result, independently of model uncertainty and
Working versions. A changed checked passage needs review again; unrelated marks remain.
**Show uncertainty** (above the transcript) flags the passages and underlines the words the
model was least sure about, relative to this transcript (lowest 5/10/20%), with the signal in
the tooltip; **Next to review** jumps between flags. Signals come from the provider output
(Whisper/WhisperX decoding and alignment scores, gpt-4o token probabilities). They are not
accuracy measures, and editing a passage removes its marks.
**Find in both transcripts** highlights literal words or phrases in both panes
as you type, keeping all aligned passages visible. In a single transcript, search also
filters passages. Clear the search to remove its highlights.
Matching passages align vertically using words and phrases across the whole transcript.
Matching wording is underlined; amber marks differences. Partial matches have a review note.
Split/combined passages can share a row, and gaps show content without a match. Alignment
starts automatically when you choose a comparison result; there is no manual alignment step.
Scrolling is linked when the panes are side by side; narrow windows stack them.
Matching is local and approximate: it never changes text, speaker identities or audio timing.
For a one-to-one matched row, **Use wording** opens both complete passages for explicit review.
**Use this wording** copies only the selected source passage's literal text into your reviewed
result. Its speaker and audio bounds stay; word timing becomes stale until separately aligned.
The other result stays unchanged. History records the exact source version and original text
remains recoverable. Ambiguous multi-passage rows require ordinary manual editing.
New provider results select themselves only when the recording has no active transcript;
an existing reviewed result stays selected until you choose another one.

Edits save explicitly with a visible status. Conflicting edits in another browser window
are rejected rather than silently overwriting saved work. Original uploads remain recoverable.

Word timing and speaker labels use the app's included WhisperX runtime: the passage tags and
**Add speaker labels** above the passages start them with one click (whole-recording runs create
a new result and keep the reviewed one intact). **Settings → Local Processing** holds the
machine settings, saved automatically: processing device (**Automatic** uses
a GPU when this computer has one; the app checks once at startup), permission to download missing
local models when a job needs them, the Python runtime (Advanced), this computer's runtime
status and the **Rules for Automatic**. **Settings → UCloud** holds cloud runtime status,
preparation controls and permission to prepare models for explicit UCloud processing. New or unset
preferences allow missing pinned local model downloads and explicit UCloud preparation. Saved opt-outs are
preserved. Opening Settings, saving preferences and starting the app do not download models
or allocate UCloud compute. With saved preparation permission, manually selecting **Check UCloud
again** can start base runtime preparation; passive checks do not. Automatic placement still requires an already-prepared UCloud
runtime; it does not prepare one. Long untimed passages use
local phrase recognition to find bounded alignment windows. Reviewed words remain unchanged;
uncertain passages stay untimed and partial results are labelled. See
[runtime setup and limitations](docs/local-enhancement.md).
**Run processing → Automatic** (the default) runs short recordings on this computer (up to 10
minutes, 3 with speaker labels, longer with a local CUDA GPU) and longer ones on UCloud when your
connection is allowed and prepared; otherwise locally, more slowly. Activity shows each choice.
**Rules for Automatic** (Settings → Local Processing) sets your own limits per library: minutes of word
timing and of speaker labels to run here, the local-GPU factor, and whether a longer recording
runs here anyway or is not started when UCloud is unavailable. A limit of 0 sends everything to
UCloud when it is available. Queued jobs keep the place and reason they were given.
[UCloud enhancement](docs/ucloud-enhancement.md) runs through your Settings UCloud
connection (a batch job, no SSH). Preparing its reusable runtime can download several GB,
take 20+ minutes and incur UCloud charges. With **Prepare UCloud models automatically when
needed** enabled, an explicitly selected UCloud job can prepare it; with that preference
off, the app asks first. The connection's Protected approval applies.
The Local Processing tab's check reports CUDA access, GPU names and memory from the selected
interpreter; it loads no models, and a saved explicit device choice is never changed for you.
When a check confirms no CUDA GPU, device and GPU-factor controls are disabled; saved preferences
are retained for a later GPU check. An unavailable check does not claim that no GPU exists.
Automatic language detection for local alignment is still pending. If neither a recording nor
its project has a language, the saved legacy Local Processing language remains the fallback.
New WhisperX transcription and passage re-transcription results retain evidence of the
configured decoder language, prepared input, model revision and processing window. Research
reports distinguish this configured choice from measured language detection; older cached
results without that evidence remain explicitly unknown. This does not enable Automatic
language selection throughout the app or change existing Danish/English settings.
**Re-transcribe** on a passage starts at once (WhisperX large-v3-turbo, Automatic place).

After media preparation completes, select a provider and optionally check **Add word timing** or
**Add speaker labels** beside **Transcribe selected** or in recording review. Select one or
several recording cards and use **Transcribe selected**; cards have no separate start button. Features
included by the model stay checked and locked. Missing features open the Local/UCloud processing
settings before starting. The same action handles single-file and multi-file runs.
Configure processing once and start the batch. Models' native capabilities are retained; only missing
features get an enhancement job. Keep the app and computer running overnight. Each file checkpoints
independently, and enhancement retries keep successful ASR output and any reviewed result.
No OpenAI Batch API is used. Models and access tokens must be prepared first; UCloud also needs
a prepared remote runtime. An alternate local CUDA environment can be selected explicitly.

Word-timed results show a count and amber markers for passages without usable word timing, including
comparison views. Rewritten passages explain why timing was invalidated. Other result types use
their normal passage/untimed labels. Reliable words within incomplete passages still highlight;
uncertain words receive no invented timing. Partial alignment keeps known sentence replay bounds.
**Add word timing** queues one passage locally as a version of the same result; **Repair all**
queues eligible passages together. An untimed passage is searched only between timed neighbours
without storing that gap as its timing. A passage too long for individual repair can instead
start a whole-recording enhancement as a new result. Open edits are retained.
Older split repairs offer **Replay source passage** where an exact frozen source interval is available.
Enhancement saves a separate result: choose **Review enhanced result** to use its timings for playback.
**Re-transcribe** (a passage action in Free text mode) or **Re-transcribe flagged** (in the
uncertainty controls) asks WhisperX for new wording of just those passages, on this computer or
UCloud. Each passage's own audio is used (plus half a second either side; words centred outside
the passage are dropped), the new wording is aligned, and one new result is saved in which every
other passage is unchanged, so **Compare with** shows exactly what changed. Passages need a known
audio interval of at most 60 seconds; only audio is sent, never your reviewed text.
**Realign edited passages** (beside Edit mode, off by default) gives an edited passage new word
timing on this computer after you save it. The timing is added to the same result as a new
version that History can undo; the text you wrote is never changed. Passages without audio
bounds, or longer than a minute, still use **Add word timing**.
Enhancement works from the exact version that existed when it was queued. If you keep editing
the source meanwhile, Activity and the enhanced result say so; those later edits stay in the
source result and are not merged automatically.

For offline method comparison with reviewed Danish/English word/speaker annotations, use
[reviewed evaluation](docs/reviewed-evaluation.md). It reports WER/CER, timing and speaker
coverage, and explicitly supplied runtime/memory measurements. Real model baselines remain pending.

**Versions… → History** previews immutable reviewed versions without changing the active transcript.
**Restore as newest version** brings an earlier version back as a new version; nothing is removed.
The first result becomes the Working transcript automatically. Processing a selected version can
make its successor Working when the source and designation remain unchanged; a newly transcribed
alternative otherwise stays an alternative.
Use **Mark Working transcript** in Versions/History to choose an exact saved version for research
exports. Choosing a different Working version while processing runs keeps that choice.
Whole-project exports use the designated Working versions by default and list recordings
without a designation as skipped. The result open for review can be a different alternative.
Projects also offer a metadata-first **Research report…** with exact version hashes, known
processing/review lineage and explicit provenance gaps. Transcript/speaker text is included only
when selected. Captured language evidence distinguishes configured decoding from model-reported
Automatic decisions, including unavailable alignment and unknown model pins; it does not claim
complete word coverage or measured detection accuracy. Notes, hidden originals, media and
credentials are excluded. See
[research history and report limits](docs/research-history.md). Migration/rollback tests use
synthetic libraries; the existing library was also backed up and upgraded on 22 October 2026,
with every prior table fingerprint and saved credential flag preserved.

New JSON/CSV imports also record the exact original file's SHA-256 and byte count in their
history and research reports. Edits retain that import origin. Reports use source aliases and
exclude original filenames and raw files; they do not recheck whether a retained file is still
available. Older imports without a receipt remain unknown. This is partial provenance, not a
complete backup or evidence of which transcription model produced an imported file.

**Topics & sentiment…** is a local research prototype for approved topic codebooks, manual
evidence coding and review of imported suggestions. Runs retain exact transcript/codebook
versions; quotation highlights, older-source warnings, human review history and separate
reviewed-evidence JSON export preserve those links. It does not execute a sentiment model
or change the transcript. See the [prototype guide](docs/topic-evidence.md). Schema 7 adds
annotation history. These features were merged into main and activated on 22 October 2026.

## Importing exports into NVivo

Open **Export Settings…** for a recording or project. A compact checklist selects project
recordings, and another selects one or more output formats. Project exports use Working versions
by default, with reviewed/all-result choices available. The ZIP uses recording-name folders;
its report identifies each exported or skipped result/format. **Include original recordings** is
unchecked by default; select it to put each selected original beside its transcripts, or choose
no formats for an originals-only ZIP. Recordings without a transcript remain visible with a reason
and become selectable when originals are included.
Multiple formats for one recording are packaged together too; a single-format recording download
keeps its ordinary file when originals are excluded. Empty recording selections, or no formats
without originals, disable export.
Exports use saved changes. Save a draft before opening the dialog.

ZIP preparation runs in **Activity**. Close Export Settings and keep working, then choose
**Download ZIP** on the ready card. **Cancel preparation** stops an unfinished ZIP;
**Discard ZIP** removes its temporary download, preserving your recordings and transcripts.
Keep the app running until preparation and download finish. Prepared ZIPs are temporary and
are removed when the server stops; they are not library backups.

First import the matching original recording into your NVivo project, then open its transcript
import command. The app's export formats differ by platform:

- **NVivo for Windows (.txt)**: tab-delimited `Timespan`, `Speaker`, `Content` columns.
  Open the media, turn on **Edit**, then **Import Rows**. Choose **Tab Delimited Line**,
  tick **File includes header row**, and map the three columns to Timespan, Speaker
  (create this custom field if necessary), and Content.
- **NVivo for Mac (.txt)**: speaker-colon paragraphs with F5-style `#hh:mm:ss-d#` timestamp
  markers. Open the media, turn on **Edit**, then **Import → Files → Transcript Rows**.
  Mac's importer can interpret colon-space inside transcript text as a speaker label;
  review the imported speaker fields. Follow the version's requirements for an empty transcript.
- **Readable text (.txt)**: the supplied scripts' INTERVIEW document workflow, with consecutive
  speaker or timestamp headings. This is readable document text, without the Windows
  media-transcript column structure.

Choose **UTF-8** in NVivo if encoding detection needs correction. Platform exports include a BOM,
use tenths of a second (NVivo's documented precision), and flatten line breaks/tabs inside a
passage into spaces. Saved edits, original text and full-precision timing remain unchanged.
Each native-format row/passage keeps its own speaker and timing, so grouping applies only to
readable text. Missing start times block native transcript export with an explicit message;
**Readable text** always retains untimed passages. No word alignment is invented.

Existing API links without a `format` parameter and saved project-folder snapshots keep the
readable format. Explicit downloads use `format=nvivo-windows`, `format=nvivo-mac`, or
`format=text`, together with the selected `result_id`.

Format and browser tests follow the official [Windows import guide](https://help-nv.qsrinternational.com/20/win/Content/files/import-audio-video-transcripts.htm)
and [Mac import guide](https://help-nv.qsrinternational.com/20/mac/Content/files/import-audio-video-transcripts.htm).
Actual imports in the desktop NVivo applications still need manual validation.

Export Settings also offers [SRT subtitles](https://www.loc.gov/preservation/digital/formats/fdd/fdd000569.shtml),
[WebVTT subtitles](https://www.w3.org/TR/webvtt1/), CSV and JSON. Subtitles require valid start/end
times for every passage and produce one cue per passage; long passages may need subtitle editing.
CSV keeps text/speaker rows, with an apostrophe before formula-like text for spreadsheet safety.
JSON keeps exact current text, speaker names and usable words, excluding original text and provider
settings. Readable text, CSV and JSON can retain untimed passages. A current-view export is not a
full library backup. **Word document (.docx)** gives one paragraph per passage (start time,
speaker, text) for comments and tracked changes in Word; changes made in Word do not flow back.

## Transcription providers

Settings manages named provider configurations. Use **Add provider** to save another endpoint
or model, then select it from the dropdown beside **Transcribe selected**, beneath a recording,
or in its editor. The bulk dropdown remembers the default configuration; individual recording
choices are saved separately. Bulk transcription uses its displayed dropdown selection.
Choose another provider and transcribe again to create a separate result. **Compare with**
selects an existing result for review beside the current one.

Custom and UCloud vLLM providers are deferred: their saved configurations, results and frozen
jobs are kept, but they are not offered for new runs. **Hugging Face** stores the shared read token. WhisperX word timing
needs no token; speaker labels need cached pyannote models, or a token with accepted model terms
to download them. **Check token** checks the token and the model's terms and, once access is
granted, downloads the pinned speaker model to this computer (retrying a dropped connection or a
briefly locked file) and verifies every file. UCloud speaker runs copy that verified model to the
UCloud runtime before they request a GPU. OpenAI offers
`gpt-transcribe`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `gpt-4o-transcribe-diarize`,
and `whisper-1`. The app selects each known model's supported response format automatically,
including when retrying older jobs with mismatched capabilities. The first three supply JSON
text without reliable word timestamps. Whisper supplies word timing; diarize supplies speaker
segments. GPT Transcribe uses the `languages` request field for a language hint.
Custom backends keep editable endpoint/model/capability controls. OpenAI selections show the
provider and model, for example **OpenAI whisper-1 (Word timing)**.

**UCloud Transcriber** submits native Transcriber Batch jobs through the released
`ucloud-workflow` **v0.1.9** client. Configure a UCloud API token, project ID, known-good
batch template job and existing writable UCloud folder. **Check UCloud setup** reads metadata
without uploading audio or starting a job. **Transcribe** uploads the original, uses the
verified native allocation, monitors the remote job and imports its JSON as an independent saved result.
New native connections request a **24-hour maximum**; existing saved limits and submitted jobs
stay exact. Change **Maximum job hours** to choose another limit for future requests.
Activity may include last-observed processing notes from the batch's log. These are separate
from UCloud job status and locally saved results; Retry reconnects to the same saved allocation.
See [native UCloud setup](docs/ucloud-transcriber.md) for parameter mapping and recovery.

**UCloud vLLM** connects to a self-hosted Hugging Face speech model using its deployed
HTTPS endpoint, HF model ID and vLLM serving key. Use **Add model** for separate saved models;
the offline Danish/English catalog fills pinned revisions and suggested response settings.
The served ID equals the HF owner/model name. **UCloud & Hugging Face setup** has separate
fields for the UCloud server/project, UCloud API token and HF token.
Setup tokens use separate OS credential-store scopes and never authenticate audio
requests. Configurations can be saved without a serving URL, but cannot transcribe until
that endpoint and its serving key are supplied. Temporary hosting needs no availability check
to configure a model. Catalog candidates do not imply a validated deployment.
See [catalog usage and provenance](docs/model-catalog.md).
See [UCloud vLLM setup](docs/ucloud-vllm.md) for requirements and the current hosting limit.

**Check model availability** in the OpenAI tab reads `/models` with the saved endpoint key
or the key currently entered. It does not upload audio, start transcription, or save a draft
key. **Listed** means the model is visible to this key; **Not listed** means it was absent
from the list. Neither verifies billing, quota, or a successful transcription request.
Failures report HTTP status and safe protocol fields without returning upstream error text.
API model availability/pricing can change; see the [official OpenAI documentation](https://developers.openai.com/api/docs/guides/speech-to-text).

Projects can be **Standard** or **Protected**.
The creation dialog requires a choice. Rename a project with the pencil beside its title. Changing the classification
below the title saves immediately. Existing projects retain
Standard classification until you change it.
Protected projects list only providers allowed for Protected projects in provider settings.
New UCloud configurations default to allowed; OpenAI and Custom default to blocked. Override
this per configuration; existing configurations and saved choices retain their policy.
This applies to local endpoints too. The API enforces eligibility when selecting,
queueing, retrying and before each upload. Revoking approval, deleting a provider or changing
its endpoint/model blocks pending protected work. It cannot recall requests already uploaded.
Classification changes wait for running transcription to finish or be cancelled. Import,
review and export remain available without an approved provider. Approval records your choice;
it does not certify a provider against CBS policy.

On upgrade, the three former protected classifications become **Protected**. A schema-3 backup
is saved first, and transcripts, recording paths and queued job settings remain intact.
Only providers previously approved for all three former classifications keep approval
automatically; partial approvals need explicit approval again for the combined category.

Each configuration accepts a base URL ending in `/v1`, model, language, and response capability:

- **Word timestamps**: defaults to OpenAI `whisper-1`, requesting `verbose_json` with
  word and segment timestamps.
- **Speaker labels**: OpenAI `gpt-4o-transcribe-diarize` with `diarized_json` and automatic
  chunking. This supplies segment timing, not word timing.
- **Segment timestamps**: compatible speech backends using `verbose_json` without
  word-granularity parameters. Highlighting uses only timings actually returned.
- **Plain JSON**: compatible backends that return text without timing. Playback remains
  available but timed highlighting and timestamp seeking require timing in the response.

For the configured OpenAI models, `whisper-1` provides word timing, while
`gpt-4o-transcribe-diarize` provides speaker labels and passage timing. `gpt-transcribe`,
`gpt-4o-transcribe` and `gpt-4o-mini-transcribe` return text without those annotations.
Word highlighting requires actual word timestamps; passage timing highlights a whole passage.
Imported transcripts can contain both speaker labels and word timing. Comparison alignment
does not generate audio timing or combine the outputs of separate models.

Saved API keys use the OS credential store (`keyring`). An unsaved key can be kept for the
current server session. `OPENAI_API_KEY` is also accepted for the default OpenAI endpoint.
Keys are never returned in API responses, placed in browser storage, or written into Git.
Changing an endpoint does not copy another endpoint's key. Configurations at the **same
endpoint share that endpoint's key**, which is useful for Whisper and diarization presets.
Deleting a configuration retains saved transcript results and its shared endpoint key.

Selecting **Transcribe selected** sends audio to the displayed configuration. Comparing providers
sends audio to each selected endpoint/model and incurs each provider's ordinary charges.
Queued jobs retain the provider settings selected when queued, even if you later edit/delete
the configuration. Selecting different saved results requires no new API request.
There is **no OpenAI Batch API, summary, or cleanup integration**.
Compatible endpoints must implement `/audio/transcriptions` and the selected response format.

Long recordings are converted to mono 64-kbit MP3 and split into bounded chunks near silence
where possible. Timestamps are offset back into the original timeline. Speaker IDs from
separate chunks are labelled by part because identities cannot safely be assumed identical.

The queue persists completed chunks. If the server stops during an API call, that job is
marked interrupted and waits for an explicit retry. An uncertain request may have reached
the provider; retrying that chunk can incur another charge. Cancelling stops local work
and future requests; it cannot revoke work already accepted by the provider. Different provider
jobs can queue for the same file; duplicate active jobs for the same configuration are rejected.
Retrying a failed job retains completed chunks and creates at most one result for that job.

Owned or uncertain UCloud allocations retain their recovery journal and Activity actions
until stopped, observed terminal or explicitly reconciled. Recording/project deletion is
blocked while remote work remains unresolved. Activity can reconnect a manually found job
only after checking its exact unique CBS name; **No job created…** records your confirmation
after inspection. Neither reconciliation action starts a job or uploads a recording.
**Clear finished** in the Activity panel hides finished jobs that need nothing from you; jobs
with pending cleanup, reconnect or Stop actions fold into one compact **earlier jobs need
attention** summary. Expand it to use those actions. Active jobs and newer updates stay visible;
no job is deleted, no cleanup is assumed successful, and a reload keeps the compact view.
Native and CBS Runtime batches appear as one card with expandable per-file outcomes. Unresolved cleanup and
recovery remain accessible after clearing finished work.
The **×** on a finished card removes just that card from Activity (nothing is deleted;
it shows again if the job changes). On active cards it means **Stop**, using the same exact
scope as the card's Stop/Cancel action. Unresolved cleanup/recovery cards stay visible and
have their own Retry controls. Files added together appear as grouped inspection/preparation
cards with per-file outcomes. The admission-to-transcription Activity handoff is under audit;
see [the workflow audit](docs/audits/transcription-batch-2026-10-30.md).

**UCloud WhisperX models.** Under Provider **UCloud**, the Model list also offers
**WhisperX large-v3 (CBS Runtime)** and **WhisperX large-v3-turbo (CBS Runtime)**. They run the
app's own pinned WhisperX pipeline on your UCloud connection: transcription, word timing and, if
you tick Speaker labels, speakers in the same job. The result keeps the model's confidence values
for **Show uncertainty**. The first time, the saved preparation preference or an explicit
confirmation permits preparing the runtime on your project drive, a download of several GB
of public packages and model weights. No recording is
uploaded until it is ready. Set the connection's language to da or en.
The catalog and selectors distinguish these from native models labelled **(Transcriber)**.
New CBS Runtime jobs independently verify and freeze the funded **gpu-nvidia-b200-1-mig.1g**
slice. Missing or ambiguous support refuses the request; there is no silent full-GPU fallback.
Native Transcriber separately uses a B200 1g MIG; previously frozen jobs retain their original
product. Live native MIG tests have completed for
some models and short recordings; other models and larger workloads remain unverified. Live CBS
Runtime MIG performance acceptance remains pending. See the [MIG test plan](docs/testing/native-transcriber-mig.md).

**Parallel parts** for API providers limits simultaneous requests to **1–4**.
API requests do not follow redirects. If a proxy redirects the request, configure its
final transcription base URL in Settings; the app does not resend audio elsewhere.
Native UCloud Transcriber sends the selected originals in one compatible batch job and uses
the native application's own processing. New compatible CBS Runtime files share a Terminal
allocation. Further files can join the same open allocation; another model, processing setup,
connection or research project creates another batch. Activity retains a separate outcome for
each recording. A batch stays open for one idle minute, with admission ending before its wall
deadline. Stop affects the whole shared allocation and preserves results already saved.
Retry reconnects to the recorded allocation; uncertain submission is never automatically repeated.
Verified completed stages survive an explicit processing retry, including successful ASR before
a later alignment/speaker failure. A failed local save can retry the same completed task without
another inference request. If interrupted input publication has no provable ready/acceptance
receipt, the app pauses rather than rewriting possibly accepted inputs; reconnect or Stop and
start a new run. Failed/partial runs containing private inputs retain their remote folders.
For a failed or partial Runtime batch, **Discard retained run files…** opens a separate preview
of every affected file and its saved or missing result. Available stage files are preserved
locally before confirmation; files without outcome proof are labelled unverified. A complete
result that has not been saved must be recovered with **Reconnect batch** first.
After explicit confirmation, the app records the decision and moves only this batch's owned
run folder to UCloud Trash. Local originals, saved results, versions and preserved evidence
remain. UCloud Trash is never emptied. The allocation must already be terminal; uncertain
ownership, changed evidence or missing local copies blocks the move. A failed move can be
retried against the recorded decision. Stop, Clear finished, Retry and ordinary cleanup
never authorize discard. Confirmed Delete separately lists the owned folders it will move
to UCloud Trash. Old failed Runtime batches can settle automatically after terminal/ownership
checks and preservation of available output; complete unsaved results still need recovery.
Uncertain submissions and unverifiable ownership remain visible for reconciliation.
Stopping a native transcription batch leaves an enhancement allocation running if it also
contains other files. Activity explains this; use that runtime batch's own Stop to stop it all.

Settings → UCloud → Advanced settings controls simultaneous CBS Runtime allocations
(initially **2**, range **1–4**). Each MIG executes one full WhisperX pipeline at a time;
independent allocations run concurrently. The first implementation does not keep several
GPU models resident between files. Old frozen single-file API/SSH jobs retain their original
execution/recovery path. Closing the app leaves remote work owned and recoverable; without
renewed heartbeats the controller drains after five minutes. Cleanup waits for durable local
results and confirmed allocation termination before moving its owned folder to UCloud Trash.
New OpenAI configurations suggest **2**; existing configurations and old queued jobs retain
**1**. UCloud defaults to **1** until its serving capacity is known.
Settings → Local Processing → **Parallel local tasks** sets how many files each step handles
at once: playback preparation, original audio inspection, transcription requests to a provider
API and request preparation checks. Automatic uses this computer's logical processors minus two
for playback preparation (at least 2, at most 8), half of them for inspection (2–4), 6 provider
API requests and 2 preparation checks; each step accepts
1–8 (preparation checks 1–4). Changes apply to the next file a step starts; running files
continue. Each API file sends up to its configured part concurrency, and rate-limited parts
stop for retry, so raise provider requests only within your provider's limits. Native UCloud
Transcriber keeps two concurrent jobs of its own, so API files no longer wait behind them, and
a long API request does not hold up a short WAV import. Enhancement keeps one local lane
independently of the runtime batch coordinators and the legacy UCloud lane: a long UCloud run does not hold up
local word timing or a passage repair, while two local WhisperX runs never compete for memory.
Heavy runtime audio preparation is bounded separately. Additional files wait in their stage's queue;
waiting imports display **Queued for preparation**. Provider-wide capacity limits for
UCloud vLLM and UCloud Transcriber remain later work.
Concurrency is frozen when a job is queued, so changing a provider does not accelerate an
already-running job or alter a retry's original settings.

Responses can finish out of order. Each successful part is checkpointed with its exact index;
final text/timing is combined chronologically. An error stops new scheduling while accepted
requests settle, and retry submits only missing parts. No automatic rate-limit retry or
provider fallback occurs. Cancellation cannot revoke uploads already accepted by a provider.
Graceful shutdown keeps the library locked until those requests finish checkpointing, which
can take as long as the active API request. Forced termination leaves uncertain work for
explicit retry.

## Storage and supported inputs

Everything lives in one visible folder in your home directory, on Windows and macOS alike:

| Folder | Windows | macOS |
| --- | --- | --- |
| New projects | `C:\Users\<name>\CBS Transcriber\Projects` | `/Users/<name>/CBS Transcriber/Projects` |
| Library (database, backups) | `C:\Users\<name>\CBS Transcriber\Library` | `/Users/<name>/CBS Transcriber/Library` |

The home folder is not synced by OneDrive or iCloud's Desktop & Documents option, so recordings
stay on this computer by default. Don't edit files in `Library` by hand. The launcher prints both
paths, and `GET /api/storage` reports the library actually in use.

An existing library at the earlier default (`%LOCALAPPDATA%\CBS Transcriber`, or
`~/Library/Application Support/CBS Transcriber`) keeps being used where it is; it is never moved
automatically. When launched from a packaged desktop app, Windows may have redirected that
location into the app's `LocalCache\Local` folder; open it with `--data-dir`.
`--data-dir` or `CBS_TRANSCRIBER_DATA_DIR` overrides the library root.

When creating each project, **Choose folder…** opens the Windows folder chooser. You can also
enter an absolute parent folder. The app creates a dedicated short-ID subfolder, leaving other
files in the parent untouched. Leaving the field blank uses `<home>/CBS Transcriber/Projects`.
`--projects-dir` or `CBS_TRANSCRIBER_PROJECTS_DIR` changes this default; existing projects stay
where they are.
To relocate an existing project, use **Choose folder…** beside its **Storage folder** field,
or enter a new absolute parent path; the app asks before moving the files. Finish or cancel that project's
queued/running jobs first. Copying large recordings can take time and needs space for both
locations temporarily. The app switches storage
after copying; failed copies retain the original. Any old copies that cannot be removed are
reported. Only managed project files move; the central database and keys stay in the library.

Classification and storage changes save automatically; a short message confirms a saved
classification. **Project Settings** in the sidebar (on a project page) holds the project's
**Classification**, **Storage folder** and **Delete Project**; deleting asks you to type the
project name; deletion removes the project's recordings, transcripts,
results, notes and history from the app and cannot be undone. **Add recordings** sits beside the
recordings search box. **Choose folder…** opens the Windows folder window in front of the browser.
The page needs a normal browser (Edge, Chrome, Firefox, Safari); dropdown menus may not open in
embedded preview panes.

Delete cancels the recording's or project's queued/running jobs and waits for its workers to
stop. A shared UCloud allocation affecting other recordings needs an additional impact
confirmation. The remote-folder confirmation lists exactly what will move to UCloud Trash;
folders needed by surviving recordings are retained. Failed batch cleanup does not block
local deletion: an independent cleanup card remains in Activity with Retry. Uncertain
submissions require reconciliation; cleanup never starts another job or empties Trash.
Legacy single-job journals still require resolution before deletion. Closing the dialog or
leaving the page stops further deletion checks. Source files outside the managed library
are unaffected.

Local files are first staged by a same-volume rename. The app removes their database entries
before physically clearing the staged files. A failed precommit operation restores files;
restart recovery never resumes an uncommitted Delete. If physical cleanup remains blocked,
Activity keeps **Retry local cleanup** after the project is gone. Unresolved file recovery
keeps the library readable and blocks changes until resolved. Finish these obligations before
backup, storage relocation or library switching. This covers tested process interruptions;
power-loss and cloud-sync durability still need separate acceptance.

Optional **Description / notes** fields belong to each project and recording. **Save notes**
stores these independently of project settings and reviewed transcript text.

Each new project folder contains:

- `recordings\<recording-id>\`: original uploads, browser playback, transcript sources, chunks
  and original API responses.
- `project.json`: readable project/recording metadata and all saved results, including original
  and edited text, timings and speakers. Updated after media preparation and transcript saves.
- `exports\<recording-id>\<result-id>.txt`: readable UTF-8 text for each saved result, updated
  after edits. Browser exports are also downloaded normally.

**The central `library.sqlite3` remains authoritative and also contains transcript text**, edits,
provider configurations, and queue state. Project folder snapshots are additional local copies;
opening a copied `project.json` as an independent project is not yet supported. Folder write
failures leave database edits intact and report a snapshot warning. Projects move only when
you explicitly save a new storage choice. Older projects retain the original
`<library>\recordings\<recording-id>` layout. Keep storage outside Git and choose locations
appropriate for the recordings; cloud-synced folders may sync sensitive files.
Upgrading an existing library to named providers/results first saves a SQLite backup under
`backups/` inside the library. Existing transcripts become **Existing transcript** results with
their text, edits, speaker names, timing, and revisions preserved.
Stop the app before copying the **whole library folder and all separately chosen project
folders** for backup or transfer.
Deleting a recording/project removes only the app-managed copies, after confirmation.

Media: MP3, WAV, M4A, FLAC, OGG, MP4, MOV, MKV, WebM, MPEG/MPG, MPGA, AAC, WMA and AVI,
subject to the installed FFmpeg decoder. Videos require an audio stream for transcription.
Playback derivatives use MP3 and H.264/AAC MP4. Damaged/unsupported media reports a job error.

Transcripts: UTF-8 JSON with a `segments` array, a segment list, or a plain `text` result;
Whisper/Transcriber CSV containing `start`, `end`, `text`, and optionally `words`/`speaker`.
Column order is flexible. Word fields can contain JSON arrays or safe Python literal lists.
Missing/incomplete word timestamps fall back to passage highlighting. Untimed text stays untimed.
Transcript uploads are limited to 32 MB; media uploads to 20 GB, streamed to disk.

The app uses its own transcript parsers and exporters. The earlier supplied preparation
scripts and guides were retired on 10 November 2026. Real UCloud/NVivo file acceptance
remains a follow-up in `TODO.md`. Live evaluation uses separately configured local credentials.

## Reference evaluation

The repository includes five licensed Danish FLEURS recordings with matching reference text
(64 seconds total). Sources, hashes, license, scoring rules, and limitations are documented in
[`tests/fixtures/fleurs_da`](tests/fixtures/fleurs_da/README.md).

To measure the configured provider, start the app and choose its model in Settings (for example,
`whisper-1`). The script uses the provider's saved language fallback when it creates its evaluation
project; a blank fallback uses the provider default. Then run:

```powershell
uv run python scripts/evaluate_fleurs.py run --app-url http://127.0.0.1:8765 --allow-api-calls --output evaluation-results/whisper-da-001
```

Use the port where your app is running. This explicitly sends the five public clips to the
configured provider and may incur charges. The script creates a separate evaluation project,
uses normal media preparation/transcription, and leaves the project available for review.
The runner verifies the selected provider's accepted job settings and scores each producer's
immutable creation version. Later edits, result switches and enhancement results do not change
that score or its version-specific export. Output includes predictions, readable-text exports,
safe provider/job/result/version receipts, payload/artifact hashes and a WER/CER report. The
legacy `.nvivo.txt` filenames contain readable text (`format: text`), not native NVivo rows.
API keys are excluded. The output directory must be new; reports are ignored by Git. Missing,
ambiguous or inconsistent producer evidence leaves an incomplete run rather than a score.
If the run fails or times out, inspect its `run.json` and the app's jobs before retrying;
requests may already have reached the provider. No automatic API retries are performed.

Saved output from any backend, including UCloud, can be scored without an API request.
Prepare a UTF-8 JSON object mapping **all five** clip IDs to recognized text:
`{"da_dk_0000": "recognized text", "da_dk_0001": "...", ...}`. Then:

```powershell
uv run python scripts/evaluate_fleurs.py score predictions.json --output report.json
```

WER/CER measure word/character errors against normalized reference text; lower is better.
Reports include per-clip errors and corpus totals. This small read-speech benchmark cannot
establish interview accuracy or validate word timing/speaker labels; keep the browser tests
and synthetic timing tests. Routine tests exercise these real audio fixtures with mocked
model responses, so they do not measure actual ASR accuracy or incur provider charges.

## Development and verification

The current development branch includes the internal schema-13
[original-inspection prerequisite](docs/verification-2026-10-28-original-inspection.md).
It does not yet remove synchronous transcription admission or playback-readiness coupling.
Develop and migrate only synthetic libraries until the remaining T87b admission work passes
verification; activating an existing library is a separate backed-up, idle operation.

```powershell
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run playwright install chromium
uv run pytest -m browser -o addopts=''
```

The tests use temporary libraries, synthetic and licensed public fixtures, and mocked providers;
they make no paid API calls. Browser tests run a local server and use Chromium.
`AGENTS.md` documents contributor guidance; `SPEC.md` records invariants; `TODO.md` tracks progress.
Architecture rationale lives in [docs/decisions](docs/decisions/README.md), with domain terms
in [CONTEXT.md](CONTEXT.md).
The [post-implementation audit](docs/audit-2026-10-20.md) records reproduced issues,
regression evidence and the remaining live acceptance boundaries.

The interface follows the [official CBS design guide](https://design.cbs.dk/en/cbslogo-fonts-colours-and-design/):
CBS blue, the original CBS logo, CBS New Bold headlines, and CBS Sans (Inter) web text.
Brand assets are served locally; provenance and font notices are in
[`static/brand/README.md`](src/cbs_transcriber/static/brand/README.md).

The app serves only loopback and rejects cross-origin mutations. It is intended for a
single local user, not internet hosting. Automatic word realignment and desktop installers are deferred. Native UCloud job
execution requires separate live acceptance with your project/template.

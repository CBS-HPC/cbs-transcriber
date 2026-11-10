# Colleague test — 11 November 2026

Test **MVP 0.1.0**, available from the [release page](https://github.com/CBS-HPC/cbs-transcriber/releases/tag/v0.1.0).
Use the [installation and Desktop shortcut guide](../README.md#install-and-start-windows-and-macos)
if installing it yourself. Record the actual label; this checklist is for the released MVP.

Allow about one hour for the app walkthrough. The maintainer supplies an installed app,
configured accounts and agreed non-sensitive test recordings. Use the separate
[NVivo test plan](work-plans/mvp-nvivo-test-plan-2026-11-11.md) for NVivo acceptance.
This guide describes checks to perform; it does not record a successful test.

The **Working version** is the saved transcript used when exporting the whole project.
You can review other results and older versions without changing that choice. Editing the
current Working transcript normally advances it; editing an older version may ask which
version you want to use for future project exports.

## Before you begin

Start CBS Transcriber from its Desktop shortcut. Keep its terminal window open while you
work. The browser should open the app. Ask the maintainer if the app does not open or shows
an unexpected library. Record the release label and browser in the feedback form below.

## Walkthrough

1. **Make a project.** Create `Colleague test`, choose Standard and Danish. Open its project
   settings, change the name, then Cancel and confirm the name is unchanged. Repeat and Save.
2. **Add recordings.** Add the supplied short and long files together. Open Activity and watch
   their progress. Can you tell when each file is ready and whether anything needs attention?
3. **Select files and language.** Try Select all and Deselect all. Change the short recording
   to English, then back to Danish. Confirm both the card and recording view show Danish.
4. **Transcribe.** With the maintainer, select the agreed provider and model and start the
   requested test. Observe Activity. Live runs use the separately agreed account and cost scope;
   the maintainer can supply prepared results for the editing checks below.
5. **Listen and navigate.** Open a result. Use Play transcript, pause, replay a passage and
   change playback speed. Try Follow playback, scroll manually, then continue playback.
   Does the transcript remain easy to follow?
6. **Edit while listening.** Open a passage for editing, replay it and change a few words.
   Save. Reopen it, type a different change and Cancel. Check that the saved wording remains.
   Note any lost cursor position, jump, unexpected prompt or noticeable delay.
   Double-click a rendered word: Text should open with that word selected and no duplicated
   saved-text panel. Save should be disabled until you change the text; reverting the draft
   to the saved text should disable it again. Cancel should close editing promptly.
   Append one word, save and inspect Word and timing: existing words retain their times and
   the added word is untimed. A simple replacement retains the old word's interval. Ambiguous
   timing should leave affected words untimed, with a nonblocking explanation. Word timing
   must not prevent saving manual wording changes.
7. **Edit structure and speakers.** Split a suitable passage at the cursor and use Undo split.
   Split should be disabled at the start/end, inside a word, or while text changes are unsaved.
   A valid split between words must also work when some words have no timing.
   Merge two passages and use Undo merge. Rename a speaker and change the speaker for one passage. Delete a
   disposable passage. Confirm unrelated passages and speaker names remain as expected.
   Open Deleted passages and restore it. Confirm its exact text returns, later edits elsewhere
   remain, and a later merge cannot make the same old deletion available for duplicate restore.
   Mark passages Checked and Needs review and check the manual progress counter. Reopen the
   recording; marks should remain. Editing a checked passage should make it need review again.
   Use Next to review to cycle through Not checked/Needs review passages, skipping Checked.
   Try with Show uncertainty off; the manual navigation should still work and wrap at the end.
8. **Check versions.** Confirm the first result is Working. Make a saved edit and check the
   Working indication. In Versions, review an older version and restore it as a new version.
   Try selecting another saved version as Working. The Set as working button should be
   inactive while you view that exact Working version. Ask the maintainer to explain any prompt
   before choosing an answer, and record whether the wording was understandable.
9. **Compare and export.** If a second result is available, compare it. Export Word and readable
   text and open them. Export the project ZIP using Working versions and check that the intended
   recordings are included. If testing NVivo, use the separate NVivo plan below.
   In project Export Settings, deselect one recording and choose two formats. Check that the ZIP
   includes only those selections and uses recording-name folders; any format skip is reported.
   An untimed result may need Word timing before a timed export is available.
   With one short test recording, enable Include original recordings and check that its original
   appears beside its transcripts. A recording without a transcript should show a reason and be
   disabled until originals are enabled; enabling them should not select it automatically.
   Try an originals-only ZIP by deselecting all transcript formats.
   Start preparing a ZIP with originals, close Export Settings and edit another passage while
   Activity reports preparation. Download the ready ZIP through Activity. Cancel a preparation
   and discard an unwanted prepared ZIP; neither action should alter source recordings.
10. **Restart.** Save edits and wait until Activity is idle. Stop the app with Ctrl+C in its
    terminal, then start it again from the shortcut. Confirm your saved wording, speaker names,
    review marks and Working choices remain. Closing only the browser tab does not stop the app.

## Optional separately arranged checks

- **NVivo:** use the [NVivo test plan and results sheet](work-plans/mvp-nvivo-test-plan-2026-11-11.md).
  The synthetic pack has known Danish/English wording, speakers and timestamps; actual NVivo
  imports remain unverified. Allow up to 90 minutes separately from the app walkthrough.
- **UCloud interruption/recovery:** with the maintainer and an agreed test/account scope,
  close the app after the remote job has been accepted, then relaunch and use Reconnect/Retry.
  Record the UCloud job ID before and after; recovery should not create another allocation.
  Live shutdown/relaunch acceptance is still open, so this is not a routine colleague prerequisite.
- **Backup/restore and permanent library deletion:** maintainer-led acceptance on disposable
  data only. Some backup/restore controls are hidden in MVP; do not enable unfinished features
  for the ordinary walkthrough. Restore into a separate library, never over the test project.

Known limitations: spelling assistance and automatic CBS shortcut icons are outside this MVP.
Existing sole results in an older test library may require explicit Set as working; step 8
checks a newly created first result. Long transcripts can take several seconds to save/split:
record observed delays rather than expecting the small-file response time.

## Feedback

Record each issue in a copy of this table. Include the action immediately before it happened;
screenshots of the app are useful when they contain only the supplied test material.

| Field | Your notes |
| --- | --- |
| Release label / commit (maintainer supplies) | |
| Windows/macOS and browser | |
| Step and action | |
| What you expected | |
| What happened | |
| Approximate delay, if relevant | |
| Did it prevent continuing? | |
| Screenshot or error wording | |

Finish with three questions: Which action was hardest to find? Which action felt slowest?
What would you change before using the app for your own work?

## Maintainer sign-off

Record actual outcomes separately for installation, provider runs, Working/export, backup and
isolated restore. Save the exact tested commit and [NVivo results](work-plans/mvp-nvivo-test-plan-2026-11-11.md).
A green synthetic suite does not establish that these real-device checks passed.

Test permanent library deletion separately with a disposable inactive library containing only
synthetic files: inspect what the preview will delete and retain, confirm by typing its name,
and verify the displayed outcome and remaining files. The active library must be refused.
Record any refusal or pending cleanup instead of treating a hidden list entry as deleted data.

# Colleague test — 11 November 2026

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
   For a separately authorized UCloud handoff check, save edits and close the app after its
   UCloud job has been accepted. Relaunch and use that Activity card's Reconnect/Retry action.
   Confirm it uses the same UCloud job ID and retrieves its results without another allocation.
5. **Listen and navigate.** Open a result. Use Play transcript, pause, replay a passage and
   change playback speed. Try Follow playback, scroll manually, then continue playback.
   Does the transcript remain easy to follow?
6. **Edit while listening.** Open a passage for editing, replay it and change a few words.
   Save. Reopen it, type a different change and Cancel. Check that the saved wording remains.
   Note any lost cursor position, jump, unexpected prompt or noticeable delay.
   Double-click a rendered word: it should open Text, without a duplicated saved-text panel.
   Append one word, save and inspect Word and timing: existing words retain their times and
   the added word is untimed. A simple replacement retains the old word's interval. An ambiguous
   edit may refuse saving while keeping the draft; record any confusing message.
7. **Edit structure and speakers.** Split a suitable passage at the cursor and use Undo split.
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
   recordings are included. Export NVivo for your platform and use the separate NVivo plan.
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
10. **Back up and restart.** When Activity is idle, open Library → Backups, choose Create backup
    and wait for its result. Save the backup receipt. The maintainer checks the backup and
    restores it into a different library. Close and restart the app when idle; confirm your saved
    wording, speaker names and Working choices remain.

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

# Review a transcript

This guide covers MVP 0.1.0 and, where marked, the unreleased post-MVP branch: the **Show**
filter and spoken-word highlighting in **Text** are post-MVP and not in the 0.1.0 package.
[Provider setup](provider-setup.md) explains transcription;
[exports](export-guide.md) explains downloads. Some specialist guides describe advanced
features hidden in MVP; check the [profile list](installation.md#release-profiles-and-advanced-features).

## Projects and recordings

Create a project, choose Standard or Protected, set its default language and add audio/video
files. Activity shows preparation and any errors. Select cards, choose Provider and Model,
then **Transcribe selected**. To use existing text, open the recording and choose
**File Settings → Import transcript** for JSON/CSV. Each import or provider run creates a
separate saved result; it does not overwrite an existing reviewed transcript.

Project Settings holds the name, classification, language and storage choice. Changes are
staged until **Save**; **Cancel** keeps the saved settings. Recording language overrides the
project default for new processing. Existing jobs retain their queued choices.
Use the pencil beside a recording title to rename it.

Collections group recordings within one project. Create one above the cards, select cards
and use **Move to**. Uploads while a collection is shown belong to it. **Collection Settings**
renames or deletes that group; deleting a collection leaves its recordings in **Unsorted**.
Files do not move on disk. Project exports respect the shown collection.

Project and recording **Description / notes** fields save separately from transcript text.
Save notes explicitly. Recording notes can be edited on its card or inside review.

## Listen and navigate

Click a timed word or passage to seek. **Replay** stops at the passage end;
**Play transcript** continues from the current position. Change speed in the player.
**Follow playback** scrolls to the current passage; enabling it immediately reveals that
passage. For video, switch **Audio / Video** while keeping playback position and speed.

Passage replay does not require word timing. An untimed fragment may offer a verified source
passage instead. If no usable interval exists, Replay's tooltip explains that it plays the
full recording; it cannot locate that fragment. Only reliable timed words highlight.
Partial timing does not mean that the whole passage has reliable word timing.

## Correct words and structure

Double-click a rendered word or choose **Edit** to open **Text**. Make the change and **Save**;
**Cancel** discards the draft. Save is disabled until the wording changes. Conflicting edits
from another window are rejected so you can review them rather than overwrite saved work.

Post-MVP: while a passage plays, **Text** highlights the spoken word inside the text box using the
saved word timing. **Replay passage** in the editor plays only that passage. Highlighting
never moves your cursor or selection and does not affect Undo. Once your draft differs from
the saved text, only words you have not touched keep their highlight: an edited or newly
typed word is not highlighted, and the app never guesses a time for it. Untimed words are
never highlighted. With **Follow playback** on, the text box scrolls to the spoken word, but
waits while you are typing.

Text corrections save even when word timing is missing or ambiguous. Proven unchanged words
retain their intervals; a uniquely matched replacement can retain the replaced interval.
Added or uncertain words stay untimed, with a nonblocking explanation. Saving does not
automatically align new text to audio. Earlier wording remains in History.

**Word and timing** lets you select a word by number/text, correct it with **Edit word** or
change its start/end times. Its saved-text playback preview follows reliable word timing
without moving your typing cursor. Playback never assigns guessed times to an unsaved draft.
Changing edit modes does not realign text.

- **Split at cursor:** save text first, then place a single cursor between words with text on
  both sides. Splitting inside a word, at an end or across a selection is disabled. Valid
  between-word splits work without complete timing. Known timing is retained where proven;
  unlocated halves remain untimed. A deliberately chosen interior playback position can
  supply an audio boundary.
- **Merge up/down:** join adjacent passages. If their speakers differ, choose the intended
  speaker deliberately.
- **Delete passage:** remove that passage from this result. Earlier text stays in History.
- **Restore original:** restore the passage's saved baseline.

**Undo split**, **Undo merge** and **Deleted passages → Restore passage** restore proven
structures while preserving edits elsewhere. Later changes can make an individual inverse
unavailable; use the exact earlier version in History instead. Consecutive deletions may need
restoring newest first when placement anchors have changed. Split halves have their own
restore baselines.

## Speakers and manual review

Unnamed voices display as Speaker_01, Speaker_02, and so on within each result. Rename a
speaker in its list, or assign a different label to one passage. A voice label is not proof
of a person's identity. Names and edits belong to the result you are reviewing.

Use **Not checked**, **Checked** and **Needs review** to track your listening review.
**Next to review** cycles through unchecked/needs-review passages and works with uncertainty
display off. Editing a checked passage makes it need review again; unrelated marks stay.

Post-MVP: **Show** limits the transcript to one group of passages, with a count for each choice:
**All**, **Not checked**, **Needs review**, **Checked**, **Uncertain** or **Missing word
timing**. Uncertain means the passages marked "Review suggested" at the chosen Show
uncertainty level; it is available while Show uncertainty is on. Missing word timing means
passages without complete word timing, including passages with no timestamps. Show works
together with search: a passage appears only if it matches both, and the counts describe the
passages your search finds. **Next to review** visits only the shown Not checked and Needs
review passages, so with **Checked** it has nothing to visit. A passage you mark or edit
stays visible until you choose a filter again, and the passage you are editing always stays
visible with its unsaved draft. Opening another recording resets Show to All. Show is not
available while you compare two results. Model uncertainty only suggests where to listen; it
never sets or changes a review mark.

**Show uncertainty** identifies the model's relatively weakest passages/words in this
transcript. The 5/10/20% choices and tooltips help prioritize listening. These scores are not
accuracy measurements; manual Checked marks are independent. Editing removes stale model
hints for the changed passage.

## Results, history and Working versions

**Review result** chooses the saved result to edit or export directly. **Compare with** opens
another result in a read-only pane on the same playback timeline. Search highlights literal
wording in both panes; single-transcript search also filters passages. Matching is approximate,
local and automatic. Linked scrolling and visual matching do not alter text, speakers or timing.
A later result does not automatically replace the result already selected for review.

**Versions → History** previews immutable saved states. **Restore as newest version** adds
an earlier state as a new saved version; it does not erase later history. **Versions → Results**
lists other results and recoverable Trash. Moving a result to Trash hides it from ordinary
choices/exports and clears its Working designation. Its files and history remain. Restore
returns it without automatically making it Working.

The **Working transcript** is the saved version used by default project exports. It can
differ from the result open for review. A newly created first result normally becomes Working.
Editing the current Working version advances it with the saved edit. When editing another
version, the app can ask whether the edited version should become Working or whether to keep
the existing choice. Use **Set as working** in Versions/History to choose deliberately.
Some older/restored libraries need an explicit designation.

Processing can advance Working only while its frozen source and the Working choice remain
unchanged. A newer explicit choice is retained. Project exports list unassigned recordings as
skipped; check the designation before a whole-project download. See
[history and research evidence](research-history.md) for the detailed limits.

## Add word timing or speaker labels

Timing and speaker actions use the included WhisperX processing stack. Settings → Local
Processing controls device, model-download permission and runtime status. Models are separate
downloads; opening Settings or starting the app does not download them. Intel Macs do not
include the pinned local inference stack. See [local setup and limits](local-enhancement.md).

Passage **Add word timing** adds timing as a new version of the same result while retaining
the reviewed wording. **Repair all** handles eligible missing-timing passages together.
Unbounded text is searched only within a bounded gap between known neighbours; a search gap
is never stored as if it were a passage's real timing. Reliable words in incomplete passages
can still highlight. A stale result/text revision prevents applying old timing.

Whole-recording enhancement creates a separate result from the exact version queued.
Later source edits stay in the source; they do not appear automatically in that result.
**Versions → Results → Preview merge** shows nonconflicting timing/speaker updates that can
be applied while retaining current wording and named voices. Inspect skipped/conflicting
passages; changed passage structures may require manual comparison. UNKNOWN labels do not
replace known voices. **Apply safe updates** records a new version.

MVP hides passage Re-transcribe, comparison Use wording and automatic realignment after saving.
Full-profile [passage retranscription](passage-retranscription.md),
[UCloud enhancement](ucloud-enhancement.md), [research reports](research-history.md) and
[topic evidence](topic-evidence.md) have separate guides and acceptance limits.

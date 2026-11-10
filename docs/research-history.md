# Research history and workflow evidence

The app keeps immutable versions beside each saved result and each project's or recording's
notes. The initial history delivery was activated on 22 October 2026 after backup and
preservation checks. This guide also describes the later Working-version, conflict-review and
producer-evidence features. Consult the [current worklist](../TODO.md) for integration and
running-build status; an implementation or synthetic check is not evidence of activation.

## Review history

Choose **Versions** beside **Review result**, then the **History** tab. The list shows observation
times, edit categories, version hashes and whether the processing source version is known.
Choose a version to preview its visible wording without activating it or changing playback,
edits or the current result.
Use the historical text download for that exact version. The separate exact JSON API includes
preserved original fields and should be handled as research data.

Accepted word/free-text corrections, passage restoration, speaker assignment/renaming and notes
saves retain previous states. Notes have independent revisions. No-change saves are recorded
separately from corrections. Playback, comparison, result selection and report downloads do not
create edit events. Local-user categories do not establish the identity of a researcher.

Opening an existing schema-5 library first makes a SQLite backup, then adds
schema-6 history in one transaction. Existing results/revisions stay unchanged. Their first
history entries are **legacy observations**: earlier edits, authors and processing environments
cannot be reconstructed. Inconsistent active-result mirrors block migration with recovery
guidance; they are not silently reconciled. Newer schemas are rejected before writes.

An enhancement freezes its exact input version and hash. Subsequent source edits do not alter
that lineage. Old queued jobs keep their frozen text but explicitly lack a historical version
identity. Successful retries cannot duplicate the first version or enhancement follow-up.

Choose **Restore as newest version** on an earlier saved state to append its exact transcript
payload as a new version of that result. The revision check refuses a stale edit; earlier
versions remain available. Notes and project settings are separate. This content restoration
does not move the Working designation or restore a result from Trash.

## Working transcript and explicit review choices

The result selected for review and the **Working transcript** serve different purposes. Mark
an exact saved version using **Mark current saved version Working**, or inspect an older state
in History and choose **Mark this version Working**. Review the version/hash preview before
confirming. Marking changes the designation, without replacing the current text or review view.
Later edits, a restored version or a completed producer leave that designation on its original
version. Nothing is assigned automatically.

Project exports and default research reports use those exact pinned payloads, even when the
same result has newer edits. Recordings with no Working designation are listed as skipped;
there is no fallback to the latest result. Explicit current-result/all-result exports and
explicit-version API reports retain their selected scope. Trashing the owning result clears
its Working designation; restoring that result does not mark it Working again.

In **Versions → Results**, an eligible enhancement can offer **Review conflicts**. The preview
shows its frozen base, current content and incoming wording, timing and speaker values. Choose
**Keep current** or **Use incoming** for each group deliberately; undecided groups stay intact.
Apply checks the exact preview and current revision before recording the decisions as a new
history observation. Timing requires matching current wording; ambiguous passage correspondence
remains unresolved. See [ADR 0093](decisions/0093-working-versions-and-review-evidence.md).

## Removing and restoring saved results

**Versions → Results** lists a recording's model transcriptions and enhancement results. **Move to
Trash** removes one result from review/comparison choices, new processing inputs and ordinary
recording/project exports. **Restore** returns its exact saved state. Removing the active result
selects the newest remaining live result; when none remains the recording has an empty transcript
view. Restoring a result selects it automatically only when no result is active.

The schema-8 migration first backs up a schema-7 database, then adds a separate Trash marker
table in one transaction. It changes no existing rows. Trash/restore actions append explicit
history observations with identical payload bytes; they are not counted as transcript edits.
Recording revisions protect these actions against stale windows. Independent notes survive.

Trash retains raw responses, originals, result rows, exact versions, processing receipts and
existing project-file copies. It does not cancel frozen jobs or break their source links, and
retrying a completed producer does not recreate or restore its result. Existing topic runs and
historical reads remain available; exact-version topic coding may show its source as changed
after lifecycle events. New topic runs require a restored result. No permanent purge is offered.

Research reports cannot select a result currently in Trash as a root. They can still describe
its preserved version when a selected enhancement depends on it, with `result_in_trash=true`
in the manifest. Only explicitly selected live transcript text can be included in that ZIP.

## Research report

Choose **Research report…** in a project. The default ZIP contains `manifest.json` and
`methods.md`, describing selected versions, processing/review observations, available model
settings, lineage, timing coverage, content hashes and explicit gaps. The methods draft needs
researcher review; no claim of universal journal compliance or exact API inference reruns is made.
The default selection is the exact Working version for each recording in scope, with unassigned
recordings reported as skipped. Merely changing **Review result** does not change that selection.

By default, it excludes transcript text, names, notes/reasons, paths, endpoint/account/job IDs,
recordings, raw responses and discarded originals. Hashes can still be linkable; metadata-only
does not mean anonymous. **Include the selected Working transcripts** explicitly adds visible
text and displayed speaker labels/names for the selected versions. It does not add unselected
historical text or preserved originals. Every included text file has its own byte count, renderer ID and
SHA-256 checksum. Downloads stay local and never execute a provider.

The API can select exact historical versions, independently of today's active result. All
selection/lineage reads use one SQLite snapshot; concurrent edits cannot substitute a newer
state partway through report capture. Reports currently allow 100 selected versions, 1,000
lineage states and 32 MiB of total preserved payload, checked before loading each payload. Large
interviews with extensive histories may need a narrower selection; scalable ancestor summaries
are later work. Missing or corrupted version content fails explicitly.

## Original transcript import evidence

New successful JSON/CSV imports preserve the exact input bytes and record a SHA-256/byte-count
receipt in the result's origin. BOM, whitespace and line endings belong to that original file;
the editable transcript is the separately normalized view. Edits, restoration and Trash retain
the receipt and earlier versions. Existing imports are not reassociated or backfilled.

Reports show recording-scoped source aliases, format, size and hash. They validate the saved
source/context agreement inside the report snapshot, with a separate 32 MiB source-metadata
budget checked before loading each result's source. They do not open retained files: availability
is `not_checked`, not a statement that the original is presently recoverable. A malformed or
contradictory captured receipt refuses export. Names, internal IDs, paths and raw import text
remain excluded. This does not capture an imported file's ASR model/environment or establish
complete backup coverage; those provenance gaps remain explicit.

## Captured processing evidence

Fresh jobs with an explicit capture policy bind their observations to the immutable creation
version, or to the particular same-result timing successor. Supported paths include local
whole-recording enhancements and bounded local timing, CBS Runtime full ASR, API transcription
and native UCloud Transcriber single/shared jobs. Their scope differs:

| Path | Recorded scope and limits |
| --- | --- |
| Local enhancement and timing | Sealed request, prepared-input and validated stage observations; only allowlisted environment facts actually observed. Timing evidence belongs to its application version and does not replace the original producer lineage. |
| CBS Runtime full ASR | Accepted request/input bindings and validated locally collected stage/control output. A normalized task outcome is not an original provider wire response; the remote environment remains uncaptured. |
| API transcription | Credential-free logical SDK request descriptors, normalized SDK part outputs and their assembly binding. These are not HTTP wire bytes or proof of the provider's internal model/environment. |
| Native Transcriber | A credential-free projection of the submitted specification and exact downloaded output bytes, checked against the owning journal. Unowned free-text parameter values are withheld; the projection does not prove remote execution or model weights. |

Edits, restoration and merges retain the evidence bound to its original observation. Legacy
jobs and files never acquire retrospective capture merely because files exist. Historical
reports validate preserved references in their bounded database snapshot, without opening
current media, runtime or model caches. File availability stays `not_checked`; unobserved
roles stay `not_captured`. Original-source and preparation receipts describe their recorded
byte/sample-window scope, not provider-side decoding or present file recoverability.
See [ADR 0093](decisions/0093-working-versions-and-review-evidence.md),
[ADR 0097](decisions/0097-api-provider-artifact-observations.md) and
[ADR 0100](decisions/0100-native-transcriber-artifact-observations.md).

## API

- `GET /api/recordings/{recording}/trashed-transcripts`
- `DELETE /api/recordings/{recording}/transcripts/{result}` with `{revision}` moves to Trash.
- `POST /api/recordings/{recording}/transcripts/{result}/restore` with `{revision}` restores.
- `GET /api/recordings/{recording}/transcripts/{result}/versions?limit=50&before_sequence=N`
- `GET /api/recordings/{recording}/transcripts/{result}/versions/{version}`
- `GET /api/recordings/{recording}/transcripts/{result}/versions/{version}/export?format=text`
- `POST /api/recordings/{recording}/transcripts/{result}/versions/{version}/restore` with
  `{revision,reason?}` appends that transcript state as the newest version.
- `POST /api/recordings/{recording}/working-preview` with `{version_id,revision,role_revision}`,
  then `POST /api/recordings/{recording}/working` with those fields and the returned `fingerprint`.
- `GET /api/projects/{project}/notes/versions` and the recording equivalent.
- `POST /api/projects/{project}/research-report` with optional `selections` of
  `{recording_id,result_id,version_id}` and `include_selected_transcripts` (default false).
  Omitting `selections` uses the exact Working designations in the requested scope.

History pages are limited to 100 entries and metadata lists exclude text/reasons. Existing edit
and notes requests accept an optional plain-text `reason` of at most 2,000 characters. Action
categories are assigned by the handler, not asserted by the browser. Ownership checks prevent
reading another recording's history through an unrelated result/project ID.

## Recorded actions

Every change to a result's text, words, timing, speakers or speaker names appends one exact
version in the same transaction as the visible update. The action is set by the server:

| Action | Recorded when | Counts as a manual edit |
| --- | --- | --- |
| `provider_result` | A transcription provider (OpenAI, UCloud) saves a new result | No |
| `imported_result` | A transcript file is imported as a new result | No |
| `enhancement_result` | Word timing/speakers or passage repairs save a new result; the report's `scope` says `whole_result`, `passage_repair` or `passage_repairs` with a passage count | No |
| `free_rewrite` | Free-text passage save | Yes |
| `word_correction` | One word replaced, keeping its timing | Yes |
| `original_restore` | A passage restored to its imported text and timing | Yes |
| `passage_merge` / `passage_split` | Adjacent passages joined, or one passage split | Yes |
| `passage_delete` | A passage removed from the result | Yes |
| `passage_speaker` | One passage assigned to another speaker | Yes |
| `speaker_rename` | A speaker's name changed throughout the result | Yes |
| `manual_save` | Older whole-transcript saves | Yes |
| `version_restore` | An earlier version restored as the newest version (context names it) | Yes |
| `result_merge` / `result_conflict_review` / `wording_adopt` | Explicit preview-bound adoption or review of another result's values; earlier versions and cited parents remain | Yes |
| `timing_realign` | Opt-in word timing for an edited passage; text unchanged (context: passage, job) | Yes |
| `save_no_change` | A save that changed nothing (still observed) | No |
| `result_trashed` / `result_restored` | A result moved to or restored from Trash | No (lifecycle) |
| `legacy_observed` | State found when history was introduced; earlier edits unknown | No |

Activating another result for review writes no version: it changes which result is shown,
not any result's content. Working designation changes have their own role revision and event
history. Notes have their own versions and are not transcript edits.

## Limits and next slices

Complete processing-environment capture, opaque provider internals, missing legacy observations,
larger-scale history/report handling and researcher acceptance remain open. Reports expose
those gaps rather than reconstructing them from today's settings. Content hashes are integrity
checks, not administrator-proof logs or verified authorship. A report is not a complete library
backup or a tested restore package; see [ADR 0096](decisions/0096-portable-library-restore-and-manual-cloud-retention.md).

Project/recording Delete removes the corresponding retained research history under the current
deletion rules. For native/CBS Runtime batches, a fingerprint-confirmed preview binds the exact
shared impact and owned-folder disposition; local deletion can finish while failed remote
cleanup remains as an independent Activity obligation. Surviving members keep their recovery
scope. Legacy single-job journals still block local deletion until resolved because their job
rows hold the control evidence. Stop and Clear finished grant no disposal consent, and no path
purges UCloud Trash. See [ADR 0101](decisions/0101-delete-release-and-automatic-settlement.md).
Retention and backup procedures still need institutional agreement.

Migration, rollback, lineage, concurrent report, privacy, exact export and browser checks use
temporary synthetic libraries. No private recording, live inference or paid call is needed.

# 0183 — Native batch early uploads, limits and plain status

Accepted 19 November 2026 (user decisions of the same day). Amends ADR 0085 (the native
selection barrier), ADR 0146 (the fixed two-worker native lane) and V187's fixed four uploads.
No schema migration: the limits are one JSON row (id 12) in the existing `settings` table and the
early-selection receipts live in the existing native batch journal. A live UCloud batch has not
been run with this behaviour; acceptance remains separate.

## 1. Plain-language batch status

Activity's native batch card showed raw UCloud states ("UCloud batch · RUNNING", "IN_QUEUE")
and a batch-level "Last log: model loading reported · log timestamp … (timezone unspecified)"
line, even after files had finished. The card now composes one sentence on each read
(`native_batch.plain_status`):

- before submission: "Uploading files to UCloud · 3 of 12 uploaded · 2 still being inspected",
  "Waiting to upload · 2 of 12 files still being inspected" or "All files uploaded · submitting
  the batch to UCloud";
- `IN_QUEUE` (and other non-running, non-terminal states): "Waiting in UCloud's queue for a GPU
  · 12 min", where the time is measured from when this app first observed that state
  (`ucloud_run.remote_state_since = {state, observed_at}`, recorded only on a state change from
  now on). An older run, or a state inherited unchanged across a restart, shows no duration: no
  timing is invented;
- `RUNNING`: "Transcribing on UCloud · 5 of 12 files reported done · 2 saved · last log 14:05";
- terminal: "UCloud finished · downloading transcripts · 3 of 12 saved"; completed: "UCloud batch
  complete · 12 of 12 transcripts saved"; stopped/failed batches keep their specific recovery
  message without any log note.

The log stays observational. "Reported done" counts exact processed-file log events (or saved
results) and never claims a saved transcript; saved counts come only from local results. Per-file
notes read "UCloud log: started 14:03" or "UCloud log: finished 14:05 · transcript not saved
yet". Times are when this app read the line, shown in this computer's local time, because the
log's own timestamps carry no time zone; the raw reported timestamp stays in the journal. The
model-loading event only dates "last log". Single-file runs use the same plain state words
without a duration. Raw states remain in `ucloud_remote_state` and the journal.

## 2. Native limits in Settings → UCloud

Advanced settings on the UCloud tab gain **Files uploaded at once per batch** (1–8, default 4)
and **UCloud Transcriber batches at once** (1–4, default 2), saved on change through
`GET/PUT /api/native-batches/settings` with their own status line and excluded from the
Settings unsaved-entries tracking. A PUT replaces only the fields it sends; invalid values are
refused before saving; an unreadable row falls back to the defaults. Neither follows the CPU
count: upload speed depends on the network.

Following ADR 0146, the `transcribe-native` lane now starts its maximum of four workers and the
saved batch limit gates new claims under the shared claim lock, counted with that lane's workers
that own work. Raising it applies at the next claim; lowering it never stops a running batch or
single-file run (each counts as one). The saved row and the in-memory limit are updated under the
claim lock. The upload limit is read when a batch run starts and kept for that run (its next
explicit Retry reads the current value). Downloads and early output reads keep their fixed
four-transfer window, so completed transcript buffers stay bounded as before.

## 3. Upload each file as soon as its own inspection passes

Previously a native selection waited until every file's original inspection and preflight had
settled before its batch existed (ADR 0085's barrier), so nothing uploaded until the slowest
inspection finished. Now a selection of two or more native files is an *early selection*:

- **Per-file admission.** Each file is claimed, preflighted and materialized on its own as soon
  as its inspection is ready. The first admitted file creates the selection's one batch; its
  manifest is the complete selection from the start (job IDs, recordings and `<job-id><suffix>`
  names are known at acceptance) and never changes. The batch records the selection
  (`native_group: {job_ids, sha256}`); a lookup by that digest inside the materializing write
  transaction finds it, and two batches for one selection are refused. Later files join it.
- **Uploads start at once.** The native lane may claim the batch after its first file joins.
  The coordinator uploads every joined file (bounded by the upload limit), then rechecks every
  second for newly joined files while others are still inspected.
- **Dropped files.** A file whose inspection fails is made terminal by the claim itself, with
  its own `source_error` outcome and no token; a preflight failure, cancellation or recovery
  cancellation likewise ends only that file. Such files never receive `native_batch_id`, are
  never uploaded and never join the submission.
- **Seal, then submit.** The transaction that gives the selection's last file its outcome
  settles it: terminal files become resolved and the batch journal records `dropped_members`
  and `membership_sealed: true` together. Only a sealed batch with every remaining member
  uploaded may write its submission intent and POST. A sealed or submitted batch admits nobody;
  a dropped file cannot ordinary-Retry into it (the existing "already materialized" refusal).
  Before the seal, Retry of a dropped file returns it to waiting and it may still join the same
  batch — never a second allocation.
- **Single survivor.** If, when a file is admitted, every other file already failed and no batch
  exists, it runs as a single-file allocation as before; admission claims files whose inspection
  already failed first, so a known sole survivor is not admitted into a batch. A batch whose
  other files fail later keeps one member; its manifest still names the whole selection.
- **Membership proof.** Each claim verifies, as the cohort barrier did, that every file still
  being admitted shares the request, project, provider and allocation, and admitted files carry
  the selection digest; joining rechecks the batch's frozen identity.
- **Preflight.** The first admitted file verifies the frozen allocation's funded product with
  UCloud metadata before any upload; files joining that batch reuse that verification instead of
  repeating the network read, as one cohort check did before. Protected approval, provider and
  source checks remain per file, and the coordinator's guard rechecks approval and source
  identity for every member before each upload and the POST.

Preserved, with tests: exactly one allocation owner per selection; no automatic resubmission of
an uncertain submission; native capture (ADR 0100) records each member's submission descriptor
only after sealing and only for joined members; frozen jobs and older batches (no
`native_group`) behave exactly as before and count as sealed.

**Stop and restart.** Stop (or shutdown) before submission stops the batch as today; files still
being inspected continue locally and then join the stopped batch in its stopped state without
uploading. Retry resumes the whole selection: missing uploads only, then one submission. Startup
recovery is unchanged: an interrupted unsubmitted batch waits for explicit Retry, and startup
reconnection still applies only to submitted owners; it skips dropped files, requires the seal on
early batches and binds `membership_sealed`/`dropped_members` into its identity.

**Deletion and cleanup.** Deleting a joined file stops the shared allocation with the existing
impact confirmation; that also cancels the admission of files still being inspected, so they are
counted as having nothing remote to keep. When no other joined file remains, the confirmed Delete
moves the owned folder (with any early uploads) to UCloud Trash exactly as ADR 0045/0101
describe. Ordinary success cleanup and automatic release require saved results only for joined,
undeleted members. Deletion scope, Activity retention and obligations treat dropped files like
deleted ones; unsettled files keep blocking their recordings' deletion as before.

**Activity.** The batch card lists every selected file: joined files with their stage, files
still being inspected as "Inspecting original", dropped files as "Not included" with their own
reason; neither is offered as a failed member to retry. Files still being inspected also keep
their source-preparation card, whose note now says that files which pass upload straight away.

## Not covered

Real UCloud throughput, queue timing and the upload limit's effect on a real network are not
measured. The "full GPU when no MIG is free" choice in the same TODO item still needs capacity
or queue evidence and is not implemented.

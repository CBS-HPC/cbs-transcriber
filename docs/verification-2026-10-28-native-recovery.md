# Native output recovery — 28 October 2026

**Outcome: four older native batch outputs recovered successfully from existing
allocation `12433016`; its exact owned folder was then moved to UCloud Trash.**
This is import/durability/cleanup evidence, not a new transcription or an accuracy
evaluation. No private recording names, transcript bodies, credentials, library
paths or complete private result identifiers are included here.

## Authorization and preconditions

The user authorized recovering older output failures. The coordinating agent
approved one exact existing-member Retry request, including the already frozen
success-cleanup behavior after all four imports were durable and the allocation
was confirmed terminal.

Before the accepted request:

- The serving app remained the isolated verified `c3c94e6` checkout, schema 12,
  with its original listener and no queued/running jobs. The development Store
  was never instantiated on this library.
- The native owner and all four members were failed after output import errors.
  The recorded remote ID was exactly `12433016`; a fresh read-only remote status
  check returned `SUCCESS`.
- Each frozen input had exactly one matching original JSON in the exact owned
  output folder, using its input identity, large-v3 model and allowed `da` suffix.
  The bounded listing was complete. No producer result or local raw response
  had yet been saved for these four jobs.
- All four recordings were ready and had no active work. Their frozen jobs had
  no enhancement follow-up or Runtime owner.
- Existing result/version/note metadata and active selections were captured
  privately before the mutation.

The existing Retry path retains the recorded remote ID. With that ID present,
the native worker skips its entire folder/input-upload/submission branch and
reconnects for output import. The explicit `replace=true` request acknowledges
adding results when reviewed text already exists; it does not replace that text.
Result insertion uses `activate=False`. Although Store can schedule a frozen
follow-up when one exists, all four of these jobs had no such follow-up.

## Request and completion

An initial request omitted the normal `X-CBS-Request: 1` application header and
received HTTP 403. The same-origin middleware rejected it before the route
handler. The owner timestamp/state remained unchanged and no job became active.

One corrected request with the standard application header, correct loopback
Origin and the approved `{ "replace": true }` body was accepted with HTTP 200 at
**13:00:12.942 UTC**. It acknowledged the existing owner and its four member jobs.
There was no repeated accepted request.

The worker saved all four outputs, appending four provider results and four
sequence-1 `provider_result` creation versions. Each creation version identifies
its exact producing job and has a stored payload SHA-256, with no predecessor or
source-version substitution. The recovered raw files total **3,284,980 bytes**;
every file's independently computed SHA-256 and size matched its durable journal
receipt and the previously listed remote size.

The batch completed with the same remote ID. Its cleanup journal records
`trashed`, stage `complete`, no error code/status, attempted at
**13:00:40.308 UTC**. A later independent read-only check again observed remote
`SUCCESS`; a complete one-page listing of the actual parent confirmed that the
exact owned batch folder was absent. UCloud Trash was not purged.

## Preservation checks

All comparisons passed:

| Evidence | Before | After |
| --- | ---: | ---: |
| Preexisting results | 15 | Same 15, metadata unchanged |
| Preexisting result versions | 27 | Same 27, metadata/hashes unchanged |
| Recording note versions | 4 | Same 4, revisions/hashes unchanged |
| Active result selections and recording revisions | 4 recordings | Exact match |
| Actual active result version heads | 4 | Exact IDs/sequences/hashes |
| Results across the four recordings | 15 | 19: only four recovered additions |
| New job rows | 0 | 0 |
| Queued/running jobs after recovery | 0 | 0 |
| Database schema | 12 | 12 |

The original job rows resumed and finished; no new job, GPU allocation, input
upload or inference was created. There was no worker restart, environment sync,
configuration rewrite, migration, reviewed-result activation or Trash purge.

Private metadata-only before/request/after receipts are retained under
`<library>/backups/native-recovery-<UTC>/`. They contain the exact local IDs,
producer/version references and hashes needed for a later preservation audit;
they contain no transcript bodies or credentials. This repository note does not
claim a full database backup, byte-for-byte research payload audit, speech-quality
acceptance or validation of unrelated retained runs.

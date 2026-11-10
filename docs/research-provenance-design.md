# Research provenance and version history design

Design version: 1. First delivery implemented on the separate research branch, 21 October 2026,
under ADR 0031 and SPEC V62–V65: immutable result/notes history, frozen version lineage,
read-only history/exact exports and metadata-first reports. Broader artifact/environment capture
and restore-as-new-version remain proposed. No real library has been migrated and no journal
compliance is certified. It develops the [research roadmap](research-workflow-roadmap.md).

## Goal and first useful delivery

Make a reviewed transcript recoverable as exact successive versions, and export a methods
draft that identifies the selected version, its known processing lineage and its gaps.
Keep normal review, comparison, provider jobs and NVivo exports working as they do today.

The first delivery should be immutable result/notes versions, read-only history APIs and a
metadata-first research report. It should not introduce a general event-sourcing framework,
remote service, automatic inference rerun or authenticated multi-user system. Complete raw
artifact capture, restoration UI and journal-specific reporting follow in separate slices.

## Current evidence

Code references describe the feature branch inspected on 21 October; function names are the
stable references if line numbers change during other work.

| Existing boundary | What is present | Missing evidence |
| --- | --- | --- |
| [`Store.save_transcript`](../src/cbs_transcriber/store.py#L393) | One transaction updates the active result and recording projection; original passage fields survive. | Intermediate reviewed text/speaker states are overwritten. |
| [`Store.save_notes`](../src/cbs_transcriber/store.py#L285) | Independent optimistic notes revisions and project snapshots. | Previous notes text and reasons are not retained. |
| [`Store.add_result`](../src/cbs_transcriber/store.py#L439) | Independent provider/import/enhancement results, unique producer job, atomic enhancement follow-up. | No immutable result version or exact version pointer for lineage. |
| [`Store.freeze_result`](../src/cbs_transcriber/store.py#L521) | One database snapshot freezes selected text/speakers for enhancement. | A mutable result ID alone does not identify its reviewed state. |
| [`import_transcript`](../src/cbs_transcriber/app.py#L492) | Raw source bytes are kept in `sources/` before adding a result. | Random saved source filename is not linked to that result; no stored digest. |
| [`run_enhancement`](../src/cbs_transcriber/enhancement.py#L711) | Frozen source, pinned local models/options and coverage; result metadata names the source result. | The source version and complete environment/artifact manifest are absent. |
| [`project_archive`](../src/cbs_transcriber/exports.py#L16) | Safe project-scoped ZIP, saved result identities and skipped-item manifest. | No immutable selection, rendered-output digest or methods history. |

Current JSON exports deliberately exclude discarded originals and settings. Retain that contract;
a research evidence bundle is a separate explicit export, not extra fields silently added to it.

## Proposed terminology

| Term | Meaning |
| --- | --- |
| Saved result | One import or processing outcome that can be reviewed. Its ID persists across edits. |
| Result version | An immutable observation of that result's exact text, words, timings and speaker mapping. |
| Current projection | Mutable convenience columns containing the selected result's latest state. |
| Predecessor | The previous reviewed version of the same result, separate from a processing input. |
| Source version | The exact result version used as input to a new enhancement result. |
| Artifact | A preserved recording, prepared chunk, raw response or imported source with a managed identity. |
| Observation | What this app captured at a stated time; it does not establish an earlier historical fact. |
| Evidence report | A versioned manifest and readable methods draft, with explicit unknowns and sharing choices. |

These terms are proposed for a later CONTEXT/ADR update. A speaker label describes a voice;
it remains distinct from verified personal identity. A content hash is an integrity check,
not proof of authorship, anonymization or resistance to an administrator modifying the library.

## Schema 5 to 6: immutable versions beside existing projections

Use two additive tables initially; do not rename or add columns to existing tables in this
slice. Existing code uses positional `INSERT INTO transcript_results VALUES (...)`, so
changing that table's column count would require wider migration and compatibility work.

`result_versions`:

| Field / constraint | Proposed purpose |
| --- | --- |
| `id TEXT PRIMARY KEY` | Generated immutable version ID. |
| `result_id TEXT NOT NULL` | Foreign key to `transcript_results`, removed only by explicit object deletion. |
| `sequence INTEGER NOT NULL`, `UNIQUE(result_id, sequence)` | Positive per-result order, allocated inside the write transaction. |
| `predecessor_id TEXT NULL` | Prior version of this result; first observed version has none. |
| `source_version_id TEXT NULL` | Frozen processing input version, checked to belong to the same recording. |
| `payload TEXT NOT NULL`, `payload_sha256 TEXT NOT NULL` | Versioned complete segments/speakers snapshot, including original fields. |
| `observed_at TEXT NOT NULL` | Server UTC observation time, not a guessed recognition/edit time. |
| `action TEXT NOT NULL`, `context TEXT NOT NULL` | Allowlisted action and structured change/provenance metadata. |
| `actor_kind TEXT NOT NULL`, `reason TEXT NULL` | `local_user`, `worker` or `migration`; optional bounded plain-text reason. |

`note_versions`:

| Field / constraint | Proposed purpose |
| --- | --- |
| `id TEXT PRIMARY KEY`, `project_id TEXT NOT NULL` | Version identity and owning project FK. |
| `recording_id TEXT NULL` | Owning recording FK; null denotes project notes. |
| `scope TEXT NOT NULL` | `project` or `recording`, checked against nullable recording ID. |
| `notes_revision INTEGER NOT NULL` | Existing independent notes revision at the accepted save. |
| `text TEXT NOT NULL`, `text_sha256 TEXT NOT NULL` | Exact Unicode notes and hash; never transcript input. |
| `observed_at`, `actor_kind`, `action`, `reason` | Same observation conventions as result versions, including no-change saves. |

Use unique partial indexes for project-note `(project_id, notes_revision)` and recording-note
`(recording_id, notes_revision)` identities. Validate recording/project ownership in the same
write transaction. Index result versions by result/sequence; resolve the latest version with
that index rather than an independently mutable head pointer. New tables use named-column
inserts. Keep every existing result, recording revision and notes revision unchanged by migration.

Serialize payloads with a documented local `cbs-result-snapshot-v1` envelope: UTF-8 JSON,
sorted dictionary keys, fixed compact separators, `ensure_ascii=False`, `allow_nan=False`.
Do not normalize transcript Unicode, punctuation, whitespace, numeric values or list order.
Hash these exact bytes with SHA-256 and retain the bytes needed to verify them. This defines
the app's encoding; it is not a claim of implementing a universal JSON canonicalization standard.
Hash notes as their exact UTF-8 bytes. Keep environment/provenance context separate from the
content hash so identical transcript content can be recognized without confusing its history.

## Migration and transaction boundaries

1. Acquire the existing exclusive library lease before workers start. Refuse unsupported
   newer schema versions; never let an older app silently mutate a newer library.
2. Back up an existing schema-5 database using the SQLite backup API, following the current
   migration pattern. A backup failure must leave the library unmodified. Do not copy only a
   live WAL database file. [Python backup API](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup).
3. In one explicit `BEGIN IMMEDIATE`, create tables/indexes and insert one observed baseline
   for every result and each current notes scope, including empty notes. Set schema version
   6 only after validation. Reopening a migrated library must create no additional baselines.
4. Label baselines `legacy_observed`, with migration observation time and history coverage
   `before_observation_unknown`. Preserve old `created`/`updated` values as unverified source
   metadata; do not turn them into edit times or invent erased intermediate versions.
5. Roll back the complete migration on any failure, keeping the backup and prior schema.
   Check existing active-result mirrors for consistency without silently choosing a winner.
   If inconsistent, fail with a curated recovery message before enabling writes.

For each accepted edit/save, open one Store-owned connection and `BEGIN IMMEDIATE`; check
the existing optimistic revision, append the version, update result/recording projections
and commit together. Append every accepted save, including an explicitly classified
`save_no_change`; exclude no-change saves from reported correction counts. A stale request,
validation failure or insertion error changes neither projections nor history. Report lock
contention without automatically repeating a paid or remote operation. SQLite permits one
writer and provides read snapshots; explicit transaction boundaries matter for multiple reads.
[SQLite isolation](https://www.sqlite.org/isolation.html),
[transaction behavior](https://www.sqlite.org/lang_transaction.html).

`save_notes` follows the same pattern using only its independent notes revision. Existing
project snapshot writes remain post-commit copies; a snapshot-folder error cannot undo a
durable version. Do not do hashing of large files, FFmpeg, network or model work inside a
SQLite write transaction or while holding the shared queue claim lock.

New project/recording creation also records its empty notes revision 0 in that object's
creation transaction. A new recording without a transcript has no result version until its
first result exists. Broader project name/classification/storage change history is a later
metadata-history extension; notes history must not be presented as covering those fields.

`add_result(job_id=...)` appends the first version in the same transaction as its result and
any frozen-source enhancement child. Its existing job uniqueness check runs first: an
idempotent retry returns the existing result/version without appending history or another
child. Keep native UCloud cleanup after durable raw/result/version commit; a failed version
write cannot permit cleanup. No remote allocation/cancellation behavior changes in this design.

Selecting an active result changes the existing recording revision/projection but does not
create a research correction. Do not turn search, playback, comparison or report download
into edit events. A future whole-version restore appends a new version with predecessor =
current head and `restored_from_version_id` = chosen historical version; it never rewinds or
deletes history. Existing original-passage restoration is recorded as its current edit action.

## Frozen source and processing lineage

Extend `freeze_result` to read the current result version ID and exact snapshot in the same
SQLite read transaction. Verify that the stored snapshot equals the version payload before
queueing. New enhancement snapshots include `source_version_id` and `source_payload_sha256`;
the result's first version records those identities as well as source result and producer job.
Add the first provider version ID to an atomic batch child's frozen input before commit.

Example: ASR result R/version 1 -> human correction R/version 2 -> timing repair E/version 1.
If the user edits R/version 3 while E runs, E still derives from R/version 2. Its successor
versions preserve this processing origin while linking to their own review predecessors.
Selected passage IDs/window offsets are part of the frozen processing context. Version/source
references must resolve within the same recording; reject forward/cyclic references.

Old jobs containing only `source_snapshot` remain executable. Record the frozen snapshot's
hash and `source_version_unknown`; never assign the source result's current head as though
it were the original input. A preserved old snapshot can substantiate its own wording without
establishing when or by whom an earlier version was reviewed.

Allowlisted actions: imported result, provider result, enhancement result (with whole-result
or passage-repair scope), word correction, free rewrite, original passage restoration, passage
merge, split and delete, passage speaker assignment, speaker rename, result Trash/restore,
notes save, no-change save and legacy observation. The implemented vocabulary is listed in
[research-history.md](research-history.md#recorded-actions). `context` identifies target passage/word
IDs and processing type without copying free-form upstream errors or credentials. Actor is
`local user (identity unverified)` for browser edits; do not read an OS username or infer
authorship. Optional reasons (suggested limit 2,000 characters) are local research metadata,
not mandatory for routine correction and excluded from shared reports unless explicitly chosen.

## Recording, import and raw-artifact linkage

First-slice versions carry curated artifact descriptors in their immutable local context:
role, managed recording ID, relative managed locator, SHA-256/byte size when known,
capture time and status (`captured`, `observed_later`, `missing`, `unlinked`, `unverified`).
Never save bearer URLs, headers or tokens. Locators are local-only and ID-resolved; reports
omit them. Missing evidence is represented explicitly, not filled by guessing filenames.

Follow-up capture should compute an original recording's hash while streaming its managed
copy, outside the database transaction. Persist a small completed input manifest before
publishing ready work. Link imported source UUID/extension and data hash directly when its
result is added. Link API raw responses by producer job/part index and original time offset;
link native UCloud JSON by the journaled run/result, before successful remote deletion.
Link enhancement requests, stage checkpoints and output with actual windows/model revisions.
Failed/interrupted runs keep their existing durable journals; a complete processing-outcome
ledger is a later append-only extension, not invented from the recent Activity list.

Where local artifact identity is already unambiguous, reports can verify its file hash by
streaming it without network access. Resolve paths under their managed recording/project,
reject links/traversal and detect file changes during verification. Missing/tampered files
produce a gap/error; never regenerate them by contacting a provider. A later verification
observation must be separate from an immutable version's original processing-time evidence.
Do not rewrite old versions to add a hash or assert historical association retroactively.

Capture app version/build commit and dependency-lock hash at new processing/review time,
plus worker package/device/model-revision metadata actually observed. Keep settings frozen,
using an allowlist. Mutable endpoint aliases and opaque remote model versions remain unknown.
Do not report the current environment as an old run's environment. Hashing and complete
artifact registration are separate implementation slices; initial reports must show their gaps.

## API and review interface proposal

- `GET /api/recordings/{id}/results/{result_id}/versions`: paginated metadata/history only.
- `GET /api/recordings/{id}/results/{result_id}/versions/{version_id}`: local exact snapshot.
- `GET /api/projects/{id}/notes/versions` and recording equivalent: independent notes history.
- Add optional plain-text `reason` to existing edit/notes requests, preserving old clients.
  Action and actor categories come from the handler/worker, not arbitrary browser assertions.
- Separate report creation request, for example `POST /api/projects/{id}/research-report`,
  with explicit version IDs, export format and inclusion choices. It creates no inference job.

Validate all ownership and version selections server-side; cross-recording/project identities
return 404. Do not expose historical text through the metadata list by default. Existing
recording revisions continue to protect selection/edit races; a version ID supplements rather
than replaces them. Future UI: History beside Review result, compare a historical version,
and an explicit Restore as new version action with draft/conflict guards.

## Privacy-aware report and export reproducibility

Produce a new ZIP with versioned `manifest.json` and plain Markdown methods draft. Read
selection, versions, notes metadata and available lineage from one SQLite `BEGIN` snapshot,
copy the bounded data and release it before rendering/streaming files. Apply project export
limits/pagination instead of loading unlimited histories. Reports never alter transcript
revisions or active selection. Default bundle contents are metadata/checksums only:

- Report-scoped recording/result/version aliases, selected version content hashes and lineage.
- Actual processing categories, language/model identity, captured software revisions, methods
  settings, timing coverage, manual-change categories and quality evidence where present.
- Explicit provenance coverage and unknown/missing/observed-later evidence; no implied perfect
  rerun or verified speaker identity. Methods prose is generated from these fields, not invented.
- Export renderer/format version and requested format; when text is explicitly included,
  hash the exact rendered bytes and name every archive entry safely and uniquely.

Default output excludes filenames, project/recording names, speaker names, notes/reasons,
transcript excerpts, original text hidden by edits, media, raw responses, account/job IDs,
absolute/runtime paths, endpoint URLs and all secrets. Even a content hash can be linkable;
metadata-only does not mean anonymous. Show an inclusion preview before sharing. Use a
field allowlist rather than attempting to redact arbitrary serialized job/settings objects.

An explicit `include_selected_transcripts` includes only the chosen current-view versions;
it does not imply permission to include discarded originals, history, notes or media.
Those require separate later inclusion options and warnings, never a single broad toggle.
Record the inclusion policy and hashes in the manifest. The app downloads locally; reports
never upload themselves or change Protected-provider policy. Researcher-controlled sharing
must follow the project's institutional rules and retention arrangements.

Reproducing an export means rendering the preserved version with the recorded renderer
and checking its bytes, or verifying bytes retained in an evidence bundle. Reproducing an
API inference is a different claim: preserve observed raw outputs and report that changing
services, stochastic inference or unavailable model revisions may prevent an identical rerun.
Never execute a provider merely to open a report. The methods draft needs researcher review;
it is not a universal journal-compliance certificate. The report itself may vary in generation
time; content/export hashes exclude that report envelope and identify the recorded bytes.

## Small implementation slices and verification gates

| Slice | Deliverable | Required verification before continuing |
| --- | --- | --- |
| 1 | Accepted ADR/SPEC, schema-6 backup/migration, version tables and atomic Store writes. | Legacy preservation, append-only saves, failure rollback, conflicts and no-change classification. |
| 2 | Frozen source version linkage, job-result idempotency and history read APIs. | Source edited during run, old job compatibility, ownership and batch follow-up atomicity. |
| 3 | Metadata-first report plus explicit selected-text export. | One-snapshot report, hashes, redaction, offline version export and skipped/missing evidence. |
| 4 | Original/import/raw/checkpoint capture and immutable verification observations. | Streaming limits, safe locators, changed/missing file detection, no premature remote cleanup. |
| 5 | Historical comparison/restore UI, notes history UI and reviewed methods templates. | Draft/conflict navigation, append-only restore, accessibility and researcher pilot. |

Start with slices 1-3 as the smallest reviewable capability, while visibly declaring artifact
and pre-migration gaps. Do not label T43 complete until its agreed history/report acceptance
has passed. No actual migrations, processing, model downloads or paid tests accompany this design.

## Exact automated acceptance plan

New `tests/test_research_history.py` and report tests should establish:

1. Schema-5 fixture migrates once with a valid backup; every old table/column value, original,
   result, job, provider choice, policy, revision and credential-store presence is preserved.
   Migration failure rolls back; repeated startup creates no baselines; newer schemas refuse writes.
2. Word/free/original-restore/speaker edits retain each exact previous segments/speakers
   dictionary. Danish Unicode, punctuation, timing-invalidated states and originals survive.
3. Stale/conflicting requests append nothing. Injected history/projection/follow-up failures
   roll back the whole transaction; snapshot-folder failure retains the committed version.
4. Every accepted notes save appends its independent revision; no-change saves are labelled;
   notes never change transcript revisions or enter inference/transcript exports.
5. `add_result` retry produces one result, first version and enhancement child. Existing
   result activation creates no correction version. Restart retains versions and ordering.
6. Queue an enhancement, edit its source, complete/retry: exact frozen version/hash remains
   linked. Old snapshots work with an explicit missing-version flag. No cross-recording links.
7. History endpoints paginate, enforce ownership and render literal text; a stale UI response
   cannot replace a newer result/draft. Historical restore eventually appends rather than rewinds.
8. Concurrent edit/note save during report capture yields one coherent selection/lineage.
   Included bytes and payload hashes verify; a changed selected version cannot be substituted.
9. Default report excludes sentinel private names, notes, reasons, paths, original text,
   tokens, URLs and raw fields. Selected-text opt-in includes only selected text. Treat CSV/HTML
   rendering as untrusted, and retain safe unique ZIP paths and explicit skipped-item reasons.
10. Legacy/import-link gaps stay unknown. Missing/changed artifact verification makes no network
    request. An offline historical export matches its recorded bytes under the pinned renderer.
11. Explicit deletion removes the selected object's retained history under the agreed policy,
    while unresolved UCloud journals still block deletion. No background compaction prunes history.

Extend `test_provider_results.py`, `test_feature_followups.py`, `test_notes.py`,
`test_speaker_edits.py`, `test_exports.py`, `test_bulk_passage_alignment.py` and native cleanup
regressions. All automation uses synthetic/public fixtures and temporary libraries. Real
researcher review against selected journals and actual backup/restore remains separate acceptance.

Open design choices for review: retention/legal deletion rules, optional researcher-supplied
identity, reason prompts, practical history/report size limits and which evidence may leave
a Protected project. Default to preserving local history and minimizing shared contents.

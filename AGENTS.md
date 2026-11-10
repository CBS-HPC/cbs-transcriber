# Working on CBS Transcriber

Read `SPEC.md` for the accepted behavior and `TODO.md` for current progress.
Keep both documents current when behavior or implementation status changes.
Read `docs/decisions/README.md` for architecture rationale and `CONTEXT.md` for terminology.
Add numbered architecture decisions when changing persistence, timing, or job semantics.

## Installing on a new machine (Windows and macOS)

Follow README "Install and start". The ordinary-user route is **uv tool install from a
versioned GitHub release wheel and its matching constraints file**; no Git is required.
Repository: https://github.com/CBS-HPC/cbs-transcriber. The MVP wheel release is v0.1.0;
verify the actual release and installation receipts before claiming acceptance. Publishing and
repository visibility changes require explicit user authorization; never embed tokens.

1. Check `uv --version` first; reuse an existing uv 0.9.29 or later. If missing or older,
   provide uv through CBS-approved routes. Managed prerequisites may need IT.
   Official installers: Windows `powershell -ExecutionPolicy ByPass -c "irm
   https://astral.sh/uv/install.ps1 | iex"`; macOS
   `curl -LsSf https://astral.sh/uv/install.sh | sh`. Reopen the terminal and check `uv --version`.
   If the new version is shadowed, inspect `Get-Command uv -All` on Windows and invoke the
   official installer's `$env:USERPROFILE\.local\bin\uv.exe` explicitly. Do not change Conda
   or global PATH silently. Run `uv python install 3.13` for per-user uv-managed CPython 3.13;
   the app's tool environment remains separate from existing Conda/system environments.
2. Use the README's explicit versioned HTTPS wheel/constraints URLs once publicly available:
   `uv tool install --python 3.13 --no-config --link-mode copy --constraints <release-constraints> <release-wheel>`.
   Copy mode avoids observed Windows Cloud Files/hardlink error 396. Preserve this flag in
   update/retry commands; it does not change runtime behavior or dependency pins.
   While access is private, obtain both files through a signed-in browser and use local paths.
   Do not require GitHub CLI, add a global PyTorch index or drop the release constraints.
3. Start `cbs-transcriber` at http://127.0.0.1:8765; keep the terminal open, Ctrl+C stops.
   The release packages MVP; `--profile mvp` selects it explicitly. The profile hides unfinished
   features but does not reduce dependency downloads. If PATH is missing, use
   `uv tool update-shell` and reopen the terminal. Stop an earlier app process before opening
   its library from the installed copy; closing a browser tab does not release the lease.
4. Optional Desktop launcher on Windows/macOS:
   `cbs-transcriber --create-shortcut --shortcut-location desktop --profile mvp`.
   Windows creates a .lnk; macOS creates an executable .command file. Both currently use
   system-default icons; custom icon assignment is not implemented in v0.1.0. Omit the
   location option for Start menu / ~/Applications. See docs/installation-shortcuts.md. Never
   silently overwrite a launcher, install dependencies, migrate a library or start processing.
5. Update only after saving edits, finishing/stopping jobs, making a complete backup and
   stopping the app. Install the next approved wheel plus matching constraints, adding
   `--reinstall-package cbs-transcriber`. Do not substitute an unqualified `uv tool upgrade`.
   Uninstall only the tool with `uv tool uninstall cbs-transcriber`; libraries/projects remain
   separate. Recreate launchers after moving/replacing their installation.

Supported scope: Windows x64; Apple Silicon/macOS14+ with CPU local inference packages;
Intel macOS core/API/UCloud without the incompatible local inference stack. Clean-device
Windows/Apple Silicon/Intel acceptance remains pending; package plans are not OS acceptance.
Wheel constraints pin release versions and selected artifacts, but tool installation does not
consume the project uv.lock or establish its complete artifact-hash guarantees.

Developers retain clone + `uv sync --locked` + `uv run --locked cbs-transcriber --profile mvp`
in a fresh unsynced folder. Git is only needed for cloning/updating source. A source ZIP is a
secondary fallback with the same locked project commands; do not copy old .venv folders or
reset unrelated changes. Never synchronize/reinstall a running app's environment.
No separate Node/FFmpeg installation is needed. Developer browser checks additionally need
`uv run playwright install chromium`; users do not need Playwright.

## Development

Public distribution uses a clean source snapshot. The earlier development history is retained
privately as CBS-HPC/cbs-transcriber-private-history; never push its old ancestors, branches or
tags into the public repository. In the legacy development checkout, keep origin pointed at
that private history and use a separate public remote or independently initialized snapshot.
Future public commits must descend only from the clean public main history.

- Python 3.13; use the repository-local `.venv` through `uv`.
- Install: `uv sync --locked`. Launch: `uv run cbs-transcriber`.
- Verify: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
- Browser acceptance: `uv run playwright install chromium`, then
  `uv run pytest -m browser -o addopts=''`.
- MVP variant: `uv run python -m cbs_transcriber.release_profile mvp --label "MVP rc1"` on the
  release branch only; run the app with `--profile mvp` or `CBS_TRANSCRIBER_PROFILE=mvp` to try it.
- Keep `uv.lock` in Git. Never edit the lockfile manually.
- Frontend files are formatted with Prettier 3.6.2 and checked out as LF via .gitattributes;
  no frontend build is required. Do not change binary brand/font attributes.

## Architecture

- FastAPI serves a build-free HTML/CSS/JavaScript interface and local JSON API.
- `release_profile.py` (ADR 0145) is the one switch for unfinished features: `--profile`, then
  `CBS_TRANSCRIBER_PROFILE`, then packaged `catalogs/release.json`, then full; main ships full.
  Hide entry points to new work, never data or recovery: mark static UI with `data-feature`
  (CSS keeps it hidden), check `featureOn()` in dynamic UI, and refuse new work on the server
  (route table or handler, 403 `feature_disabled`). Keep reads, exports, Stop/Retry/Reconnect/
  cleanup and frozen jobs open. Tests pin full; MVP tests set the profile explicitly.
- `transcripts.py` owns import, timing normalization, editing, and NVivo export.
- It also renders current-view subtitle/CSV/JSON exports; `exports.py` builds project ZIPs
  from one database read, with safe unique paths and an explicit skipped-item manifest.
  Original recordings require an explicit unchecked opt-in, independent of transcript formats.
  Stream only owned regular originals with bounded reads and source-identity checks; keep
  large copies outside shared queue/snapshot/writer locks. Never silently widen selections.
- `export_tasks.py` (ADR 0158) owns server-lifetime background ZIP preparation: one active,
  three retained ready downloads, frozen read snapshot before 202, separate Activity controls.
  Preserve source-operation admission inhibitors and shutdown drain under the serving lease.
  Stream ready owned descriptors with unconditional response-dispatch cleanup; no ZIP Blob or
  schema migration. Ordinary editing remains available; restart continuation is not promised.
- Manual edit hot paths (ADR 0159) may reuse canonical-format validation only with a fresh
  hash and fresh parsed objects; live ownership/context/CAS and inverse checks stay current.
  Targeted merge/split Apply can stop after its exact proof; advisory and Delete restoration
  keep full bounded scans. Preserve prewriter UTF-8/type/finite checks and synchronous snapshots.
  Native-JSON mutation responses retain the legacy _sa filtering/error fallback; GETs stay unchanged.
- `store.py` owns SQLite persistence. `media.py` owns FFmpeg operations.
- New original-v1 inference (ADR 0075/T87) derives from the immutable managed original,
  independently of browser playback. Freeze at fresh request boundaries, never the shared
  insertion seam used by frozen follow-ups. Missing policy keeps exact legacy behavior.
  Verify owned paths, source identity, explicit first stream and durable prepared receipts;
  keep heavy I/O outside queue/DB locks and never overwrite legacy recording duration.
  Original input selection and playback-readiness separation are distinct implementation stages.
  T87a's synchronous enqueue inspection is an activation hold under V8 until T87b durable
  background admission is implemented. Preserve one compatible native allocation per selection.
- Schema-13 `original_inspection.py` (ADR 0082) is an internal prerequisite, with no automatic
  lane or public producer admission. Only its generation/job/epoch CAS updates the reserved
  source-inspect job/cache; generic mutation, dispatch, Retry/Cancel and recovery cannot.
  Keep complete byte anchors across failure/cancel/retry, including Cancel after hashing.
  Claim/run/recover require the caller-held same-Store lease until drain; never re-enter the
  lifespan RLock on a worker. Hash/decode outside locks, bound reads/JSON and drain the decoder
  before propagating guard failures. Raw cache stays out of API and project snapshots.
- Saved results own transcript edits/speakers; recording transcript columns are the active
  result view. Update both atomically. New jobs append results, never overwrite reviewed text.
- Working roles (ADR 0093/0133) pin immutable version IDs/hashes, separately from the active
  review view and content revision. Schema18 records automatic events with a truthful system
  actor; first results and processing successors follow the frozen role/source/head fences.
  Explicit edits require their own consent/revision guard. Default project exports use that exact
  payload and manifest missing designations; Trash clears roles in the same transaction. Include
  the independent role revision in destructive-operation snapshots.
- `working_existing.py` (ADR 0141) designates an untouched sole existing result at startup via
  `move_automatic`; any role event, edited passage, extra result/version, unsettled job,
  uncompleted authority or restore evidence skips. Snapshots refresh only owned link-free
  folders; staleness compares bounded snapshot roles with SQLite, never file times or a new
  marker. Retain foreign or unverifiable snapshot bytes with a warning. Keep startup bounded.
- Fresh local enhancement and bounded timing capture real sealed request/audio/stage artifacts;
  absent policies preserve legacy gaps. Local timing Apply commits version/mirror/job receipt
  atomically, validates lost-reply successors and skips newer timing maps. Keep original producer
  meaning, named voices and unselected dictionaries; reports validate without runtime/file reads.
- Schema-8 result Trash changes visibility with revision checks and explicit history events.
  Retain result rows, raw files, versions, frozen jobs and source lineage. Active removal chooses
  the newest live result or clears its projection; restore activates only when none is active.
  New selection/export/processing uses live results, while historical reads and frozen retries
  retain evidence. Never recreate or restore a trashed result through producer idempotency.
- Schema-6 history appends exact result/notes versions atomically with projection writes.
  Explicit enhancement merges (ADR 0068) use the immutable incoming creation version to prove
  correspondence with its frozen base; IDs alone are insufficient after renumbering/splits.
  Preserve current wording/originals/structure/named voices; UNKNOWN never replaces a known
  label. Preview reads one snapshot; Apply recomputes under one writer transaction and appends
  result_merge with exact base/incoming/creation references. Keep source_version_id's original
  producer meaning. Reports verify these parent hashes/ownership and expose aliases only.
  Back up schema 5 and validate mirrors before migration; legacy observations do not invent
  earlier history. Frozen enhancements identify exact input versions/hashes. Historical reads
  verify payload integrity; reports use one bounded read snapshot and a metadata allowlist.
  Keep development migrations on synthetic libraries. Authorized real-library activation uses
  a SQLite backup, idle shutdown and preservation checks before browser reload.
- Explicit comparison wording adoption (ADR 0074) requires fresh one-to-one passage preview
  and fingerprint-bound Apply. Use the literal free edit only; preserve destination bounds,
  speaker, originals, producer lineage and unselected passages. Never copy source timing or
  queue alignment. Reports verify the exact source and reconstruct the complete successor
  from the predecessor before projecting version/passage aliases. No guessed automatic merge.
- New import receipts (ADR 0073) bind exact preserved JSON/CSV bytes to their verified parsed
  payload. Use Store.import_transcript and the explicit add_result receipt keyword; source
  dictionaries cannot self-assert capture. Hold snapshot_lock through owned-file verification
  and commit, preserving queue → snapshot → SQLite ordering. Do not scan/backfill old sources.
  Preserve receipt inheritance. Reports compare source/context in one snapshot with a separate
  bounded source budget, emit scoped aliases and not_checked availability, and never read raw
  files or imply full backup/API/runtime evidence. Keep all managed-child link/collision guards.
- Import receipt parser generations (ADR 0111): schema 1 retains legacy normalization; fresh
  imports explicitly use schema 2 for strict CSV and numeric speaker zero. Physical/portable
  validation uses only the claimed generation and includes it in cache identity; never fall back
  or rewrite historical receipts. Repin parser and worker sources together when modified.
- Schema-7 topic coding is a local research prototype, separate from transcript edits/ASR.
  Bind runs to exact approved codebook/source versions; validate literal code-point evidence.
  Keep suggestions immutable and reviews append-only, including scoped retry receipts through
  failed UI refreshes. Import raw JSON through strict bounded validation; never imply inference.
  Exports include current reviewed evidence, excluding private review reasons and unreviewed text.
- Passage repairs freeze selected IDs, decode only their actual bounded intervals, checkpoint
  per target and append one combined result. Preserve every unselected passage dictionary.
  The editor's "Add word timing"/"Repair all" tags instead queue in-place timing jobs (ADR 0056):
  one job per request, several may wait, each tag shows only its own passage's job, and output
  joins the same result as one revision-checked timing_realign version. Every untimed passage
  shows that same tag; unbounded ones are aligned with adjacent unbounded passages only inside
  the gap between known neighbour times (<=60 s), never storing the gap as timing (ADR 0058).
  The read-only comparison shows no timing actions.
  One local cached-model worker handles them sequentially and expires after five idle minutes;
  close it on cancellation, identity change, failure and shutdown. Never inherit credentials.
- `repair_sources.py` (ADR 0077) inspects frozen source/projection evidence in one bounded DB
  snapshot. Full-version, actual snapshot and selected projection are distinct scopes. Preserve
  historical/Trash access; never interpret this internal receipt as audio mapping, upload approval
  or current validity. Keep IDs internal, parameters hash observational and media/runtime/network untouched.
- `repair_bundles.py` (ADR 0078) is an internal fixed-language alignment-only primitive.
  Require recorded original-v1 extraction receipts, exact frozen source/runtime and bounded
  unique regular files. Canonical PCM bundles publish create-if-absent markers on hard-link-capable
  volumes; incomplete generations remain. Offset validated nullable words once with one passage
  owner. Trusted in-process validation is not public upload/routing/remote-execution authority.
- Worker JSON writes reuse exact unchanged bytes, preserving frozen retries with open Windows
  readers. Changed writes remain atomic and errors propagate; never delete the target first.
- Partial alignment retains real source passage bounds. Playback may highlight individually
  valid words while timing stays incomplete; free-text edits disable stale word playback.
  Structural outputs mark current word maps separately from the general edited flag and
  recompute completeness per half. Free rewrites clear that marker; legacy edited/incomplete
  maps remain conservative. Playback and research coverage share this distinction.
  Legacy replay_windows are read-only frozen source context, never inferred fragment timings.
  If no passage/source bounds exist, retain Replay with an explicit full-recording tooltip;
  never assign that interval to the untimed fragment or enable repair/highlighting from it.
- Project/recording notes use independent notes revisions, never transcript revisions or inputs.
  Treat text literally, guard drafts/late replies, and preserve schema-4 data during schema-5 migration.
  Card/review notes share one API; freeze pending text/baseline/revision, recover failed drafts by ID
  and reject stale replies. Route owns the navigation guard; confirmed discard clears retained UI drafts.
- `providers.py` owns API settings and OpenAI-compatible requests.
- `native_capture.py` (ADR 0100) freezes native-artifact-v1 only in admission prepare.
  Before POST each served job records a credential-free submission projection (withhold
  unowned free text by name); the journal keeps its digest. Policy jobs keep exact
  downloaded output bytes; never backfill. Batch rows own the journal, members their
  evidence. Pinned in the portable checker: repin after edits.
- `provider_capture.py` (ADR 0097) freezes api-artifact-v1 only in admission prepare for
  fresh API transcription. Record each credential-free descriptor before upload; publish the
  observed normalized output with its part; never backfill preserved outputs. Store creation
  binds exact parts/assembly via the explicit keyword. Its pinned source runs in the portable
  checker, so repin semantics after edits. Native Transcriber capture follows ADR 0100.
- `catalog.py` reads packaged, pinned speech candidates offline. Selection never deploys a model.
  OpenAI/native presets share provider_models.json; CBS_TRANSCRIBER_MODELS may override it.
  Catalog schema 2 separates openai, ucloud_transcriber and ucloud_cbs_runtime; only the latter
  contains engine whisperx. Combine both UCloud groups for connection profiles, never native
  app enumeration. Schema-1 overrides normalize in memory without changing source files/IDs.
  Validate before startup; preserve custom configurations and frozen jobs.
  UCloud vLLM saves use HF owner/model as served ID; reading old queued snapshots keeps identity.
- Complete native B200 saves/backfill append canonical model profiles atomically. Preserve existing
  rows/denials, selected IDs and frozen jobs; backfill offline inside the library lease before workers.
  OpenAI saves append missing catalog profiles too. Provider/Model dropdowns select real profiles,
  applying enable flags and Protected policy in both groups/models. Built-in setup is connection-only;
  Basic Available models lists are authoritative for the exact group. Missing flags mean enabled.
  Fresh catalog rows may explicitly default unchecked; preserve saved choices and frozen jobs.
  A newly created library bootstraps the OpenAI catalog default, never rewrite legacy defaults.
  Reject disabled models for new selection/enqueue, preserving frozen runs/retries and saved results.
- Opt-in realignment (ADR 0052) is the one same-result timing write: a local align-only job
  stores words on the job; the idle editor applies a revision-checked `timing_realign` version
  only if the passage text/bounds still match. Never change text; stale timing is never applied.
- Structural passage edits require active result/revision and atomic history/projection updates.
  Preserve exact Unicode text/unselected passages; split only at real word boundaries or an explicit
  interior audio position. Merged differing speakers require a deliberate label; never invent timing.
- Named configurations share credentials per endpoint. Preserve `/api/settings` as the default
  configuration interface for existing clients; queue jobs freeze provider identity/settings.
- Known official OpenAI models determine request formats; custom backends retain explicit
  capabilities. Model visibility checks use only `/models`, without uploading audio.
- UCloud vLLM is separate from UCloud Transcriber. Never send UCloud/HF setup tokens to
  inference, read another workspace's .env, or imply model listing verifies ASR capability.
- `ucloud_native.py` uses the released UCloud API project's client and template builder.
  Token-first discovery uses released setup helpers plus read-only app/product metadata.
  Resolve account ambiguities explicitly; freeze identities before enqueue. New native setups
  and templates require validated compute; validate metadata/parameters before upload.
  The verified transcriber/Batch flavor is WEB-capable: require its declared boolean jupyterLab
  switch and force it false, including inherited templates. Exclude arbitrary WEB/GUI apps.
  Resolve only known default field aliases during discovery; preserve explicit overrides and
  freeze resolved field names. Do not rewrite existing queued settings. See ADR 0036.
  Fresh native Transcriber requests use the B200 1g MIG without full-GPU fallback; preserve
  template bindings, frozen legacy requests, Runtime identities and approval checks. Freeze the
  execution product with fresh admission, reject Full-B200-bound templates for new work with a
  reconnect instruction (ADR 0124), and use it for native
  submission, grouping and provenance, and validate exact funded identity without GPU fallback.
  The public MIG test covered only two short large-v3-turbo clips; other models/workloads remain
  unverified. Require a unique compatible funded identity (or an exact existing reference); absence
  or ambiguity blocks setup. New-job API requests require its frozen full reference. Reconnect
  older configurations before new jobs; retain already frozen legacy runs without rewriting them.
  When no project is selected, new setups may default to the exact token record's eligible
  owner project. Ownership is not permission scope. Unavailable metadata retains choices;
  preserve explicit selections and reject malformed/duplicate ownership. Never expose token
  bodies in diagnostics.
  Checkpoint submission intent before POST, retain the remote job ID, and never automatically
  resubmit an uncertain allocation. Shutdown leaves native remote jobs running; explicit
  cancellation affects only this local run's recorded remote job. No live jobs during tests.
  New configurations clean their owned remote input/output tree after validated local save.
  Journal pending before result commit, flush raw bytes to disk, then move only the owned folder
  to UCloud Trash and confirm absence by listing its parent (providers lack DELETE; ADR 0045).
  Reject fresh folder conflicts; preserve pending cleanup beyond Activity limits. No Trash purge.
  Identical selected native jobs share one schema-9 batch allocation; the batch row owns the
  remote journal while each member keeps its own result. Retry/Stop/cleanup act on the batch.
  Activity groups members by that allocation and uses its canonical state/progress/update time;
  keep per-file outcomes and unresolved obligations visible after Clear finished. Match native
  output names by exact input UUID and frozen submitted model/language; auto language permits
  known language tokens only and ambiguity fails closed. Cleanup receipts include safe stages.
- Native UCloud control tokens use credential_scope(), shared only for the same server.
  Transfer sessions use separate clients and transient tokens, without control Authorization.
- Parallel parts checkpoint explicit indexes; the coordinator alone updates job chunks.
  Drain accepted requests after error/cancellation and retain the library lease during shutdown.
- `local_concurrency.py` (ADR 0146): media, inspection, API and admission lanes start their
  maximum threads (8/8/8/4); saved or automatic limits gate new claims under the shared claim
  lock, never stopping running work. Automatic: media = logical CPUs − 2 in 2..8, inspection =
  CPUs/2 in 2..4, API 6, admission 2 (user decisions 6 November). Native Transcriber keeps its own two-worker lane. All workers share the
  library lease; cancel only the owner. Idle siblings coalesce polls; notify() still wakes
  claims at once. Keep WhisperX/UCloud/Runtime lanes fixed. Provider-wide capacity is later work.
- Project classifications restrict providers through explicit approvals, including local APIs.
- New UCloud configurations default to allowed for Protected; OpenAI/Custom default off.
  Preserve saved choices, including explicit denials; never reapply defaults to existing providers.
  Check current approval and frozen endpoint/model identity before each protected upload/retry.
- Provider transports must not follow redirects. Use the official SDK's explicit default
  client with redirect following disabled; test that path as well as injected HTTP clients.
- Keep unresolved UCloud journals on deletion and visible beyond recent Activity history.
  Reconnect only an exact unique run name; absence reconciliation requires explicit user
  confirmation, and every later POST resets that receipt. See ADR 0021.
- Offer only Standard/Protected. Preserve schema-3 data and avoid widening partial legacy
  approvals during consolidation. Comparison alignment is local/read-only and creates no timing.
- Schema-11 collections (ADR 0060) are project metadata: one per recording, never move files,
  deleting keeps recordings (Unsorted), moves/uploads stay within the project. Exports and
  reports take the shown collection; ZIP folder names are sanitised and numbered.
- `backup.py` (ADR 0061) writes verified backups (backup-API snapshot, integrity check,
  hashed originals manifest); never include keys, tokens or the server lock.
- `portable_transport.py` (ADR 0081/S1a) verifies sealed bytes only in caller-owned quiescent
  staging storage against a caller-trusted commit hash. It never opens SQLite/Store or paths
  from file mappings, mutates packages, or grants restore/upload/cleanup authority. Preserve
  semantic not_checked fields and restore_ready=false; full S1/T91 remains pending.
- `portable_rows.py` (ADR 0083/T91b0) hashes only caller-observed rows against the pinned
  schema-12 catalog. Preserve typed framing, literal text/float bits and total PK order;
  no Store/SQLite/input filename access or capture/restore authority. S1b's contained opening
  protocol is separate (ADR 0088); codec tests alone do not establish database observations.
- `portable_database.py`/worker (ADR 0088/T91b1) contain synthetic-only database-claim checks
  in a captured-source child. Verify heap accounting before opening objects; never set a
  SQLite global cap in the serving process. Preserve strict schema-first gating, bounded I/O,
  drain/reap/close and unchecked history/package/security/restore scopes; no public admission.
- `portable_package.py` (ADR 0090/T91b2) composes its own frozen transport receipts with
  contained database checks, then rechecks every inventory/witness after child closure.
  Public transport behavior stays compatible; media bodies are hashed once. Captured bytes
  are immutable, the filesystem is not: require quiescence and retain explicit semantic,
  security, capture, quarantine and restore gaps. Accept no caller capture/claims authority.
- `portable_payload.py` (ADR 0091/T91b3) observes only supplied snapshot-v1 bytes, with fixed
  lexical budgets before JSON decoding and streaming canonical comparison. Preserve opaque
  inner JSON and exact numeric/Unicode spellings; entry counts are syntactic. Keep guards
  outside curation catches, resource refusal distinct from corruption, and all history/DB/
  ownership/restore scopes unchecked. No Store, reader/protocol or application-writer changes.
- New projects choose managed subfolders. The central database stays authoritative; project
  JSON/NVivo snapshots are additional copies. Preserve legacy paths during migrations.
- Existing-project moves hold queue/snapshot locks, block active work, copy before switching
  SQLite, and clean only resolved ID-owned paths. Failed copies retain originals; a committed
  target is never removed on finalization failure. Never move the central database or keys.
- Project settings stage name, classification, language and storage until Save (ADR 0132).
  Preserve expected-value/revision fences, explicit move confirmation, honest partial or
  uncertain outcomes and the durable browser-local warning until explicit acknowledgement;
  an immediate refresh is not proof that an unconfirmed request has finished.
- `jobs.py` owns the durable local queue. No external queue service is required.
- `passage_review.py` owns schema19 manual review metadata, separate from text/history/Working.
  Bind marks to the exact saved passage hash and independent row revision; stale Checked becomes
  Needs review. Keep review changes in deletion fingerprints and complete backup/restore, and
  migrate only under the library lease with a prior-schema backup. Do not snapshot whole projects
  or append transcript versions for marking. Preserve editor drafts and late-response fences.
- Native batch transfer pools accept at most four file tasks per batch, with independent clients.
  The coordinator owns receipts/imports; drain accepted tasks before releasing the serving lease.
  Separate waiting/upload/remote-processing/download/save stages and never imply per-file ASR progress.
  Activity groups only verified project/upload/request identities and exact producer follow-ups;
  native singleton/shared and Runtime owners take precedence. Use one read snapshot for raw
  rows, group expansion and phase projections; retain native inputs before closing it. Files,
  model requests and stages are separate counts. Restored rows are excluded before bounds;
  overflow disables group actions, never silently narrows their scope. Retained cleanup owners
  outlive recent history. Never invent grouping for legacy jobs lacking a durable request ID.
  Batch Activity may display a current verified inspection as a request stage, but never transfers
  its ownership or hides independent/shared/error/upload-group inspection controls (V176).
- Delete cancels the captured recording/project jobs through `deletions.py` (ADR 0092).
  Shared allocations affecting other recordings require explicit impact confirmation. Keep
  finish-only continuations, worker/control drain and current metadata fences; Stop never
  grants remote output disposal. Network controls run outside queue locks, while ephemeral
  job/allocation slots exclude concurrent Stop/Retry. Treat HTTP202 as pending in the UI.
  Local staging promotes the existing HTTP mutation slot before taking queue/snapshot locks,
  drains earlier mutators and rechecks recovery after waits. Admission waits use a separate
  one-thread limiter so earlier endpoints can finish; recursive cleanup releases exclusivity.
- `deletion_files.py` (ADR0102) journals exact local targets before exclusive same-volume
  staging. Never recursively unlink before SQLite Delete commits. Re-read database ownership
  after uncertain commits: live means file-only rollback, absent means cleanup, mixed remains
  unresolved. No manifest phase resumes logical Delete. Register active cleanup before
  releasing claim locks; settle recursively outside queue/snapshot/writer locks. Recovery runs
  before profile backfill/workers. Retain independent Activity Retry; block unresolved live
  mutations and capture/move/switch while journals remain. Borrow the serving lease on workers,
  never re-enter its RLock; take nonblocking local-action ownership before queue/snapshot.
  Keep Cloud reparse, exclusive-rename, process-crash and storage durability limits explicit.
- `library_deletion.py` (ADR 0144) permanently deletes only a listed, non-current library no
  server holds. Preview never opens the target database in place or creates/writes a target
  file (not even `server.lock`): read a private copy, walk without following links. Unsettled
  work, unreadable evidence and links block; external project folders are deleted only when
  exclusive, otherwise kept and listed. Recheck the fingerprint under the target lease, publish
  the reservation in the app folder before any change, stage by same-volume renames, commit,
  then remove. Store/launcher refuse reserved paths before creating anything and under the
  lease. Recovery restores before commit and finishes after it; never test on a real library.
- Original-input admission (ADR 0085) accepts metadata-only intents, then uses the bounded
  source-inspection and admission lanes (ADR 0146) alongside playback/API processing. Claim
  scans skip intents still waiting on their own pending inspection. Keep the lease
  through drain; original readiness is independent of playback. Native selections wait for
  every source/preflight outcome and create at most one owner; resolved failures cannot
  ordinary-Retry into a second allocation. Keep input_acceptance immutable through checkpoints.
  Quarantine malformed/non-object job params without resetting raw evidence or starving healthy
  recordings. Intent Cancel/Retry/Prepare use frozen phase/epoch/fingerprint authority. Explicit
  preparation creates idempotent successors from exact frozen choices; never retarget to current
  settings or grant consent through ordinary Retry. One Prepare covers every stopped intent of
  the same non-native request with the exact same accepted choice (ADR 0098).
- `repair_cloud.py`/`repair_remote.py` (ADR 0086) execute selected alignment bundles API-only on
  one prepared MIG singleton. New large multi-unit requests need an already-ready original
  receipt; single/small and legacy/uninspected requests keep the explicit local/Stop fallback.
  Prepare only bounded selected clips in the worker, then validate receipts/bundle before upload.
  Recovery/Stop/cleanup/reconcile use the fenced repair writer, never stale whole-params replace.
  Atomic repair Apply binds result/revision and current timing map, appends one timing version
  with its job receipt, and is idempotent after a lost reply. Strict terminal partial outputs may
  apply valid targets while newer timing, wording and all unselected passages are retained.
- Schema-12 CBS Runtime owners/members/attempts are separate from native batches (ADR 0065).
  Only new frozen `ucloud_api.batch_plan` jobs attach; preserve older API/SSH snapshots. Exact
  compatibility excludes per-file sources. Fence coordinator writes by epoch; admit/seal under
  one SQLite CAS boundary. Commit output hashes/result/version/job/member atomically, including
  valid outputs after Stop and producer results already in Trash. Never restore Trash implicitly.
  `runtime_batches.py` alone publishes shared remote controls; `runtime_batch_remote.py` runs
  one GPU pipeline at a time and never replays accepted work with an uncertain outcome.
  Default two allocation slots (1..4), counting legacy and uncertain owners; recovery needs no
  new slot. Retain owner obligations beyond Activity limits and block storage/deletion directly.
  Parent native cancellation must prove every runtime member is its descendant before closing
  that shared owner, under the same stop/admission transaction. Preserve mixed owners and show
  continued processing; only a direct runtime Stop expands to all that allocation's files.
  Stop is allocation-wide; shutdown retains remote work. Cleanup needs every durable local
  receipt and observed terminal state. Shared runtime/model folders never belong to cleanup.
  Never replace `.in-use`; readers hold shared locks, destructive preparation requires exclusive
  ownership and no known legacy reader. No age-based preparation-lock stealing. Test migration
  only on synthetic libraries; real schema-12 activation and Linux locking need separate evidence.
- `runtime_discard.py` (ADR 0066) preserves all attempts' available bounded output separately
  and verifies originals/frozen inputs before a whole-owner confirmation. Distinguish matching
  receipts from unverified raw artifacts; complete unsaved results require recovery first.
  Require fresh exact terminal ownership, bind consent to the preview fingerprint and fence
  recovery/producers atomically before owned-folder Trash. Keep immutable consent, diagnostics,
  saved results/versions and local artifacts; recheck evidence on explicit cleanup retry. Stop,
  Clear, Retry and ordinary cleanup never imply consent; never purge UCloud Trash.
  ADR 0101 permits fingerprint-confirmed Delete disposition of the displayed batch-owned
  folders and automatic terminal settlement after bounded output preservation; complete
  unsaved results still require recovery. Reserve the allocation's control slot through
  disposition, retain failed cleanup journals/cards after local deletion, and skip only
  explicitly receipted-and-absent deleted members during sibling recovery. Legacy single-job
  journals still block deletion until resolved; archival/restored owners remain inert.
- `features.py` preflights optional batch extras before ASR. Commit each provider result and
  its frozen-source enhancement follow-up atomically; retries must not duplicate or repeat ASR.
- Runtime/GPU detection runs in the selected optional interpreter, without weights or network.
  Report CUDA access separately from package readiness and never silently change the device.
  Settings → Local Processing owns device/downloads/runtime and Automatic rules;
  Settings → UCloud owns cloud readiness and preparation permission. The startup
  check is cached; device "auto" is resolved when a stage first runs and written into the job.
  Intel macOS installs the core app without unsupported pinned local inference wheels and
  reports that limitation. Apple Silicon local inference requires macOS 14+; real Mac acceptance
  stays distinct from cross-platform dependency plans and mocked chooser tests.
- Recording language overrides project language; unset preferences keep the saved legacy model
  or processing fallback (ADR0127). Resolve and freeze this choice for each fresh transcription,
  enhancement, timing, edit realignment and passage repair/retranscription. Guard project and
  recording language revisions in the enqueue transaction after routing probes. Reject unsupported
  choices before job creation; a completed free edit returns a visible warning if opt-in
  realignment cannot queue. Keep old job bytes and saved legacy fallbacks; omission of a hidden
  settings field preserves the current persisted value atomically. A configured language is not
  detected evidence. Bind a present language scope in new UCloud repair identities without
  changing older identities.
- Fresh WhisperX requests explicitly freeze timing_policy=retain-estimates (ADR 0089).
  Preserve valid low-score estimates and alignment-fit hints; missing policy keeps legacy
  filtering and exact frozen request bytes. Coverage separates lexical words from complete,
  partial and untimed passages. Automatic second-pass alignment is deferred by the user.
- Danish Edda turbo/large and Røst v3 CBS Runtime profiles are fixed-da full-ASR choices
  (ADR 0084). Preparation converts only verified pinned local safetensors with the locked
  CT2 recipe; cached inference verifies immutable derived artifacts. Keep source and conversion
  receipts separate, and validate history without current weights/runtime/registry access.
- `enhancement.py` owns optional local enhancement; `enhancement_worker.py` is a standalone
  JSON worker. WhisperX and CPU PyTorch are standard locked dependencies in the app .venv.
  Keep inference optional and alternate CUDA runtimes explicit. Fresh/unset Processing now
  enables missing public local model downloads and preparation for explicitly requested UCloud
  work (ADR0103); never download on startup/check/save, preserve saved false and frozen legacy
  option defaults, and never grant standing UCloud preparation through Automatic routing.
  New ASR stages retain configured-language evidence (ADR 0067), bound to the prepared WAV,
  frozen request, actual sample window and model choice/pin. Configured probability is null;
  never present a forced request as detected language. Cached legacy evidence stays unknown.
  Validate present receipts before source/history propagation; reports omit arbitrary passage
  IDs and paths. Derived float caches require WAV/PCM hash and frame identity, marker last;
  new inference checks the actual mapped buffer too. Historical reads need no inference runtime.
  Internal full-ASR Automatic uses separate language_decision and decision-bound alignment
  outcomes (ADR 0069/0070). Unavailable timing has exact null words; keep ASR/empty results and
  independent speakers without claiming word capability. Validate before cleanup/save, retain
  only captured/frozen model pins, and keep historical report validation runtime/file-free.
  Public/default/remote admission stays closed until preparation/carry and bounded-language
  integration is complete. A fixed parent refuses Auto fields; never treat them as legacy.
  Preflight local new jobs/retries outside the queue lock; legacy workers check before decoding.
  Complete stage checkpoints can be finalized without launching/checking an inference runtime.
  One local and one UCloud enhancement lane run alongside media/API workers (ADR 0049). Freeze source result/options;
  retain stage checkpoints, append results idempotently, and never overwrite the reviewed result.
  Disable interpolation, implicit model downloads and telemetry. HF secrets use only the
  credential store and child environment, never subprocess arguments or output logs.
  Long-text alignment uses distinctive ordered local phrase anchors, bounded CTC text/audio
  windows and disk-backed audio. Recognition only finds windows; uncertain words remain untimed.
  Preserve recognition/alignment window checkpoints and every character in partial results.
- Explicit CBS Runtime full-ASR profiles may choose experimental language auto (ADR 0071).
  One version-checked raw-row edit changes only that model; ordinary saves preserve it and
  other-model backfill never inherits the opt-in. Blank still uses Processing's saved da/en.
  Prepare both pinned aligners when requested. Remote acceptance/output/carry uses exact
  hash-verified frozen validator bytes and accepted input receipts; allow empty ASR only after
  decision proof and retain independent valid stages. General Processing, enhancement/follow-up
  and bounded-repair Auto stay gated. OpenAI/official-host and new native specifications reject
  literal auto while older settings/frozen recovery remain readable. No implicit preparation.
  Keep the generic provider form's original version separate from row-cache refreshes; only
  its own verified language-only save may advance that baseline (B164).
- The standalone bounded_alignment worker operation (ADR 0072) is an internal local seam,
  never public/remote repair admission. Validate the complete <=60s prepared unit before
  decode. Its separate alignment_language checkpoint describes a first <=30s no-VAD probe,
  not speech presence, original-recording provenance or text-language compatibility. Retain
  not_assessed/experimental metadata and an alignment_attempted outcome. No ASR or implicit
  downloads; completed decisions never redetect. Keep fixed/full-ASR checkpoints separate.
- `enhancement_api.py` runs new UCloud enhancements API-only on the Settings UCloud
  connection (ADR 0046/0055): a generated credential-free supervisor via a Terminal batchScript,
  readiness before private upload, checksummed inputs, bounded status reads, Trash
  cleanup. The hash-locked runtime in `catalogs/ucloud_whisperx_runtime.txt` lives in a versioned
  project-drive folder; preparation needs request-time consent (one 409 prepare_runtime per
  request, after a bounded read-only probe outside locks) or the researcher's standing UCloud
  consent, frozen with its source in each accepted choice (ADR 0059/0098). Unknown readiness
  accepts and keeps the Activity confirmation fallback; Automatic routing never prepares. Gated
  speaker weights are copied from the local cache, never downloaded remotely. Stop the owned
  job on readiness failure.
- CBS-runtime UCloud jobs are Terminal app batchScript jobs (ADR 0055), not Transcriber
  initScript runs. Missing speaker weights may be fetched locally with the saved HF token only
  under explicit preparation consent, then copied; detection runs on UCloud; never upload tokens.
  Speaker files are verified against pinned sizes/SHA-256 and prepared (retried download, named
  failures) by Hugging Face Check token and before any GPU request; never download during a
  running GPU job (ADR 0057). The runtime copy counts only with every pinned file at its size;
  its marker is written last, never copied as a file. Tests isolate the HF cache (conftest).
  New jobs separately freeze runtime_product and mig-v1 policy (ADR 0063): exact funded single
  B200 MIG, verified COMPUTE wallet/Docker support, first-device VRAM before private upload.
  No silent full-GPU fallback; native Transcriber and earlier frozen jobs keep their products.
- UCloud catalog entries with "engine": "whisperx" (ADR 0053) are WhisperX transcription on the
  CBS runtime: Transcribe queues UCloud-lane enhance jobs (`transcription: true`) frozen to that
  exact profile; speakers run in the same job. Preparation is asked once per request (409
  prepare_runtime) or given by standing consent (ADR 0098), never inferred from routing or
  Retry; prefetch only pinned public models. Script jobs give the app a native model.
- Passage re-transcription (ADR 0054) sends only audio bounds, keeps words centred inside each
  bounded passage (<=60 s), aligns them and appends one combined result; never send reviewed text.
- Review hints (schema 10, ADR 0048) live in `result_hints`, bound to exact passage text; never
  in passages, versions, reports or exports. `pop_hints` strips `_hints` before any save;
  `review_hints.py` rebuilds older results only when text matches the first version.
- Unnamed speakers display/export as Speaker_NN derived from the raw label
  (`default_speaker_names`); never rewrite raw passage labels to apply names.
- `routing.py` resolves `execution: auto` once per recording at queue time (ADR 0047): local
  within the length limit and when ready, otherwise UCloud when approved and prepared, otherwise
  local with a recorded reason. Frozen jobs always hold a concrete place; ordinary repairs stay
  local, while the separately validated selected-bundle route follows ADR 0086.
- `enhancement_cloud.py` remains only for jobs already frozen with SSH settings;
  `enhancement_remote.py` is the standalone detached/locked wrapper used by both. Keep cloud configuration
  identity/Protected approval separate from native Transcriber and vLLM. Never upload tokens.
  Journal intent/remote ID before private transfer, preserve remote work on shutdown, and never
  resubmit uncertain allocations. Explicit Stop remains available for failed owned remote jobs.
  Retry reconnects to the same allocation; stopped/expired allocations require a new enhancement.
  Keep remote runtime/model preparation explicit; no automatic pip or weight downloads.
- `evaluation.py` scores reference text; `scripts/evaluate_fleurs.py` runs the reference suite.
- `review_evaluation.py` scores reviewed clip annotations offline; `scripts/evaluate_reviewed.py`
  compares saved methods. Keep timing/mapping/measurement coverage explicit. Reported speaker
  word accuracy is not DER; preserve time/memory scopes and flag supplied review edits.
- Tests use synthetic or licensed public fixtures, temporary libraries, and mocked providers.
- Guard asynchronous editor responses by recording/result/view generation; late responses
  cannot replace newer navigation or drafts. Keep saving inputs locked until settled.
- Distinguish NVivo Windows TSV and Mac timestamp/speaker text from readable exports.
  Reject untimed native exports explicitly; preserve every passage in readable text.
  Desktop NVivo imports require separate manual acceptance beyond our format/browser tests.
- Prefer standard-library functions and small modules over speculative abstractions.

## Data and credentials

- Bind only to loopback. Preserve same-origin checks for mutations and media access by ID.
- Keep originals immutable. Changes must retain original text and word timings.
- App data belongs outside this repository: by default <home>/CBS Transcriber/{Library,Projects}
  (ADR 0050). Use an existing library at the earlier app-data default in place; never auto-move it.
  `libraries.py` remembers/discovers libraries read-only; switching restarts via the launcher,
  never while jobs are queued/running (ADR 0051).
- Never commit private recordings, interview text, keys, databases, or private paths.
- Public test recordings are allowed only with documented license, attribution, pinned source,
  and hashes. Keep the FLEURS subset small; never download datasets during routine tests.
- API keys belong in the OS credential store or environment, never browser storage or logs.
- Tests never call a paid service. Live provider checks are opt-in and reported separately.
- Treat imported transcript fields and filenames as untrusted data; never use `eval`.
- The supplied external NVivo-preparation scripts/archive were removed by the user on
  10 November 2026. Do not re-add them as a runtime or development dependency.
- Use descriptive errors without exposing upstream credentials or transcript bodies.

## Development usage documentation

Keep [the AI development usage ledger](docs/development-usage.md) and its
[machine-readable checkpoints](docs/development-usage.json) current at release, major milestone
and handoff boundaries. Track Codex and Claude separately, including subagents, with explicit
project scope, UTC/Copenhagen dates, cutoff, release/commit and coverage limits. Keep the MVP
0.1.0 baseline fixed; put later planning/implementation in separate, non-overlapping phases.
Do not sum cumulative checkpoints as if they were additional usage.

For Codex, use retained project session usage records read-only. Deduplicate repeated snapshots
and inherited fork counters; count new work in main and subagent sessions once. Cached input is
already included in input, and reasoning is already included in output. Retain the method,
source snapshot identities and reconciliation checks privately. Never commit raw chat logs,
session IDs, account balances, billing statements, credentials or personal filesystem paths.

Distinguish recorded tokens, rate-card credit equivalents, actual credits debited and monetary
estimates. Record model/rates/source/as-of date, speed and whether it is user-confirmed. Standard
speed is confirmed for the MVP Codex baseline. Do not apply API prices or current rates as proof
of historical Edu billing; CBS's contracted price, included credits and licence fees are separate.
Unknown values stay null/pending, not zero. Preserve historical assumptions when adding updates.

The user assigned Claude's MVP accounting to Claude. Leave that entry pending until Claude
supplies its own scoped evidence; Codex must not estimate it or start a Claude audit. Do not
combine providers' incompatible credit units or report a full AI total while a provider is missing.
Usage accounting does not authorize paid calls, provider jobs or changes to the running app.

## Workflow

Implement the approved plan autonomously. Update TODO checkboxes as work passes verification.
On failure, trace the cause and add a regression test where useful; record material lessons
in SPEC's bug section. Avoid adding new approval gates for routine reversible development.

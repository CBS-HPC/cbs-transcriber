# CBS Transcriber specification

## §G Goal
CBS-supported transcription service for Danish/English interview research: accessible
transcription, synchronized review and research exports with manageable service/support costs.
Current delivery: local Python browser app, projects → recordings → API/import → review/edit → NVivo.
Long-term T88: transcription → AI-assisted analysis → human review → structured NVivo export;
advanced qualitative interpretation and research reporting remain in NVivo.

## §C Constraints
- Windows and macOS; Python 3.13, uv, local .venv, Git main, pytest, Ruff.
  Validate real installation/playback on each OS before claiming platform acceptance.
- FastAPI + build-free HTML/CSS/JS; SQLite; loopback only; single user.
- Current provider UI offers OpenAI and UCloud only. Custom and UCloud vLLM are deferred;
  retain their existing saved configurations, research results and frozen jobs for compatibility.
  Built-in connection names are internal; Available models is part of basic Settings.
- CBS service rollout is a separate pilot decision: access, ownership, support, approved processing
  and measured total operating cost. Current local architecture does not imply shared hosting/SSO.
- Managed local library outside repository/OneDrive; retain originals.
- Cloud storage scope: working library stays local; UCloud holds recoverable backup copies
  restored into a local library. Active UCloud-hosted projects and multi-device synchronization
  are far-future roadmap items, outside current delivery and weekend implementation.
- OpenAI + compatible transcription endpoints; no OpenAI Batch API or automatic transcription
  summary/cleanup. Separately planned T88 analysis may generate evidence-linked reviewed summaries.
- T88 remains a longer-term roadmap, after current reliability and UCloud execution work. Its
  own P0 validates REFI-QDA/NVivo interoperability and the data model; P1 adds shared UCloud AI
  Platform/OpenAI analysis, hierarchical codebooks, deductive/inductive coding, summaries and
  human review; P2 adds topic discovery, sentiment/stance and expanded exports. Preserve exact
  passage/version evidence and available original-audio links; distinguish AI suggestions from
  approved coding. NVivo owns advanced thematic interpretation, matrices, code co-occurrence,
  cross-interview comparisons and research reporting. See docs/ai-analysis-nvivo-roadmap.md.
- UI: action button captions omit trailing ellipses, including dialog launchers and busy states.
- UI: manual review progress, Next to review and the uncertainty controls share a row when space
  permits, wrapping on narrow screens. Uncertainty controls follow Next to review.
- UI: Text / Word and timing editor dropdown defaults to Text; audio/video switch retains position.
- UI: double-clicking a rendered transcript word opens Text editing with that exact mapped word
  selected, including repeated words and Unicode/newline offsets. Opening does not save or align.
- UI: Save passage requires a changed text draft. Split at cursor requires unchanged text and
  a single caret with non-whitespace text on both sides. Cancel discards only the passage draft;
  ordinary cancellation preserves unrelated transcript nodes, search, review and playback state.
- UI: continuous Play transcript and Follow playback appear in the transcript header;
  routine saved status is hidden, while saving, unsaved and error messages remain visible.
  Passage Replay remains bounded.
  Rename projects with a small heading button and name dialog. Edit classification and storage
  inline below the heading on separate rows; they save on change (V86). Renaming preserves those
  settings and notes drafts. Classification, storage and deletion live in the sidebar's Project
  Settings dialog (V93).
  Classification is only as wide as its options; the storage path field is bounded; Add
  recordings sits beside the recordings search box.
  Recording heading: the name, rename control and recording details share a row when space permits.
  Review result sits below the title on the left, followed by Compare with
  and Versions on the same row; Compare is disabled with fewer than two live results. Wrap on
  small screens. The result controls follow the transcript column; the transcription toolbar
  may use the available width above the details panel to keep its controls and search on one row.
  At narrow widths it wraps without horizontal page overflow. Versions matches the dropdown height.
  In comparison, align to the combined transcript area; wrap controls on small screens.
  Keep Transcribe, Set as working and search immediately after the language selector when they fit.
  Omit the redundant saved-result count below the selectors; keep the transcription controls
  close beneath that row.
  No project/recording breadcrumb strip. Provider/Model/features/Transcribe and search sit below
  the heading; File Settings and export/research actions are in the sidebar (V108).
  Omit decorative workspace/listening/computer/
  storage slogans; retain actionable warnings, errors and progress.
- UI: literal search highlights matches in both transcript panes; sidebar has Settings
  in place of the local-storage slogan. Existing projects can change their storage in settings.
  Keep one sidebar Create a project action; no duplicate plus button beside YOUR PROJECTS.
- OpenAI labels: provider prefix, model ID followed by (Word timing) or (Speaker Labels) where supported;
  display old automatic names consistently while preserving user-defined configuration names.
- Existing scripts define CSV formats and NVivo convention; fix dropped first passage.
- Brand: unchanged official CBS logos; primary #4967AA; CBS New Bold headlines and CBS Sans web text.
- API tests mocked. Real UCloud example and live credentials not supplied.

## §I Interfaces
- I.cli: uv run cbs-transcriber [--port PORT] [--data-dir PATH] [--no-browser] [--profile full|mvp].
- I.release-profile: GET /api/features returns the effective release profile, its optional build
  label and hidden features. Resolution: --profile, CBS_TRANSCRIBER_PROFILE, packaged
  catalogs/release.json, then full; unknown names refuse startup. Hidden new-work routes answer
  403 code feature_disabled (ADR 0145).
- I.api: /api/projects, /api/recordings, /api/jobs, /api/settings; ID-based media/export.
  GET/PUT /api/local-concurrency reads and partially updates parallel local task limits.
- I.providers: named provider configurations; bulk and per-recording selection; explicit
  multi-provider queue requests. Legacy /api/settings edits the default configuration.
- I.results: per-recording saved transcripts, active result selection, independent edits,
  exports, and read-only comparison sharing the recording playback timeline.
  Manage results offers recoverable Trash/Restore per model/enhancement result; never erases
  originals, histories, raw files or frozen source relationships.
- I.passage-delete: explicit passage deletion from the active saved result with revision/identity
  checks and exact earlier History. Deleting the final passage leaves an empty saved result.
- I.passage-merge-undo: discover eligible merged passage IDs with a bounded history check;
  Undo verifies the active result, revision and exact predecessor/merge evidence in one writer
  transaction, restores the two original passage dictionaries, preserves other current edits
  and appends a manual history version. Apply the same atomic Working choice and stale-role
  fence as other manual edits. Refuse changed or unproven merges (ADRs 0133, 0135).
- I.passage-split-undo: the same bounded eligibility lookup also identifies first halves of
  proven splits; POST segments/{id}/undo-split restores the exact original passage only when
  both current halves still match the verified split. Preserve unrelated edits and Working fences.
- I.passage-review: independent manual Not checked / Checked / Needs review metadata for a
  saved result's passages; scoped GET and revision/hash-bound PATCH and checked progress.
  Marking does not edit text, timing, content history or Working roles.
  Next to review navigates visible Not checked/Needs review passages, skipping Checked and
  wrapping independently of the optional model-uncertainty display.
- I.passage-delete-undo: the same bounded inverse lookup lists proven deleted passages outside
  the live transcript. POST segments/{id}/restore binds result/revision/delete-version and
  Working choice; restore one exact dictionary at proven anchors, preserving unrelated edits.
- I.native-batch: one explicit selected-file request groups identical native model/connection
  settings into one UCloud allocation with a unique input directory and per-file outputs/results.
  Retain local per-recording identities and durable shared-run ownership/recovery.
  Bounded reads of the exact batch-owned processing log may publish curated per-file progress
  matched to frozen input UUIDs; the log is observational and never proves saved completion.
  New native connection defaults request a 24-hour maximum; existing explicit limits and frozen
  jobs retain their exact values. Legacy snapshots with omitted hours still mean four hours.
  Frozen transcriber/1.12 batches may preserve finished members after fresh exact processed
  observations and two identical valid reads. Unknown references wait for terminal. Existing
  raw uses terminal recovery; early saves never authorize remote cleanup (ADR 0155).
- I.enhance-api: new UCloud enhancements reuse the Provider Settings UCloud connection and
  API-only job/file operations. No new SSH setup or separate control token. Existing frozen SSH
  runs retain their original recovery path. Runtime/models must remain explicit and validated;
  never re-transcribe or overwrite reviewed source text to simulate enhancement.
- I.runtime-discard: POST /api/runtime-batches/{id}/discard-preview preserves bounded remote
  evidence and returns an exact confirmation fingerprint and member/missing-result summary.
  POST /discard requires that fingerprint plus explicit true confirmation (ADR 0066).
  ADR 0101 additionally permits exact fingerprint-confirmed Delete disposition and bounded
  automatic terminal settlement after output preservation; complete unsaved results block it.
- I.language-evidence: optional inline stage receipts bind configured ASR language to prepared
  audio/request hashes, pinned model and actual sample-rounded prepared_audio window. Validate
  before result/version propagation; metadata-only reports omit arbitrary IDs and private paths.
- I.language-decision: gated full-ASR worker/host accepts frozen requested auto and preserves
  inline language_decision plus typed decision-bound alignment outcomes in results/versions.
  Explicit CBS Runtime full-ASR profiles may opt in; generic enhancement/follow-up admission
  and Processing defaults stay gated. Detector window is explicitly unknown, distinct from
  full prepared-input scope (ADR 0069–0071).
- I.runtime-language: PUT /api/providers/{id}/runtime-language takes {language,expected_version};
  language is blank/da/en/auto and the version binds one existing saved Runtime model row.
  The endpoint remains for existing clients; the compact UCloud model list has no language controls.
- I.native-language: PUT /api/providers/{id}/native-language takes {language,expected_version};
  it version-fences one existing native model profile, accepts blank or a custom code of at most
  20 characters, rejects auto, and does not assert backend support or change frozen jobs.
- I.clip-language: internal local worker operation bounded_alignment detects language only on
  a validated <=60s prepared unit. Separate alignment_language.json and digest-bound attempted
  alignment retain exact source/request identity. Public/settings/remote admission stays closed.
- I.repair-source: internal read-only repair_sources inspector proves a job's full preserved
  source ownership and exact frozen/selected projections in one bounded SQLite snapshot.
  Return internal IDs/hashes/order only; no audio, current approval or remote admission proof.
- I.repair-bundle: internal alignment-only clip builder/validator consumes owned frozen source
  and recorded original-v1 extraction receipts, emits an immutable bounded ordinal inventory,
  and maps validated clip outputs once. No upload, allocation or public routing. ADR 0078.
- I.model-evaluation: offline stratified plans and supplied-output comparisons bind candidate/
  reference pins, preserve unverified operator identity and missing coverage, and reuse existing
  metrics without inference, rankings or catalog changes. ADR 0079.
- I.runtime-progress: Activity exposes read-only member counts, confirmed active stages and
  curated reasons; saved-result count is distinct from unproven recognition percentage. ADR 0080.
- I.result-merge: POST recording/transcripts/{active}/merge-preview and /merge with an incoming
  enhancement ID, recording revision and (for Apply) exact preview fingerprint. Server computes
  timing/speaker changes; no client passage payload. Context retains base/incoming/mapping
  version hashes and reports traverse their verified aliased ancestry. ADR 0068.
- I.wording-adopt: POST recording/transcripts/{active}/wording-preview and /wording-adopt with
  source result, exact source/target passage IDs, revision and Apply fingerprint. Server freezes
  actual current heads and shows both literal texts/positions; one explicit free-edit successor,
  no timing transfer or new job. Strict evidence and verified aliased report ancestry. ADR 0074.
- I.import: UTF-8 JSON (segments or segment list), Whisper/Transcriber CSV by column names.
- I.original-inspection: internal schema-13 nullable cache and reserved source-inspect jobs;
  read/ensure/claim/cancel/retry/recover/run use generation/job/epoch CAS and a stable byte anchor.
  Caller-held same-Store lease guards claims/run/recovery; no public admission or automatic lane.
- I.inference-source: new transcription paths use the same immutable original recording;
  provider-required decode/compression/splitting derives directly from it, independently of
  browser playback. Freeze input/conversion identity; already frozen jobs keep their source.
- I.transcript: segments with original/current text, original/current words, optional times,
  speaker ID, timing state; recording-level revision and speaker-name mapping.
- I.provider: /audio/transcriptions; configurable base URL/model/language/response capability.
- I.provider-check: explicit model-list check with a saved or unsaved endpoint key; no audio.
- I.ucloud-vllm: independent UCloud vLLM tab with several saved model configurations; serving
  URL/key, HF model ID shared with served model ID, pinned catalog and separate setup tokens.
  Models can be configured while offline; availability checks are not part of this tab.
  Hosting and URL creation stay in
  the hosting agent. Its published release remains chat/PII only; speech support is
  implemented on a separate provider branch pending real allocation validation and release.
- I.ucloud-transcriber: independent native UCloud batch provider using the released UCloud
  API project dependency; token-first project/application/compute/storage discovery with
  human choices only for ambiguous eligible resources; advanced template setup remains.
  Recognized native token references look up an exact server metadata record and prefer its
  eligible owner project. Preserve explicit saved selections; unavailable ownership retains
  named choices. Ownership is a default association, not a claim of token access scope.
  Original upload, job polling,
  cancellation, output import and retry of the same remote job. No OpenAI Batch calls.
- I.parallel: provider-specific 1–4 simultaneous parts per recording; legacy settings use 1.
- I.project-policy: Standard or Protected projects; saved provider eligibility for Protected data.
  New OpenAI/Custom configurations default off, UCloud configurations default on; user choices persist.
- I.project-storage: chosen per-project folder for media, sources, result snapshots and NVivo;
  central library database retains the authoritative index, edits, and queue.
- I.export: explicit NVivo Windows TSV and Mac speaker/timestamp text; readable text retains
  INTERVIEW heading and consecutive speaker/timestamp grouping. Native formats use UTF-8 BOM.
- I.export-settings: shared recording/project dialog; SRT, WebVTT, CSV and current-view JSON
  exports. Project ZIP chooses active or all saved results and reports every skipped recording.
  Compact checkbox lists select project recordings and one or more output formats. Structured
  POST archive requests freeze the explicit selection; legacy single-format GET links remain.
  ZIP recording folders use safe, collision-resolved recording names, not bare numbering.
  Include original recordings is an explicit unchecked option. It adds each selected original
  once beside that recording's transcripts and permits original-only ZIPs. Keep rows without a
  transcript for the chosen result scope visible with a reason, disabled unless originals are included.
  New ZIP preparation returns a server-lifetime Activity task after freezing its read snapshot;
  closing the dialog/navigation does not cancel it. Ready owned ZIPs stream by download ID;
  one active/three ready, explicit cancel/discard, shutdown cleanup, legacy APIs retained (ADR 0158).
- I.evaluate: repository script scores clip-ID → text JSON offline, or explicitly runs the
  configured app provider on five pinned public Danish FLEURS recordings.
  Opt-in runs bind accepted producer/settings, exact immutable creation version and payload
  hash; prediction/export receipts never follow a recording's active or latest reviewed view.
- I.enhance: optional local processing with standard WhisperX dependencies, CPU/CUDA selection,
  Danish/English word alignment
  and/or speaker detection on a frozen saved result; separate durable enhancement queue and new result.
  Cached models by default; model downloads require explicit per-run consent. No ASR text replacement.
  UCloud execution uses a separate approved SSH-template configuration and the same cached worker;
  native Transcriber and vLLM serving endpoints are not enhancement executors.
- I.install-inference: uv sync --locked installs WhisperX 3.8.6 and CPU PyTorch on Windows/
  Apple Silicon by default. Intel macOS installs the core API/UCloud/review/export app;
  pinned local inference is explicitly unavailable there. No install/startup weights or inference.
  Python 3.13 (<3.14); Apple Silicon locked inference stack requires macOS 14+.
- I.passage-repair: missing-timing passage/count actions queue local cached-model alignment
  for selected bounded passages. Freeze source and IDs; append one result with explicit Review repair.
  Partial bounded passages retain replay intervals; legacy untimed fragments may expose separate
  replay_windows from exact frozen source evidence, without changing stored timestamps.
- I.notes: project/recording notes PATCH {text,revision}, max 20,000 Unicode characters;
  independent optimistic notes revisions; metadata excluded from transcript/provider input.
  Recording cards and recording review edit the same notes field; preserve card drafts across
  filtering/renders and retain revision checks on both entrypoints.
- I.portable-transport: internal read-only portable_transport inspector binds caller-trusted
  commit SHA to canonical bounded manifest and closed object bytes. Transport observation only;
  database/ownership/completeness/quarantine unchecked, restore_ready=false. No v1/UI change.
- I.portable-rows: internal pure typed table digests use the pinned schema-12 catalog and
  exact observed row values; no SQLite/filename/package observation or restore authority.
- I.portable-database: internal contained child verifies one hash-bound schema-12 database's
  supplied claims; matched_supplied_database only, no sealed-package/history/restore authority.
- I.portable-package: internal package_database_claims_only observation derives database
  references from an in-call frozen transport capture; no supplied capture or restore authority.
- I.portable-payload: pure supplied snapshot-v1 byte/hash and canonical-envelope observation
  with pre-JSON lexical budgets; no passage/history/database/owner/restore authority.
- I.history: schema-6 immutable result/notes versions, recording-scoped paginated history,
  exact version reads/exports and a metadata-first project research-report ZIP. Optional bounded
  reasons describe local observations; selected-text sharing is explicit, no provider execution.
- I.import-artifact: new successful imports preserve exact raw bytes under an opaque managed
  source ID; explicit add_result import_artifact_evidence binds its verified parser payload.
  Pure receipt validation and same-snapshot reports expose aliases/hash/size with availability
  not_checked, retaining legacy unknowns (ADR 0073).
- I.annotations: experimental manual topic/evaluation evidence and validated suggestion imports,
  versioned approved project codebooks, frozen transcript versions, append-only human reviews,
  scoped JSON export. Separate from timing/speaker enhancement; automated scoring is unavailable.
- I.analysis (planned, T88): shared model-independent UCloud AI Platform/OpenAI text-analysis
  engine; interview/topic summaries, deductive/inductive coding and later topic discovery and
  topic/aspect sentiment/stance. Freeze prompts, model, transcript/codebook versions and evidence;
  retain researcher accept/modify/reject decisions separately from immutable AI suggestions.
- I.refi-qda (planned, T88): `.qdc` hierarchical codebook import/export and `.qdpx` project export,
  mapping transcripts, code hierarchies, coded passages and supported summaries/memos. Validate
  actual NVivo Windows/Mac imports before claiming compatibility; report unsupported mappings.
- I.passage-speaker: selected-passage speaker PATCH {revision,speaker_id} or {revision,name};
  choose an existing label or create one named speaker, preserving word timing.
- I.model-presets: editable schema-2 JSON groups for OpenAI/native Transcriber/CBS Runtime shared by UI/request
  normalization; optional CBS_TRANSCRIBER_MODELS override, validated on server startup.
  Native UCloud model selection offers only catalog presets, without a custom-model entry
  or editable model ID. Existing out-of-catalog settings/jobs remain preserved; select a
  supported model before saving that configuration through the UI.
- I.native-cleanup: new configurations default to delete owned remote inputs/outputs after
  successful validation and local result commit. Retention optional; failed/uncertain jobs retained.
- I.batch: project selection → provider/model → Transcribe selected → ASR-only or requested
  timing/speaker extras through Local/UCloud settings. Start now, run unattended; no scheduler.
  Project cards show shared editable Description / notes and result counts, without
  provider/model/features or a Transcribe button; no separate Batch transcription launcher.
  Recording review retains its own Provider/Model/features and Transcribe action.
  Project search, Export Settings, Research report and Topics/sentiment form a left-aligned
  toolbar with consistent gaps and wrapping when space is limited.
  Freeze options and attach a durable enhancement only for capabilities missing from that model.
  Recording-review/Transcribe selected controls expose native locked and optional timing/speaker
  choices; missing features open the same Local/UCloud settings before submission.
- I.review-evaluate: offline reviewed-clip CLI compares saved methods with language/corpus
  WER/CER, actual matched timing coverage, explicitly mapped speaker-word accuracy (not DER),
  and reported performance provenance/scopes; no provider execution or invented reference data.

## §V Invariants
- V192: Next to review uses the current validated manual review snapshot and rendered passage
  order, skipping Checked and wrapping from the end. Zero model hints or hidden uncertainty
  controls cannot disable available manual work. Disable on all checked, unavailable/loading
  marks or unsupported historical views. Refresh eligibility after status/filter/view changes;
  preserve drafts, caret and content and refuse stale view authority. Navigation writes no marks.
- V193: Original media enters an export only with explicit include_originals=true; old requests
  and dialog defaults exclude it. Stream selected, owned regular originals through bounded reads
  into their safe unique recording folders; never follow arbitrary paths/links or read a complete
  media body into memory. Preserve source identity and reject uncertain changes. Missing originals
  have an explicit skip; no source file is moved or changed. No credentials or private provider
  configuration enters the ZIP. Metadata/Working selection still uses one read snapshot. Do not
  hold queue/snapshot/writer locks across large media copies. Original-only requests may have no
  transcript formats; every other empty-format request refuses. Disabled rows cannot be selected
  by Select all, and enabling originals never silently adds previously unselected recordings.
- V194: Native processing-log reads are best effort, byte/time/rate bounded and restricted to
  the recorded batch-owned output log. Parse only known formats and exact frozen input names;
  expose curated stages/observations rather than arbitrary log text, filenames or credentials.
  Ambiguous/interleaved lines cannot assign another member's stage; ordinals never prove
  correspondence or completion. Missing/rotated/malformed/oversize logs and log-transfer errors
  cannot fail a job or alter submission, Stop, cleanup or validated local receipts. Retain current
  job/status authority, distinguish last observations from fresh state, and preserve same-ID Retry.
- V195: Apply the 24-hour native job default only to fresh native setup/profile creation where
  hours was omitted. Preserve explicit values, existing profile updates, Runtime settings and all
  queued/running/retry snapshots. Legacy NativeSetup parsing still defaults omitted hours to four.
  Fresh setup must serialize 24 explicitly before freezing; the native job specification uses that
  exact frozen limit. Keep the existing one-to-168-hour validation range and allow explicit overrides.
- V196: Ordinary Text edit-v2 fixes conservative-word-map-v1 on the server. Keep exact unchanged
  word intervals/dictionaries and uniquely corresponding replacement intervals; new words stay
  untimed, with no inferred passage bounds or alignment. Bound all-optimal correspondence and
  refuse ambiguous, reordered, malformed or partially mapped lexical reuse atomically, retaining
  the draft. Never clear a valid map as an ambiguity fallback. Already-stale maps may save untimed
  with an explicit warning. Preserve legacy edit/adoption/replay and explicit v1 realignment;
  capture the policy in history and verify it in the repinned portable semantics (ADR 0156).
  This remains the legacy generation; fresh manual Text uses edit-v3 under V208.
- V197: Restore one deleted passage only from exact owned verified Delete/predecessor history,
  unique absent ID and strict current immediate anchors/boundary. Preserve unrelated dictionaries
  and current named voices; append passage_delete_undo/deleted_version_id through current
  revision/Working authority. Newer passage presence, whole-version restoration or corrupt newer
  evidence blocks old resurrection even if the ID was later consumed. Share bounded discovery
  with merge/split. Inactive previews never enter live playback/search/export/counts; drafts and
  late replies remain fenced (ADR 0157).
- V198: Early native retrieval requires frozen transcriber/1.12, fresh exact completion log
  observation, exact output selection and identical valid bytes across separate polls. Unknown
  references and preserved raw use terminal recovery. Never replace prior raw/capture after a
  lost commit; recheck before transfer and receive. Preserve per-member capture/idempotency,
  later manual edits and one pending owner until terminal. Cleanup remains terminal-only with
  all preservation receipts. Public writer ordering is not a deployed-image proof (ADR 0155).
- V199: Background ZIP preparation freezes its exact scope/selection and read snapshot before
  returning acceptance, continues after dialog/navigation close, and exposes status/cancel/ready
  download in Activity. Bound concurrent work and retained artifacts, stream ready owned bytes,
  and keep ordinary editing usable. Fence library switch/capture/deletion/file moves against
  active export admission. Shutdown drains/cancels and cleans private temporary exports; server
  restart continuation is not promised. Ready downloads remain until explicit discard/shutdown.
- V201: Pristine/reverted passage drafts cannot Save. Split needs a collapsed interior caret,
  unchanged text and no active save/structure lock. Cancel retains unrelated DOM/search/review/
  playback and does not write content/Working/jobs; dirty passage-switch refusal keeps its draft.
- V202: Native log I/O excludes elapsed mandatory guard work from its three-second budget.
  Keep every fresh guard and exact failure propagation; actual slow metadata/session/tail reads
  remain bounded. No authority cache or relaxed publication/cleanup rules (ADR 0160).
- V204: Retry transient native output reads at most three times on the same recorded allocation,
  retaining fresh guards, bounded transfer bytes and completed results. Never retry submission,
  authentication/selection/parse/guard failures or infer missing-output resubmission authority.
  Curated per-member read errors are bounded observations, not capture/control proof (ADR 0162).
- V205: Native streamed transfer guards read fresh recording identity/project/readiness metadata
  without hydrating transcript, speaker or Working payloads per block. Preserve all-member
  membership/frozen-identity/original-input/approval/Stop checks; no cached authority or relaxed
  capture, import, history or cleanup validation (ADR 0163).
- V206: Same-editor-context hint loads share one pending request; failure permits retry and
  older lifecycle replies never replace newer cached hints. Hints arriving after a saved
  mutation update decorations/summaries without a second full transcript render, retaining
  passage/manual controls, drafts, search, playback and comparison behavior (ADR 0165).
- V207: Host history memos retain bounded digest facts only. Only ordinary parsed serialized
  JSON may seed generated canonical-format facts; generic object encoding stays unchanged.
  A mirror-binding hit freshly hashes both actual mirrors and current head payload, checks
  its stored hash and retains fresh owner/head/CAS/Working/source/inverse checks. Misses and
  unsupported raw hashing use the exact legacy canonical fallback; never cache objects or
  authority, or perform hashing/JSON/database work under the memo lock (ADR 0164).
- V208: Fresh manual Text edit-v3 and split-v2 never refuse valid wording or a between-word
  split because word timing is ambiguous, missing, malformed or over its correspondence
  budget. Preserve identifiable unchanged timing/dictionaries and unique replacement intervals;
  inserted/uncertain words remain null with an accurate nonblocking warning. Literal appends
  retain verified prefix words even with repetitions. Split requires a whitespace boundary,
  nonempty halves and current revision/Working authority; disable inside-word/range/dirty/end
  controls without snapping. Keep exact text/unselected passages; derive half bounds only from
  verified complete coverage or a valid explicit audio point, otherwise keep them null. Capture
  new policy identifiers and dispatch Undo by the recorded generation; legacy routes, history
  and original/predecessor bytes retain their exact behavior (ADR 0166).
- V200: Editing performance shortcuts preserve fresh actual-byte hashes, current ownership,
  revision/Working/inverse/lifecycle and bounded-history checks. Reuse only verified canonical
  format facts; parse fresh mutable objects. Targeted merge/split Apply may stop only after its
  requested proof is fully verified; full options/Delete-restore discovery stays unchanged.
  Keep strict input refusal before writes, canonical fallback for legacy mirrors, authoritative
  history encoding and synchronous snapshots. Supported native-JSON mutations preserve legacy
  wire/error/header behavior, including _sa-key omission; no global/GET shortcut (ADR 0159).
- V190: Word-and-timing editing uses one lazy word dropdown labelled with ordinal and literal
  saved word text. Edit word reveals correction within that column; start/end remain separate.
  Preserve text/timing/CAS/Working guards, unsaved-switch protection, keyboard access and exact
  Unicode. Ordinary text-editor opening does not allocate every word option; opening timing does.
  Double-clicking a rendered word opens Text editing, including current partial playback maps;
  single-click seeking and explicit Word and timing remain available, with existing draft guards.
- V191: Once a native batch owns a recorded remote job, transient 502/503/504 or transport poll
  failures automatically reconnect to that same job with cancellable 2/5/10/30-second capped
  backoff inside the existing monitoring deadline. Never retry a submission/mutation through
  this path. Successful reads reset backoff and restore stages. Deadline exhaustion is a curated
  monitoring interruption, not evidence of ASR failure; preserve the job and remote allocation.
  Authentication refusals remain explicit; shutdown/cancellation keep their existing ownership rules.
- V189: Explicit export selections require at least one owned in-scope recording and one allowed
  output format; empty, unknown or foreign selections never widen to the whole project. Read all
  chosen results/Working payloads, collections and formats from one database snapshot. Preserve
  exact Working-version authority and report every format/result skip without losing other valid
  outputs. ZIP paths use safe unique recording names, with deterministic collision suffixes and
  distinct names for same-extension formats/results. Original media requires the separate explicit
  V193 choice; exports never mutate source data.
  Dialog choices and download replies remain bound to their opening recording/project/result/view.
- V185: Undo split verifies the split version and immediate predecessor, their hashes/ownership,
  exact full recomputation, unique IDs and unchanged adjacent current halves. Normalize only the
  generated right-half ID after proving its historical shape. Restore one exact predecessor
  passage, preserve every unrelated current dictionary and speaker name, append passage_split_undo
  atomically with the ordinary manual Working choice. Changed, stale or unproven splits refuse.
  Bound history discovery and Apply together with merge undo; never infer an inverse from labels.
- V186: Manual passage review is separate from model uncertainty, transcript content and Working.
  Persist status against the exact canonical saved passage hash with an independent row revision.
  An absent mark is Not checked; a Checked mark with a changed passage is Needs review; unrelated
  marks remain. Exact restoration can match an earlier mark again; new results inherit no marks.
  PATCH requires the active live result, current recording revision, passage hash and review-row CAS.
  Preserve marks in complete backup/restore; reject malformed or orphan review rows. UI acknowledgments
  remain view/revision fenced and cannot replace drafts or silently mark unsaved wording Checked.
- V187: Native batch uploads/downloads use at most four in-flight file transfers per batch.
  Network tasks own separate control/transfer clients; the coordinator alone merges durable upload,
  output/capture and result receipts. Bound completed output bytes by the transfer window. On error,
  cancellation or shutdown stop admission and drain accepted tasks before releasing the library lease;
  preserve successful receipts. Submit no allocation before all inputs verify, retry no confirmed input,
  and retain existing remote ownership, Stop, capture and cleanup semantics. Show each file's actual stage.
- V188: The word-and-timing editor retains a saved-text preview with only valid existing word
  mappings highlighted. Follow playback may scroll that preview without moving the input caret,
  focus or draft, or scrolling the whole editor. Explain that unsaved wording is not timed; absent
  or stale maps never gain fabricated word timing. Text mode has Replay without a duplicated
  preview; both modes keep their drafts. Highlighting in one editable Text area remains later work.
- V184: A release profile hides entry points to new work, never existing data or recovery.
  Static elements carry data-feature and stay hidden by CSS even when code un-hides them; dynamic
  UI omits hidden choices so rows reflow. The server refuses hidden new work (403
  feature_disabled); reads, exports, Stop/Retry/Reconnect/cleanup and frozen jobs are unchanged.
  Without UCloud processing, Automatic freezes this computer and Repair all stays local; main
  ships full and tests pin full unless a run sets the profile explicitly (ADR 0145).
- V177: Removing a non-current library from the main list is reversible presentation only;
  retain its files, jobs and remote obligations in a visible Hidden libraries disclosure.
  Bind suppression to directory/database identities so a replacement is discoverable; reject
  stale actions and an open target. Corrupt visibility metadata never hides libraries.
  Hiding never grants permanent-deletion authority; that is the separate V183 (ADR 0144).
- V183: Delete permanently applies only to a listed, non-current library no server holds.
  Its preview never opens the target database in place or creates/writes any target file
  (including `server.lock`): it reads a private copy with unchanged signatures and walks
  without following links. Unsettled jobs, UCloud/Runtime/preparation obligations, local
  deletion/transfer/restore records, unreadable evidence, links/mounts, nested libraries,
  any overlap with another known library's project folder and newer/damaged databases block deletion.
  External project folders are deleted only when exclusive (no overlap with any known
  library root or referenced project folder, owned layout, matching `project.json`);
  others are kept and listed. Confirmation types the folder name and binds a fingerprint
  rechecked under the target lease, including deterministic nested names, kinds, file identities,
  sizes and recorded mtime/ctime; these metadata observations do not claim content hashes
  or exclusion of unrelated concurrent writers. A reservation outside the target is published before
  any change; `Store`/launcher refuse it before creating anything and under the lease.
  Same-volume staging into marked siblings precedes commit; recovery restores only into
  unoccupied names before commit, finishes after it, and keeps anything unverifiable
  reserved and visible with Retry. The registry entry is removed only after commit (ADR0144).
- V163: Explicit UCloud CBS Runtime requests that create input intents probe runtime/model
  readiness read-only, bounded and outside locks before acceptance. Definitely unprepared work
  without standing consent is refused whole with one 409 prepare_runtime naming model and file
  count; nothing is accepted. Confirmed (`request`) or Processing standing (`standing`, on for
  fresh/unset Processing settings, saved opt-outs retained) consent is frozen in version-2 choices;
  version-1 intents stay valid. Unknown readiness accepts with the Activity fallback, whose one
  Prepare covers the request's stopped same-choice files. Routing/Retry never consent (ADR 0098).
- V156: Fresh whole Runtime compressed-original transport requires full original-v1 proof,
  one audio stream/no video, supported encoded format, <=512MiB/6h and fewer source bytes than
  PCM. Freeze per-member transport without splitting compatible allocations. Absent protocol
  keeps legacy WAV; local/OpenAI/selected repairs keep their existing input routes.
- V157: Verify the pinned remote decoder before private upload. Bounded Linux decoding/parent
  death handling must match exact frozen PCM/frame/source/request identities before inference;
  failure publishes no ready marker or inferred time offset. Retain the distinct immutable
  remote preparation receipt and fence its job/member/attempt/epoch publication.
- V158: Older selected repairs admit metadata only, then preserve a complete byte anchor before
  hash-bound Stop/failure. Pre-proof Retry advances an attempt epoch; stale workers cannot
  publish. Inspect only the selected-source timeline/clip windows; no full ready-cache backfill,
  ASR or original-file upload follows from this bounded authority.
- V159: Scoped repair global PCM/frame claims remain null. Before PTS reset, prove each exact
  selected absolute start, contiguous16kHz sample timeline and end/count; recheck actual WAV
  frames. Require selected-pcm-contiguous-v1 on the exact frozen IDs/windows in local bundle
  and remote validation. Refuse gaps before allocation, preserving full-source/legacy contracts.
- V164: Fresh native UCloud Transcriber jobs freeze native-artifact-v1 only in admission
  prepare. Before POST each served job records a credential-free submission projection
  (free-text values not set by the app withheld by name) and its input binding; the journal
  keeps the accepted projection digest. Exact downloaded output bytes are observed outside
  writer transactions; reused preserved outputs stay gaps. Creation checks the journal and
  binds per-member evidence; legacy/frozen native runs keep their behavior (ADR 0100).
  A frozen submitted `parakeet` model also permits the exact input-id `_parakeet.json`
  output without inferring a language; selection and artifact verification keep the same
  owner, identity and ambiguity checks (ADR 0119).
- V161: Fresh API transcription (OpenAI/custom/UCloud vLLM) freezes api-artifact-v1 only in
  admission prepare. Each part records its credential-free logical request descriptor before
  upload, then publishes the observed normalized SDK output with its part; retries replay the
  first fields. Preserved outputs without an observation stay explicit gaps. Store creation
  binds the exact parts/assembly to the sequence-one version; source flags cannot assert it.
  Native Transcriber and older/unmarked jobs keep their earlier behavior (ADR 0097).
- V149: Working transcript pins an immutable version, with independent
  role revision and append-only designation events. Migration and restore do not assign it;
  fresh-result and processing succession follow V180; an untouched sole existing result
  follows V182. Trash clears its designation
  atomically. Whole-project exports default to the exact pinned payload; unassigned recordings
  are skipped explicitly. Review selection remains independent. When an active saved result is
  available and no result mutation is pending, disable the main Set as working button when its
  exact viewed version is already Working; a different version of the same result remains eligible.
- V180: Before a manual edit or role-event write, validate the two most recent role events and
  their immediate predecessor linkage, bounded metadata, action, actor and referenced payloads.
  This bounded check does not establish the integrity of older role history.
  A manual edit of the current Working head follows that head; reject an explicit contrary
  keep choice atomically, rather than silently advancing. Timing-only Apply has its own choice policy.
  A refreshed merge/conflict preview binds its Working choice to the same fresh recording
  head, role revision and preview fingerprint; keep the visible editor snapshot and drafts
  separate. Closing, replacing or leaving that preview invalidates a pending choice before POST.
  New first results may become Working automatically only when no result, including Trash,
  or earlier designation exists. Fresh producer requests freeze the exact Working reference and
  independent role revision; successor promotion commits atomically only if the source version,
  source head is live and the frozen role still matches, including when the selected source was an
  alternative or the role was unassigned. User selection, later edits and unmarked legacy jobs
  cannot be overwritten. Trusted job authority is stored separately from formerly arbitrary job
  parameters and binds its recording, frozen role, exact source/version/snapshot and accepted
  request where applicable; retries retain that evidence. A later disjoint timing Apply can also
  advance across at most 16 intervening automatic timing events under a 16 MiB verification budget
  only after exact source, role/event, job-authority and Apply-receipt reconstruction proves the
  entire chain and its current head. Explicit choices, manual/unrelated edits, missing proof or
  exhausted budget leave Working unchanged and expose a warning. An explicit Keep or reselect
  of the same Working role records a local-user self-loop event and advances the role revision,
  including an unassigned role, so it fences earlier jobs. Automatic events record a system actor; user-consented edits record a
  local user. Role history, exports and restore validate these observations (ADR 0133).
- V182: A bounded startup pass may designate the creation version of a recording's only
  result as Working once, with the system `working_auto_first` event, only in an unrestored
  library, without any role row/event, Trash, second result or version, edited passage,
  unsettled job or uncompleted Working authority, and when both projections equal the intact
  creation payload; each decision is fenced in one writer transaction. Project snapshots use
  the ordinary writer only for a proven ID-owned, link-free folder and targets; staleness
  compares the bounded snapshot's actual Working roles against SQLite, never file timestamps.
  Unverifiable or foreign existing snapshots are retained with a visible warning; interrupted,
  refused or failed updates are retried at start. Budgets, short busy waits and
  interrupted statements bound startup; unfinished work continues at the next start (ADR 0141).
- V150: Conflict/restoration choices use exact source/destination/base versions, current revision
  and preview fingerprints. Apply recomputes under one writer transaction, preserves literal
  unselected passages/originals and named voices, and appends immutable decision evidence;
  unresolved/stale choices never silently overwrite research edits.
- V151: Fresh public local enhancement captures sealed actual request/audio/stage bytes and
  limited runtime evidence, then binds exact producer job/creation version transactionally.
  Source flags cannot assert capture. Verify present receipts before propagation, preserve
  inheritance and explicit legacy gaps; historical reports need no inference/runtime/files.
- V152: Fresh bounded local timing captures per-unit request/audio/alignment and exact assembled
  output separately from original producer provenance. Apply atomically commits the matching
  timing successor, mirror and job receipt; lost replies verify/reconstruct the exact successor
  without reapplying it. Compare current wording/bounds/word maps, skip newer timing and keep
  current voices/notes/unselected passages. Legacy cached units remain not_captured.
- V153: Fresh Runtime resident cohorts accept at most8 tasks over at most2 seconds, stage them
  through one exact pinned model slot and retain accepted input/source identities. Unload on
  engine/pin/device change and error; a failed unload poisons the process rather than retaining
  uncertain models. Legacy captured plans keep their per-file protocol.
- V154: Intermediate stage acknowledgement is not final task completion. Retain every accepted
  cohort member and its uncertainty through Stop/timeout/child loss/restart, preserve valid
  completed/partial outputs and never replay an accepted uncertain task. Source loading compiles
  the exact hash-verified bytes, without stale bytecode or a second-read identity gap.
- V155: Legacy singleton ordinary cleanup requires fresh exact terminal allocation ownership
  plus saved results or verified public-only metadata inventory. Failure never grants unsaved
  private-output disposition; cached settlement validates actual local outputs before cleanup.
  Previews expose bounded stage/count metadata only, not transcript bodies or disposal consent.
- V160: Library → Backups creates a complete v2 capture through
  `POST /api/library/portable-backups` only in the serving app (lifespan lease) with no
  operational queued/running job or active worker. Idle Runtime capacity observations delay,
  never refuse, a capture; a refusal creates no partial folder. Every other mutation waits for
  the capture gate, and capture waits for in-flight mutations; host/same-origin checks reject a
  request before it can occupy admission. The Library UI lists saved creator receipts as
  unchecked and recovers one only after a fresh full transport integrity check of its published
  package against that receipt; it never treats a receipt alone as completed capture or a check
  as a test restore. Lost responses report an uncertain outcome and leave partials untouched.
  One Restore flow accepts an earlier creator receipt or a separately held external receipt.
  It checks the independently referenced local or UCloud bytes before enabling restore; remote
  checks stream every object without creating a local copy and report only what matched when read;
  no remote snapshot or lock is implied. A changed source, receipt or reviewed cloud target
  invalidates the check, and Restore revalidates the package independently. Failed,
  incomplete or uncertain checks retain existing files and operation references. Checksums stay
  hidden in the normal flow; restore recovery references are available on explicit request or
  after an interrupted restore. The local immutable restore index (ADR 0140) binds the source,
  receipt hash, target and UCloud destination before any restore side effect; previous attempts
  remain discoverable, but selecting one requires a fresh check. Show older
  database-only inventories under Restore only when present, label their missing recordings,
  and offer no new inventory creation in the Library UI; retain prior files and APIs.
  Before creation, the Library shows the default backup parent inside the local library and
  allows choosing another parent; it explains that a folder on the same device does not protect
  against device failure and does not claim that another path proves physical independence.
  A best-effort local Windows volume observation can identify the same filesystem volume;
  different, network, linked, inaccessible and unsupported paths remain unknown, never independent.
  Capture uses the destination frozen at request time and never overwrites a newer UI choice.
  Completion states the scope of checks performed during creation and is not a test restore.
  Restore creates only a new registered library and
  resumes with the same operation reference. An unapproved upload is refused before journaling.
  UCloud backup resolves a unique configured server/project, presents writable project-drive
  backup locations and asks for explicit whole-library confirmation. Ambiguous projects block
  new backup selection. A missing pinned profile is never silently substituted for a frozen
  operation; preserve exact provider IDs, approved transfer origins and destination for recovery.
  Approval binds the observed destination and stale profile/settings revisions are refused.
- V181: Backup storage Save requires the exact displayed provider server/project and a
  revision of the settings observed on load. Hold the OS operation lock over comparison and
  atomic write; every successful v3 envelope receives a fresh write ID, preventing approval
  ABA. Preserve plain/v2 reads, never rewrite frozen transfers, and reject stale/overlapping
  saves without inventing consent. An unavailable profile reports the same observed file's
  revision, and conflict reload retains the unapproved draft while showing the current saved
  destination, transfer origins and approval state, and adopting its revision for an explicit
  new decision. Upload, cloud restore and retention preview require the reviewed revision
  and target fingerprint, preventing stale tabs from acting on an unseen destination. Failed
  saves disable upload; an uncertain reply requires a fresh read
  (ADR0134).
  An app-owned creator receipt is journalled as a `validating` upload under a per-operation
  lease before package-file verification; it binds the exact target and request (ADR0139).
  Compatible retries retain that ID, incompatible target changes cannot strand it, and explicit
  local-only abandonment requires exclusive admission ownership. Remote uncertainty is not
  abandonment. Duplicate completed/trashed uploads to the same physical UCloud folder are
  refused. Stop acts on the displayed local snapshot and needs no remote location listing;
  an already in-flight request may still finish.
  Retention is a fingerprint-bound manual Trash action. UCloud/DPO acceptance remains external.
- V148: Delete cancels exact captured owned work before local removal; shared allocations
  affecting other recordings require explicit impact confirmation. Finish-only continuation
  tokens reject edits/new jobs/Retry/generation or membership changes, wait for all worker
  ownership and admission groups to settle, and retain unresolved remote evidence. Remote
  Stop runs outside queue locks and merges current journals. UI treats202 as pending, keeps
  errors in its dialog and ignores late responses after navigation. No durable deferred Delete.
  A locally inactive allocation with known terminal remote state and confirmed cleanup retains
  historical membership for revalidation but does not expand cancellation to deleted jobs.
- V165: Delete previews bind exact batch-owned folder/server/project and shared-file impact;
  confirmation authorizes only that disposition. Drain workers, reserve the allocation control
  slot, revalidate before Trash and fence recovery/producers; no network under queue/DB locks.
  Batch cleanup failures/uncertain submissions do not block local deletion: owner journals and
  actionable Activity survive without job/project rows; Retry never submits inference. Keep
  surviving unsaved members' folders/recovery, skipping only receipted-and-absent deleted jobs.
  Periodic automatic settlement preserves all available bounded output and proves terminal
  ownership; complete unsaved results, missing originals and uncertain evidence remain blocked.
  Clear only hides cards; restored owners stay inert; never purge Trash/shared runtime files.
  Legacy single-job journals remain guarded until independently durable release is available.
- V166: Windows managed deletion retries only a read-only attribute on an already validated
  regular target; never bypass sharing/ACL failures or follow links. Filesystem failures return
  an actionable409 and keep database entries. The same open Delete dialog clears pending
  progress and enables retry after failure even if the project projection was refreshed;
  late replies never alter a reopened dialog or a different route.
- V167: Local Delete stages exact owned files/directories by exclusive same-volume rename
  before deleting SQLite rows; never recursively unlink live research files. Durable bounded
  plan/marker identity plus fresh exact database ownership decides file-only rollback versus
  committed cleanup. Mixed/colliding/unknown evidence stays pending; no automatic logical
  Delete on restart. Close an uncertain COMMIT before checking its actual outcome. Cleanup
  runs outside queue/snapshot/writer locks; pending journals stay actionable independently of
  deleted jobs and block capture/move/switch. Recover before backfill/workers; unresolved live
  recovery gates mutations. Before staging, drain already-admitted HTTP mutations and exclude
  new admission through commit/rollback settlement; waiting requests recheck recovery after
  the gate opens. Acquire exclusivity before queue/snapshot locks, refuse competing promotion
  without waiting, and release before recursive cleanup (ADR0106). Async gates never wait on
  claim locks; blocking admission waits use their own bounded thread limiter, never the
  endpoint pool whose earlier mutations must drain. No worker re-entry of the serving lease.
  Retain explicit platform/power-loss/
  OneDrive durability limits.
- V168: Fresh/unset Processing settings enable missing local model downloads and UCloud
  preparation for explicit requested work. Saved false and omitted-save choices survive;
  reads/checks/saves never execute downloads. Legacy frozen LocalSettings/EnhancementOptions
  missing flags retain false. Automatic placement never acquires UCloud standing consent
  after routing, including a stamped-runtime/missing-model mismatch (ADR0103).
- V169: Shortcut creation is explicit, per-user and collision-refusing; exits before library
  resolution/migration. Pin the installed source/venv and supported launch arguments, never
  keys or ambient environment; clicking neither installs nor synchronizes. Preserve real
  Windows/Mac acceptance and unsigned source-launcher packaging limits (ADR0104).
- V170: Explicit Runtime setup retry requires fresh exact no-POST/no-allocation/no-publication
  proof, initial attempts and actual empty dictionary receipts, no output/Stop/disposition or
  reserved capacity. One writer CAS queues same frozen work and increments epoch, preserving
  source/params/task/attempt identity; normal claim reacquires capacity. Never use this path for
  accepted/uncertain submissions. Diagnostics include only allowlisted public stage/code/file
  and time. Check current code/source/Protected approval and retain restart proof (ADR0105).
- V171: Every static dialog in the main interface exposes an accessible name derived from
  its visible heading, including when that heading changes.
- V172: A manual Check UCloud again in UCloud settings may queue missing base Runtime preparation only with
  saved consent and a fresh matching connection; passive checks never allocate. Schema16
  library-level owners retain frozen identity and durable submission/control evidence,
  deduplicate active/uncertain work per target, and never repost uncertain allocations.
  Stop records intent and controls a known allocation independently of the preparation lane;
  only observed terminal state releases capacity. Activity retains every unresolved owner
  plus bounded recent history; corrupt/archived owners cannot starve work or become active.
  Restore leaves these owners inert. Baseline public caches are included; additional ASR
  profiles and gated speakers remain separate. No recording is uploaded (ADR0122).
- V174: Nullable project default and recording override use canonical packaged language codes;
  recording overrides project, then existing model settings. Each accepted request freezes its
  selected configured language and scope, rejects stale edits and known incompatible models, and
  groups native work only by compatible frozen settings. Existing jobs and unset preferences
  keep their old behavior; schema17 portable restore preserves choices or initializes old ones
  to null (ADR0125).
- V175: New enhancement, timing, passage repair and retranscription choices use recording
  language, then project default, then the legacy saved fallback. Freeze language and scope,
  verify preference revisions atomically at acceptance, and preserve old job bytes. Reject
  unsupported language-dependent processing choices; speaker-only diarization accepts other
  recording languages while preserving the preference without claiming language inference.
  An already-saved free edit returns a visible warning when
  opt-in realignment cannot queue. Processing/edit requests recheck relevant pending language
  saves immediately before fetch and do not start until they settle. Partial Processing saves
  preserve every omitted field, and omitted provider language preserves the latest compatible
  profile value, atomically (ADR0127).
- V176: A multi-recording request shows its current, verified routine source inspections inside
  its Activity card through materialization and allocation handoff. Project, recording,
  generation and accepted receipt must match; a different or invalid observation grants no
  grouping. Keep independently uploaded, shared, failed and restored inspections separately
  actionable. Stop, Retry, Clear and Remove retain their existing exact ownership and tokens.
- V179: Project settings stage name, classification, default language and storage until Save;
  Cancel discards the draft. Save checks observed values before mutation, confirms any move,
  reports partial or uncertain outcomes and persists a browser-local warning before dispatch;
  an unconfirmed request may still finish after refresh and needs explicit acknowledgement
  before another Save. This warning is not server-side settlement (ADR0132).
- V178: Bulk recording-language changes freeze the exact selected IDs and their language
  revisions, validate one project and update all or none in a single write transaction.
  Preserve unchanged revisions and frozen jobs; allow explicit inheritance. Filtered select
  all selects visible recordings and is disabled when all visible recordings are selected;
  Deselect all clears the whole selection. Guard pending saves
  and report uncertain or failed responses without claiming success (ADR0131).
- V1: first passage and Unicode survive import/edit/export; malformed imports execute no code.
- V2: edited word retains interval. Legacy free rewrites invalidate stale maps; ordinary Text
  edit-v2 uses V196's explicit conservative policy without changing historical replay.
- V3: originals remain recoverable; revisions prevent lost updates; projects remain isolated.
- V4: media seek uses byte ranges; audio/video share the original recording timeline.
  Playback lookup in both panes handles untimed gaps and overlapping intervals without reordering text.
- V5: keys never returned or logged; mutations same-origin; managed paths resolved by IDs.
- V6: job/chunk successes persist; restart never silently resubmits uncertain paid requests.
- V7: chunk offsets restored; missing timestamps never fabricated; chunk speaker IDs scoped.
- V8: real media decoding checked; long operations never block the UI request lifecycle.
- V9: one live server owns each library; a second server cannot recover or claim its jobs.
- V10: public evaluation fixtures have pinned provenance, attribution, and checked hashes;
  routine tests use temporary libraries and mocked providers, never paid requests.
- V11: WER/CER use documented Unicode normalization and corpus-weighted edit counts;
  missing/unknown predictions fail evaluation; references supply no invented word timings.
  App evaluation checkpoints accepted job IDs, verifies one producer per expected recording
  and its frozen selected provider/settings, then reads that producer's exact machine-creation
  version/hash. Score and export the same immutable state; active switches, edits and follow-ups
  cannot substitute a newer result. Missing/ambiguous/trashed/corrupt evidence stays incomplete
  without fallback, cancellation or resubmission. Retain safe per-clip receipts and actual export
  format; no arbitrary job settings/secrets or backfilled historical evidence (ADR 0076).
- V12: migration preserves existing text, timing, edits, and revisions; each provider run
  creates an independent saved result; result edits, speakers, and exports remain isolated.
- V13: queued work freezes provider identity/settings; different providers may queue on one
  recording; duplicate active provider requests are rejected; failures/retries keep successes.
- V14: background provider completion keeps the existing active result; explicit result/import
  selection checks revision, retains playback state, and rejects stale edits and cross-recording IDs.
- V15: Transcribe uses the provider visibly selected at click time, even while saving that
  selection is pending; preference persistence must not submit work to the previous provider.
- V16: known OpenAI models use supported response formats, including legacy job settings;
  gpt-transcribe uses languages rather than language. Custom backend capabilities remain explicit.
- V17: model checks send no audio, start no transcription, save no draft key, and expose no
  upstream message. Listed/unlisted is model visibility, not proof of billing or endpoint success.
- V18: protected projects use only explicitly approved configurations at selection, enqueue,
  retry, and before each upload; approval revocation/deletion or endpoint/model changes block
  pending work. Existing in-flight requests cannot be recalled.
  Transcription/model-check clients do not follow endpoint redirects; configure the final URL explicitly.
- V19: project folder choices create dedicated app-owned subfolders; media/export access stays
  ID based; existing storage and transcripts survive migration. Saved outputs retain original text.
- V20: UCloud vLLM remains distinct from UCloud Transcriber; control/HF tokens never reach
  inference, SQLite, snapshots or browser storage. Catalogs/discovery do not claim ASR validation;
  awaiting-deployment configurations cannot transcribe. No implicit remote jobs.
- V21: part concurrency stays bounded; out-of-order successes checkpoint with explicit indexes;
  errors/cancellation stop new requests and retain accepted successes. Retry skips exact successes,
  preserves chronological offsets, and shutdown keeps the library lease until requests settle.
- V22: media, source-inspection, API-transcription and admission lanes run in parallel, each up
  to its Settings → Local Processing limit (1..8/8/8/4; automatic media = logical CPUs − 2
  within 2..8, inspection = CPUs/2 within 2..4, API 6, admission 2). Limits gate new claims
  only and apply without restart;
  lowering one never stops running work. Every job is claimed exactly once; native UCloud
  Transcriber keeps its own two-worker lane; cancellation targets the correct worker, all share
  the library lease through drain, and queued preparation is labelled as waiting rather than
  active conversion. Idle workers of one lane coalesce database polls (ADR 0146).
- V23: NVivo Windows export has rectangular Timespan/Speaker/Content rows; Mac export uses
  documented speaker-colon and F5 timestamps. Both retain selected-result text, first/last
  passages and actual segment timing at tenths precision; untimed passages block timed export
  explicitly, never silently disappear. Readable text remains available without timing.
- V24: comparisons align wording in order without editing either result;
  missing passages remain visible and different segmentation can match a group of passages.
  Specific words/phrases can resynchronize after long omissions and inside long passages;
  timing overlap alone does not establish a wording match. Explicit anchors remain local/read-only.
- V25: only Standard/Protected classifications are offered. Old protected projects become Protected;
  partial legacy provider approvals require renewed approval, never widened access. Migration
  backs up schema 3 and preserves all transcript text, original timings, results and queued work.
- V26: UCloud Transcriber validates a matching batch application and declared parameters before
  upload/submission; retains the template product/resources and checkpoints submission intent
  before sending. A generic model enum alias requires declared identity proving the selected
  canonical version; explicit version-bearing values remain valid. Unknown submission outcomes
  never automatically create another remote job.
- V27: native UCloud tokens stay in the credential store/session, never job snapshots or file
  transfer headers. Shutdown leaves remote work recoverable; explicit cancellation affects
  only the recorded job owned by this local run. Downloaded output creates an independent result.
- V28: Protected defaults apply only to new configurations; preserve explicit off/on choices
  and existing approvals. API and workers continue checking saved eligibility and frozen identity.
- V29: UCloud vLLM model configuration needs no live endpoint. Newly saved served IDs equal
  the HF owner/model ID; separate model settings and results remain independent. Catalog entries
  retain pinned provenance and candidate status without implying deployment or ASR validation.
- V30: existing-project storage changes copy managed files before switching the authoritative
  location; reject active jobs, nested/occupied destinations and filesystem links. Failed copies
  retain originals; committed copies survive finalization errors. Other projects and SQLite stay put.
- V31: enhancement freezes the selected text/settings, preserves source results and the active
  reviewed transcript, and appends at most one result per job. Text, punctuation and Unicode survive
  splitting by detected speakers; unaligned/low-confidence words stay untimed and are not interpolated.
- V32: local enhancement runs in a separate optional Python process, one job at a time; cancellation
  stops its process tree, retries reuse completed stages, and restart never automatically reruns it.
  HF tokens stay in credential storage/transient child environment; subprocess output is not logged.
- V33: offline enhancement uses pinned local model snapshots and no implicit downloads or telemetry.
  Alignment inference is bounded to 60 seconds / 120 words / 1200 characters. Long untimed text
  uses locally recognized, distinctive ordered phrase anchors to find windows; ambiguous/unanchored
  words stay untimed. Preliminary recognition never replaces reviewed wording or supplies final word
  timings. Cache completed recognition windows and alignment windows; use disk-backed audio.
- V34: UCloud enhancement freezes server/project/template/runtime identity and source, checks
  current Protected approval before private transfer/retry, and never sends control/HF tokens to
  inference. Journal submission intent/owned ID before uploads; uncertain allocation is not repeated.
  Retry reconnects, shutdown preserves remote work, and explicit cancellation affects only its job.
  Download validated stages before stopping a successful allocation; no implicit runtime/weight install.
- V35: reviewed evaluation verifies media hashes without loading whole recordings, requires exact
  reference/prediction coverage and matching annotation text, and reports WER/CER by language and
  corpus. Timing uses only actual matched annotated intervals; speaker scoring requires explicit
  label mappings. Missing timing/speaker/measurement evidence remains missing, never fabricated.
  Limit per-clip annotation alignment work; reports distinguish synthetic checks from live benchmarks.
- V36: deleting recordings/projects cannot erase unresolved owned UCloud allocation journals.
  Retain unknown submission intent until explicit reconciliation; reconnect only a remote job
  whose unique name matches this local run. Stop acknowledgement or observed terminal state
  permits deletion. Reconciliation starts no job and uploads no recording. Unresolved jobs
  remain visible beyond the recent Activity history limit.
- V37: imported top-level word structures fail with curated errors; timestamp validation never
  echoes field contents. Word timing requires positive, ordered intervals inside known passage bounds.
  Classification changes wait for remote enhancement as well as transcription to settle.
- V38: late route, mutation and polling responses cannot replace a newer recording/result view
  or discard a draft started while a request was pending. Saving locks its passage input until settled.
  Transcript imports require a loaded recording matching the current route and freeze its ID/revision.
- V39: HTML, application JavaScript and CSS carry Cache-Control: no-store, including
  conditional responses. Refresh must fetch the current local interface after an update.
- V40: comparison alignment starts automatically without a manual confirmation form.
  One case-insensitive literal search highlights both panes while all aligned passages remain
  visible, including when only one result contains the query. Preserve word seeking, Unicode,
  literal punctuation and single-transcript filtering when entering/leaving comparison.
  Update search marks in place, retaining passage/word controls and unsaved passage drafts.
- V41: enabling Follow playback immediately reveals the current timed passage, including
  during bounded replay and comparison; playback position, playing state and replay bounds stay intact.
  Follow playback and Show uncertainty start checked; manual scrolling never changes the follow
  checkbox, and explicit choices remain in effect for the current page session.

- V42: word-timed result views flag each passage without usable word timing in both panes,
  including a count and edited/untimed explanations; segment-only results avoid these warnings.
- V43: project exports read one SQLite snapshot, include only the requested project's results,
  use safe unique archive paths and report every skipped item. Downloads never change saved data.
- V44: subtitles require positive millisecond start/end intervals for every passage; never
  invent missing timing. Structured exports retain current text, never stale original words,
  credentials, runtime paths or provider settings. CSV text cells neutralize spreadsheet formulas.
- V45: batch choices cannot disable native capabilities. Preflight all selected combinations
  before ASR; invalid runtime, CUDA access or Protected approval queues nothing.
- V46: provider result and frozen-source enhancement are committed atomically, once per ASR job.
  Retry/restart cannot duplicate the follow-up or overwrite reviewed text; cancellation stops
  only the recorded child. Failed enhancement retries reuse ASR and existing remote journals.
- V47: selected-interpreter hardware detection reports CUDA availability, device names/memory,
  and unknown checks separately; no model download, inference, API call or automatic device switch.
- V48: local new jobs and retries needing inference validate runtime readiness outside the
  queue lock before enqueue; recheck recording/revision under the lock. Legacy workers validate
  before audio conversion. Completed stages remain finalizable without inference dependencies.
- V49: locked standard installation includes pinned WhisperX and CPU Torch; default app Python
  passes the runtime doctor. CPU preflight avoids importing Torch for hardware checks; explicit
  GPU checks allow cold imports. Model downloads and alternate runtimes stay explicit.
- V50: scoped repairs validate membership, source revision and real bounded intervals; decode
  only chosen clips, restore offsets once, retain per-passage checkpoints and append one result.
  All unselected passages/originals and speaker names stay identical; no source is overwritten.
- V51: the local repair worker caches at most one model for five idle minutes; identity change,
  cancellation, runtime failure and shutdown close its tree. Requests stay sequential/offline without secrets.
- V52: notes migration preserves all prior columns. Notes retain Unicode/literal text, use their
  own revisions, persist in project snapshots and never change transcript edits/timings or API input.
- V53: metadata replies cannot replace another item's view/draft or save target. Navigation guards
  unsaved notes/settings, conflicts retain drafts and notes/settings save independently.
  Same-recording transcript replies, including History restoration, retain a newer independent
  notes_revision instead of regressing successfully saved notes or newer drafts.
- V54: a passage speaker assignment changes only its speaker and any new name mapping. Atomic
  result/active-view persistence retains all text, words, timings, originals and other results;
  stale revisions and unknown labels/passages cannot silently change data.
- V55: provider presets come from one validated offline JSON catalog; preserve custom saved
  models, legacy labels, explicit capabilities and frozen jobs. UI and OpenAI formats agree.
- V56: token-first UCloud discovery allocates nothing/uploads nothing; use released API package
  helpers and a verified unattended Batch contract, project-eligible GPU/storage. Ambiguous cost destination
  requires a named choice, never a guessed account/project; freeze resolved identity before queueing.
  Expand available application-group flavors instead of treating search's default interactive
  flavor as the whole catalog; exact pinned configurations bypass group/default selection.
- V57: native cleanup follows validated durable local raw/result persistence and observed SUCCESS.
  Delete only the exact newly journaled app-owned run folder. Never shared/template/drive roots,
  never unknown/failed/cancelled work. Journal deletion status, expose failed cleanup and allow
  idempotent cleanup-only retry without retranscription; no shared Trash purge. Preserve old jobs.
  Journal pending before local result commit; flush the raw file before irreversible deletion.
  Fresh folder conflicts cannot become ownership receipts.
- V58: locked install plans cover Windows, Apple Silicon and Intel macOS. Exclude unsupported
  pinned inference wheels only on Intel macOS; retain core functions and explicit runtime guidance.
  Native folder selection handles macOS cancellation/Unicode without shell interpolation.
- V59: partial alignment retains actual bounded source passage intervals. Playback highlights
  only individually positive, ordered, in-bounds words; incomplete timing remains flagged and
  legacy free rewrites disable stale words until restore; v2 retains only V196-proven maps.
  Legacy replay_windows require exact
  frozen source identity/text mapping, never alter saved timing, and replay the labelled source passage.
  Without either passage or frozen-source bounds, retain Replay over the actual recording
  duration, with an explicit whole-recording tooltip. Never create fragment timing/highlighting.
- V60: recording-review and Transcribe selected controls retain native checked/locked capabilities;
  optional additions open existing Local/UCloud settings and use the same preflight/atomic chain.
- V61: enhancement completed during editing becomes discoverable after the draft settles,
  without replacing the draft, changing the active source or requiring another job event.
- V62: schema-6 migration backs up existing schema 5 and atomically preserves every prior value,
  recording/result mirror and revision. Baselines are legacy observations, never invented edits;
  failed migrations leave old schema/data intact, newer schemas refuse writes.
- V63: every accepted transcript/speaker/notes save appends an immutable exact version atomically
  with optimistic projection updates. Failed/stale saves append nothing; selection/reads do not
  create corrections. Preserve exact Unicode/timing/original fields and independent notes revisions.
- V64: frozen enhancement inputs identify their exact result version/hash; later source edits,
  retries and atomic batch follow-ups cannot change lineage or duplicate versions/children.
  Older snapshots explicitly retain unknown version identity; reject cross-recording references.
- V65: history/version/report access enforces ownership and bounded pagination/selections. Reports
  capture one SQLite snapshot; default output excludes private content/names/notes/reasons, paths,
  endpoint/account/job IDs and credentials. Text opt-in renders only selected current-view versions;
  hashes verify exact payload/export bytes and unknown provenance stays explicit.
  Check report budgets before loading payloads; exact historical reads reject invalid hashes/formats.
  Timing coverage uses comparable current text tokens when provider word units differ.
- V66: Basic/Advanced provider views share one unchanged configuration/credential contract.
  Opening settings/disclosures preserves existing overrides and explicit Protected choices;
  valid basic saves do not require hidden-field interaction or an online vLLM endpoint.
  Saving configuration never implies speech deployment or starts a model check/job.
  Late settings save/forget replies cannot erase newer form drafts, reset visible model choices
  or close/refill another provider's dialog.
- V67: additive schema-7 annotation migration backs up schema 6 and preserves all prior state;
  codebook/run/item/review writes validate same-project frozen version/hash ownership atomically.
  Explicit codebook approval and stable/retired topic IDs survive versioning.
- V68: evidence matches exact frozen passage text at Unicode code-point offsets; reject invented,
  ambiguous, empty/out-of-bounds or unsupported fields/topics. Source speakers/timings are reused,
  never inferred. Uncertain labels need explanation; uncoded does not mean absent/processed.
  Imports retain raw JSON through bounded server validation; duplicate keys/nonfinite values fail.
- V69: suggestions remain immutable/unreviewed; manual items and review events append with
  optimistic revisions and scoped idempotency receipts. Retries cannot overwrite reviewed evidence.
  Source/codebook changes mark older provenance and never relocate evidence automatically.
  UI receipts survive a committed mutation followed by failed refresh, preventing duplicate retries.
- V70: annotation UI/exports clearly identify prototype/manual/imported evidence and review state.
  No fake automatic scorer, model/network execution, credential/raw-original export or transcript
  mutation occurs. Drafts/late replies and project isolation remain protected.
- V71: identical worker JSON bytes are an idempotent no-op, including frozen retries with Windows
  readers open. Changed content retains atomic replacement; denied writes preserve the old target
  and propagate errors. No deletion-first fallback, swallowed error or uncertain resubmission.
- V72: token-associated project defaults require an exact unique server metadata record and an
  eligible owner project; never derive project identity or permissions from the token prefix alone.
  Preserve explicit selections; unknown/personal/unreadable metadata retains named choices.
  Authentication/outage or malformed/duplicate records fail closed, without secrets in diagnostics.
- V73: recognize the observed transcriber/Batch WEB flavor only with its declared boolean
  jupyterLab control, forced false for direct and template-based execution. Other WEB/GUI apps
  remain excluded. Resolve legacy default field aliases to declared camel-case fields during
  discovery, preserve explicit overrides, freeze resolved names and validate again before upload.
- V74: provider form failures remain readable inside the modal, focus the persistent alert,
  preserve unsaved fields and clear obsolete loading text. Late failures cannot affect newer forms.
- V75: discovery errors retain safe HTTP status/connection categories through released management
  wrappers. Never display raw exception messages, response bodies, tokens or request URLs.
- V76: fresh native Transcriber requests use the fixed B200 1g MIG (ADR0124); earlier saved
  full-GPU preferences cannot change new requests. Existing template-bound profiles require
  reconnecting without the template before new requests; never retarget a template. Preferences stay outside normalized
  connection Settings;
  fresh admission freezes the effective exact product for submission, grouping and provenance.
  Validate compatible funded identity without fallback; contradictory, ambiguous or unavailable
  metadata blocks submission. Preserve template bindings, current protected approval and Runtime
  checks, existing frozen/legacy jobs and their identities. One public two-file turbo test does
  not establish suitability of other models or workloads.
- V77: OpenAI/native tabs configure connections without a visible Model or Saved provider/Add/Delete
  selector. Keep model selection at transcription time. Available models lists catalog/existing
  models as enable checkboxes, without UCloud language or machine selectors; OpenAI visibility
  checking remains in the basic area and performs no audio upload. Preserve same-endpoint draft
  keys, per-model settings and unknown saved models;
  Custom/vLLM retain their model/configuration managers. Saving is explicit.
- V78: one complete native B200 setup adds missing canonical catalog models atomically as real
  provider configurations, sharing the endpoint credential. Offline startup backfills missing rows
  under the library lease. Preserve every existing model row, explicit approval/denial, default,
  recording selection and frozen job; no checks/uploads/allocations or duplicated automatic rows.
  Transcription selectors split Provider and Model in bulk and the editor;
  model values remain exact saved IDs and both selectors apply current Protected policy.
- V79: manual Merge up/down and Split passage change only the active saved result, its atomic
  recording projection and appended exact history. Require revision/result identity; preserve
  every unselected passage. Merge inserts one explicit space, requires an explicit speaker when
  labels differ and retains real timing only. Split uses Unicode code-point text boundaries and
  real timed-word intervals, or a deliberate interior playback position for passage-only timing;
  in a partly timed bounded passage the boundary is a playback pause inside the gap at the cursor,
  else the start (or end) of the current timed word next to the cursor (B371); a cursor inside a
  timed word is refused. Untimed text stays untimed. Never interpolate timing, revive stale word maps or discard text.
  New split baselines are local to each half; exact earlier originals remain in History.
  Preserve valid partial maps after word correction through repeated merges/splits. New structural
  outputs mark current maps independently of edited/completeness flags; changed legacy free
  rewrites clear that marker. V196 records its new policy explicitly. Recompute each half's
  coverage; legacy edited/incomplete maps stay disabled.
  Playback and research coverage use the same validity distinction. See ADR 0043.
  Unbounded passages can follow reliable words through their actual intervals only; never fill
  unknown gaps. Fully located split halves may use their own first/last word bounds for replay.

- V80: persisted enabled flags default true for old provider rows and are separate from Protected
  approvals. Explicit enabled_models lists atomically control only their exact endpoint/project
  group, including legacy IDs; omitted lists preserve flags. OpenAI saves append missing catalog
  profiles with shared credentials, preserving existing model rows/settings/approval choices.
  Disabled models disappear from all new-run controls and new enqueue/selection APIs reject them;
  already frozen runs/retries retain identity and continue. Disabling models never deletes results.
  A fresh library bootstraps the OpenAI catalog default with a first-save marker; only a new
  connection or marked placeholder uses default_enabled. Missing rows in older partial connections
  keep the legacy enabled-by-default behavior. Bundled defaults select GPT Transcribe and native
  Whisper large-v3; all bundled CBS Runtime models start unchecked (ADR 0130). Fresh API-created
  UCloud connections without a model list also append Runtime profiles disabled; explicit source
  flags and legacy expansion/backfill remain intact. The UCloud model list is a grouped checkbox-only list; hiding model
  language controls retains their saved values. Missing catalog flags retain true for custom/legacy
  overrides; every existing profile, approval and frozen job remains unchanged. Settings submits
  only intentionally changed model IDs with exact observed per-profile versions; stale or changed
  group membership refuses the save. PUT/POST provider `enabled_models_touched` and
  `enabled_model_versions` carry these optional deltas; older requests without them retain bulk
  semantics. Untouched duplicate flags never collapse (ADR0126).

- V81: result Trash/restore checks recording ownership and current strict integer revision;
  atomically changes visibility, recording projection/revision and explicit history observations.
  Active removal selects the newest live result or clears the transcript view. Restore activates
  only when none is active. Live lists/counts, comparison, ordinary exports and new processing
  inputs exclude Trash; existing frozen jobs, completed producer receipts, historical reads and
  report ancestry retain exact evidence. Retrying a producer never recreates/restores its result.
  Notes, originals, files and all prior versions remain. Trashed roots require restore before
  report selection; retained ancestors are labelled. Browser actions protect drafts/media and
  reject stale replies/navigation. Schema 8 is additive with a schema-7 backup. See ADR 0044.
  Recording projection, live choices and active result reads share one SQLite snapshot even
  when a different store connection concurrently changes visibility. Project-file copies likewise
  capture full live results/project/recordings together before writing recovery artifacts.

- V82: only OpenAI/UCloud appear in provider setup and new-run UI choices. Deferred families retain
  saved data, frozen-job identities and existing results. Label the native family UCloud without
  changing its internal identity. Hide built-in Configuration name without resetting saved names;
  show Available models outside Advanced and retain explicit model/Protected/credential choices.
- V83: deleting a passage validates the active saved result and recording revision, preserves
  every unselected passage dictionary/timing and appends one exact passage_delete observation
  atomically with the projection. Empty final results remain saved and historical text is retained.
  Drafts, pending requests, comparison/playback and late navigation use existing generation guards.
- V84: a native selected-file batch freezes one shared remote run per exact compatible native
  configuration in the same research project. Submit one specification with its input directory,
  import each output only into its frozen owning recording, and retain per-file result/follow-up
  idempotency. One owner journals allocation intent/remote ID; uncertain submissions never repeat.
  Shared cancellation/cleanup must not stop or delete a sibling's needed remote work.
  Native output recovery requires exact input UUID, frozen submitted model/language (known
  language tokens for auto), one unambiguous file; retries reconnect and skip saved members.
  The exact frozen `parakeet` model also permits the observed suffix without a language
  token; this filename does not establish a detected language (ADR 0119).
  Activity renders one canonical allocation card with safe ordered per-file outcomes; retain
  unresolved groups beyond recent limits, using shared state/progress/update time.
- V86: remote cleanup moves only the exact journaled owned folder to UCloud Trash and confirms
  absence by listing its parent; a refused move with confirmed absence is idempotent success.
  Receipt `trashed` (legacy `deleted` stays valid); never empty Trash. Failures stay pending with
  a credential-free reason and cleanup-only retry. Project classification/storage save on change;
  storage moves require confirmation and failed saves restore the saved values.
  Record cleanup stage/code/status/time even for failed local prerequisites; a pending receipt
  means unverified removal, not proof that files remain. Never send raw upstream error bodies.
- V105: Activity "Clear finished" stores one per-library cleared time (settings row 7) and hides
  only listed jobs that are completed/failed/cancelled/interrupted, last updated at or before it,
  and need no action (no remote cleanup, reconnect/no-allocation or UCloud Stop; submission not
  uncertain). Older finished jobs needing action remain accessible in one collapsed summary
  with a count; Clear closes that group and reload preserves the compact presentation. Opening
  it exposes the original actions, and polling preserves that choice. Active/newer jobs stay
  visible. No job, journal or result is deleted; later updates or new jobs show again.
- V162: Activity × (Remove from Activity) hides one finished card; nothing is deleted.
  Settings row 7 keeps `hidden` {job_id: updated} beside cleared_at (Clear preserves it;
  deleted jobs pruned, newest 1000 kept). POST /api/activity/hide refuses queued/running
  work, own UCloud cleanup/uncertain/live-unstopped jobs, unresolved native/runtime owners,
  unresolved native selections and unverified preparation evidence; an update shows the
  card again and obligations are never hidden. Multi-file requests (shared request_id) and
  multi-file uploads (client 32-hex upload_batch in media params) render as one grouped card
  with counts, per-file list, one Prepare (first eligible token) or Cancel (per-job endpoint),
  hidden only as a whole. Carry project/request identity through durable input_acceptance and
  exact producing-result follow-ups; native/Runtime owner identity always takes precedence.
  Count unique recordings as Files, distinct producer requests separately from stages. Expand
  complete bounded membership before recent-history truncation, excluding restored rows before
  the bound. Explicit overflow refuses group Stop/Hide/Clear but retains exact individual
  controls. Unresolved owners outlive recent-row limits. Preserve legacy jobs without inventing
  membership. Active × mirrors the existing Stop scope; unresolved cleanup never becomes Hide.
  Read raw rows, request membership and intent projections in one snapshot so materialization
  cannot turn an observed frozen intent into a generic control. Native status inputs must not
  re-dereference deleted jobs after that snapshot; current retained-owner obligations stay visible.
  Activity notes no longer float; older polls never repaint. ADR 0099.
- V110: Back up library writes library-<time>/ with a SQLite backup-API snapshot and a
  manifest (schema, table counts, integrity/foreign-key result, every original's size, mtime and
  SHA-256, missing originals, exclusions); a copy failing integrity_check is removed; keys/tokens
  and the server lock are never included; unchanged originals reuse earlier hashes (ADR 0061).
- V109: schema 11 collections: one per recording (nullable collection_id, ON DELETE SET NULL),
  one level, unique case-insensitive names per project, plain notes; additive idempotent migration
  after a schema-10 backup; moves/uploads only within the project; deleting keeps recordings
  (Unsorted); files never move. Chips filter cards, select-all and Transcribe selected; uploads go
  to the shown collection; project ZIP/research report take the shown collection; ZIPs use one
  sanitised numbered folder per collection; project.json lists collections (ADR 0060).
- V108: Settings → Local Processing holds language, device (auto/cpu/cuda; auto resolved
  from the cached startup check when a stage first runs and written into the job), standing
  model-download permission, Python runtime and Automatic rules; it shows this computer's
  runtime status and saves on change. UCloud settings hold cloud preparation consent and the
  saved connection's runtime status; opening either tab checks status read-only. Explicit UCloud
  work on an unprepared runtime follows V163's request or saved-standing-consent and fallback rules.
  Re-transcribe (passage) starts at once; the recording page has no enhancement dialog button;
  Versions… merges History and Results; File Settings (download original, import, delete) and
  the project/recording actions live in the sidebar (ADR 0059).
  Manual language is Advanced; its saved da/en value is preserved until real detection is built.
  A confirmed absent GPU disables device/GPU-factor controls without rewriting saved preferences;
  unknown detection does not establish absence. Saved unavailable CUDA is reported explicitly.
- V111: provider catalog schema 2 separates openai, ucloud_transcriber and ucloud_cbs_runtime;
  native entries cannot use engine whisperx; CBS entries require it and a supported underlying
  model. UCloud model IDs are unique across groups. Profiles combine both groups for one
  connection; native enumeration does not. Schema-1 overrides split in memory without file/ID/
  custom-name changes. UI groups and catalog suffixes distinguish (Transcriber)/(CBS Runtime).
- V112: new CBS Runtime jobs independently verify/freeze exact funded single B200 MIG product,
  COMPUTE wallet for the same project/provider, Docker and queue compatibility (AVAILABLE/BUSY).
  No automatic full-GPU fallback. Policy mig-v1 requires first-device actual VRAM >=8 GiB before
  private upload; readiness refusal stops the owned allocation. Partial policy identities fail
  closed; older frozen jobs without either new field keep their original product. Native
  Transcriber follows its separately frozen allocation policy in V76 (ADR 0063, ADR0123).
- V113: schema 12 runtime owner/member/attempt journals are additive under the library lease,
  with schema-11 backup before writes. Exact compatible new batch_plan jobs attach transactionally;
  native/API/WhisperX mixed requests and frozen-source follow-ups are all-or-none. Old singleton
  snapshots stay unchanged. Source identity belongs to the member, not compatibility. Result,
  version, job and exact durable output/member receipt commit together, including existing
  producer results in Trash and valid completed output after Stop; never restore Trash implicitly.
- V114: dynamic runtime admission/seal/Stop use one SQLite CAS seam and coordinator epoch.
  Only the owner publishes bounded immutable task descriptors/membership; accepted input hashes
  precede inference. Partial publication cannot execute, uncertain acceptance cannot replay,
  and explicit new attempts require known stopped/failed task evidence plus open admission.
  Late files join compatible open allocations; different identities queue another allocation.
  Owned remote status/collection/finalization/Stop remain available after approval revocation;
  current identity/Protected approval is required before each private transfer.
- V115: UCloud → Advanced settings controls runtime allocation capacity (default 2, user range 1..4), reserved durably for new
  batches and legacy API singleton POSTs, including uncertainty. Recovery never needs another
  slot or allocation; lowering capacity never stops owners. One GPU pipeline per allocation,
  bounded local preparation/transfers, independent local enhancement lane. Shared Stop and
  cleanup address the batch; owner obligations survive member completion/removal and Clear.
- V116: concurrent runtime preparation rechecks the stamp under its lock and never rebuilds
  beneath active readers. Persistent reader-lock inode remains stable; destructive preparation
  requires exclusive ownership; legacy reader uncertainty refuses destructive rebuilding.
  Heartbeat/idle/deadline drain preserves exact accepted outcomes; cleanup requires durable
  local output/result receipts and a terminal allocation, targeting only its owned folder.
- V117: stopping a native parent batch may cascade to a shared runtime follow-up only when
  every runtime member belongs to that parent's follow-ups. Check containment and close
  admission in one SQLite transaction; a racing foreign attachment either prevents the cascade
  or joins another owner. Mixed owners continue with an explicit Activity/API notice. Direct
  runtime Stop remains allocation-wide; no indirect cancellation of unrelated files.
- V118: retained private runtime discard requires a whole-owner preview, explicit confirmation
  of its current fingerprint, exact owned allocation/folder identity and fresh terminal proof.
  Verify local originals/frozen inputs and preserve all attempts' available bounded stage bytes
  separately; unreceipted outputs remain unverified. Complete unsaved results block abandonment.
  Atomically fence recovery/result writers and record immutable consent/evidence before Trash;
  retain failures, results, versions and raw artifacts. Stale consent or unavailable preservation
  refuses Trash. Retry the same decision after cleanup failure; never purge or imply consent
  from Stop, Clear, Retry or ordinary cleanup. ADR 0101's explicitly confirmed Delete and
  evidenced automatic settlement are separate authorities. Only proven removal resolves obligations.
- V119: new fixed-language WhisperX ASR/re-transcription evidence is explicitly configured,
  never detected confidence; probability is null even when a forced decoder reports one.
  Bind hashes to exact prepared audio/frozen request and windows to actual prepared samples,
  with pinned model identity and observed/unknown engine version. Present invalid evidence
  refuses reuse/result propagation. Absent legacy evidence stays unknown; never rewrite cached
  stages or change frozen options/identity hashes to add it. No transcript edits or fabricated
  detection/default change. Research metadata uses a bounded privacy-preserving allowlist.
- V120: decoded float-audio caches may be reused for inference only when their complete
  source-WAV hash, frame count and PCM hash agree. A same-sized changed source/corrupt cache,
  missing marker or interrupted write requires regeneration; publish the marker last.
  Before new ASR, verify actual mapped bytes as well as the path; an old mapping can retain
  another inode after replacement. Refuse source drift before attaching a current-input receipt.
  Do not regenerate ASR or rewrite completed legacy checkpoints merely to add this evidence.
- V121: enhancement merges require a live same-recording active source and exact frozen base.
  Prove creation/base correspondence before following passage IDs; reject changed creation
  segmentation. Preserve current wording, originals, unknown fields, structural edits and named
  voices. An unknown incoming voice never replaces an established label. Merge validated timing
  as one group and speakers separately; conflicts keep ours.
  Preview is read-only. Apply recomputes under one writer transaction, binding all heads,
  hashes, live states and revision; append one result_merge version with lineage context and
  unchanged producer ancestry. Reject no-op/repeated/stale Apply; keep the incoming result.
  Reports verify merge reference ownership/hashes and alias IDs without extra text or reasons.
- V122: current browser assets preflight CBS Runtime batch support before enqueue. An older
  backend's 404 shows a restart/no-job message with no transcription POST. Native UCloud and
  OpenAI do not depend on that probe. Preserve other HTTP/network errors; never fake shared
  ownership for historical per-file jobs or restart the backend without authorization.
- V123: gated full-ASR Automatic requires a verified multilingual loaded model, immutable
  requested auto and an exact hash/model-bound completed ASR decision. Keep detector window
  unknown rather than inventing VAD source mapping; record versioned uncalibrated policy.
  Derive supported/uncertain/unsupported/no-speech states from bounded engine metadata and
  retained rows; no-speech needs zero VAD duration plus no rows. Only supported da/en selects
  tokenizer/aligner/cache through copied options. Unresolved direct ASR returns a curated code;
  integrated orchestration may preserve explicit unavailable timing under V124. Reuse completed ASR
  without inference or current-pin substitution; reject missing/forged/wrong-request decisions.
  Validate usable cached ASR rows and parsed frozen language/model/full-ASR context too;
  a matching file hash and row count alone do not establish those semantics.
  Fixed legacy bytes stay unchanged. Keep general Auto admission/defaults and bounded repairs
  unavailable until their complete contracts; pre-checkpoint ASR interruption may rerun.
- V124: full-ASR Auto retains usable text/bounds or validated empty results with distinct
  no-speech/uncertain outcomes. Requested unavailable alignment preserves exact ASR IDs/token
  order and literal null word start/end/score, bound by a strict canonical decision SHA;
  aligned means stage execution, not complete coverage. Validate cached artifacts, never
  retro-stamp them. Not-requested alignment creates no file; speakers are language-independent.
  Validate one source/metadata snapshot before result acceptance/cleanup and reuse only within
  that finalization; retry validates afresh without inference for complete checkpoints.
  Append results without overwriting review; durable capability/labels reflect actual outcome.
  ASR pin is captured; unknown non-ASR pins cannot come from today's registry. Historical
  capture/export stays runtime/file-free, redacts private metadata and refuses mixed or
  contradictory language origins; record missing outcomes/pins as gaps. Read new Auto JSON
  with bounded allocations before decoding, including local artifacts. Shared reader support
  must not admit public Auto enhancement/follow-ups: reject before runtime/network/parent ASR
  or enqueue, including no-extra-feature requests. Keep fixed serialized defaults/bytes intact.
- V125: only explicit saved CBS Runtime full-ASR profiles admit Auto; blank retains Processing
  fallback and other operations/defaults remain gated. Version-checked language edits change one
  raw model row without fanout, credential writes or altered approvals/frozen jobs. Provider
  values and concurrency token come from the same row. Ordinary saves preserve the latest
  Runtime language; new other-model backfill never inherits an Auto opt-in. Display legacy
  aliases truthfully without rewriting them; stale/old-backend replies preserve unrelated drafts.
  Language-row refresh/retry never blesses stale connection/approval drafts with a fresh token;
  advance the generic form baseline only for a verified own language-only mutation from it.
  Auto preparation includes both aligners when requested, never snapshot(auto) or implicit
  downloads. Remote admission requires explicit full-ASR markers, supported model and bounds;
  execute only bounded hash-verified frozen validator bytes with a compatible helper interface.
  Prove accepted request bytes before output classification; bind decisions to accepted/current
  inputs and frozen pins. Validate alignment and speaker semantics before completion/carry;
  refuse undeclared admission caches. Host carry proves ASR before allowing empty rows, retains
  independent valid stages and never redoes completed ASR. Preserve Protected checks, Stop
  harvest and cached-only recovery without inference readiness/private upload/new allocation.
  New OpenAI/official-host requests and native specifications refuse literal auto; omission,
  genuine custom capability, fixed/legacy snapshots and owned native recovery stay compatible.
- V126: internal Auto bounded_alignment requires one known-bound complete prepared input,
  <=60s/120 words/1200 characters, local/concrete device/cached-only and no ASR/speakers.
  Prove request and actual WAV scope before decoding/imports, rechecking the byte cap after
  input binding. Direct detection sees only the
  first actual <=30s contiguous samples with VAD off, a verified multilingual pinned model and
  expected extractor geometry. Bind captured pin/engine, input/request hashes and exact windows
  in a separate atomic decision before alignment. No no-speech claim or text-compatibility proof;
  record not_assessed and experimental policy. Unsupported/uncertain adds no timing. Resolved
  language uses copied options/cache; never changes frozen auto. Validate exact source tokens,
  legal intervals and digest-bound alignment_attempted outcome; validate each present timing
  component even if another endpoint/score is unknown. Refuse unbound/stale artifacts.
  Retry reuses decisions without detection; valid complete execute returns before inference.
  General host/public/remote admission, clip provenance, compatibility and defaults remain gated;
  fixed/full-ASR bytes and existing readers keep their meaning. No implicit model downloads.
- V127: new import capture holds snapshot_lock across owned path/write/fsync/close/hash/parser
  verification and result/version commit; preserve queue→snapshot→DB ordering and direct-call
  safety. Exclusive ID-owned regular files never overwrite or follow managed-child symlinks,
  junctions or escaping paths. Physical bytes must match original bounded upload and declared
  format's normalized hint-stripped payload. Compare path/descriptor dev/inode/size/mtime across
  observations, ctime only within the same stat API; retain exact byte/hash proof. Only explicit
  import receipt/no producing job may
  capture; source dictionaries cannot self-assert. Copy one strict receipt into source/origin
  atomically. Failure changes no result/version/projection; retained orphan files are not receipts.
  Edits/restore/Trash/merges retain origin evidence; derived results follow their own lineage.
  Reports compare both claims in one bounded DB snapshot, preflight and separately budget source
  bytes once per result, never read retained files or leak raw IDs/names/paths/text. Use scoped
  aliases/hash/size/format and not_checked availability; malformed/one-sided/contradictory claims
  refuse, legacy absence/not_captured stays unknown. No schema, backfill, complete backup,
  API/raw-HTTP or historical execution-identity claims.
- V128: wording adoption requires explicit selected same-recording live result/passages and a
  fresh full-text preview; heuristic comparison alignment never authorizes a write. Refuse
  blank/no-op/ambiguous/stale/corrupt inputs. Apply recomputes and verifies both heads, hashes,
  active destination and revision in one writer transaction. Append wording_adopt using exactly
  the free-edit transform; preserve destination bounds/speaker/originals/other passages, source,
  jobs and notes. Invalidate current word timing; never copy timing or auto-run alignment.
  Preserve producer ancestry and record strict source/passage evidence. Reports reconstruct the
  exact successor against verified predecessor/source and expose aliases without private text.
  Preview does not seek; late replies cannot replace newer views/drafts. No guessed multi-match.
- V129: settlement of a runtime child future preserves the current attempt's verified terminal
  outcome and specific curated remote failure code. Generic processing is a fallback only when
  no such current evidence exists; unknown codes stay generic and an old child cannot change
  its replacement attempt. Diagnostic settlement never resubmits work or changes lifecycle rules.
  Early supervisor failure blocks fresh work but not finalization of verified local receipts;
  seal exact membership before unexpected terminal closure, always release waiters, and retain
  only safe allowlisted diagnostics. Allocation-wide upload claims require all-attempt evidence.
- V130: new original-v1 audio inputs are server-built at fresh request boundaries and bind
  the owned original's hash/bytes/format, explicit first stream, observed duration/timebase
  and recipe. Convert directly from original; validate strict decode, actual frames/window and
  durable prepared hash receipts before publication/reuse. Failures/cancellation publish no
  complete receipt; changed sources refuse new inference. Keep heavy I/O outside queue/DB locks.
  Concurrent cold observations of the same decoder signature share its identity probe; each
  waiter retains its own cancellation guard and rechecks the current signature (ADR 0108).
  Missing policy retains legacy sources, completed stages and frozen follow-ups/retries exactly.
  Do not rewrite recording duration or put per-file identity in allocation compatibility. Native
  original upload does not imply an unverified multitrack/codec contract. ADR 0075.
- V131: reports validate original-input recording ownership and every captured preparation's
  exact snapshot binding using pure saved-evidence validators. Preflight total context bytes
  at 32 MiB before loading each version, alongside existing payload/source budgets. Emit only
  scoped source aliases, hashes/sizes, typed sample/window/timebase observations and recipe/
  decoder hashes; no IDs, filenames, paths, free-text decoder output or current file/runtime
  reads. Local validation is not remote inference proof; missing preparations and legacy input
  remain explicit gaps. Edits and merges retain the original observation, never invent evidence.
- V132: repair source inspection uses one read snapshot with SQL byte-length preflights before
  params/payload fetch/parse (32MiB each, 64MiB aggregate). Prove job/recording/result/version
  ownership and full canonical hash, then exact full or timing-trimmed frozen snapshot and
  separate ordered selected projection. Strict explicit selectors/groups retain complete
  passage dictionaries/speakers; a subset hash never substitutes for the full source hash.
  Full-ASR dispatch markers cannot masquerade as scoped alignment; malformed huge numeric
  bounds receive curated refusal rather than arithmetic exceptions.
  New heads, active switches and Trash do not replace historical input. Missing legacy proof
  refuses only the inspector; no mutation/runtime/media/network/current-transcript read.
  Return safe internal receipt only; observed params hash is mutable observation, never
  frozen request/compatibility identity. No extraction/timebase/approval/admission claim.
- V133: alignment bundles prove full historical ownership and exact selected projection, then
  bind each actual clip to the job's original-v1 snapshot, recorded extraction window and frozen
  runtime descriptor. Bound units/JSON/PCM/inventory before publication; canonical PCM-only WAVs
  carry no ancillary metadata. Use ordinal owned paths, strict regular-file/hash/geometry checks,
  marker-last immutable reuse and explicit partial-generation retention. No unselected text,
  speaker names, paths or credentials. Validate nullable clip outputs against exact unit binding
  and offset once; no inferred passage bounds, remote admission or unimplemented ASR operation.
  Physical size precedes reads; same-descriptor bounded copies reject linked artifacts/drift.
  Marker publication never overwrites an existing commit and requires a hard-link-capable volume.
  A word span belongs to exactly one selected passage; mapper consumes trusted in-process validation.
- V134: offline model comparisons bind exact candidate/artifact/reference observations and
  distinguish supplied unverified identities from producer receipts. Preserve strata, missing
  metrics and training/holdout qualifications; reuse corpus-weighted scorers, never manufacture
  scores, rank unrun models or relabel reviewed/invalid edit claims as raw ASR. Strict bounded
  JSON and safe exclusive output omit arbitrary/private metadata. Licence evidence does not
  imply CBS clearance, runtime validation or historical base-artifact proof.
- V135: Runtime Activity counts saved results only from completed member receipts; accepted or
  uploaded tasks remain waiting until current accepted/running-task evidence proves execution.
  Stages/reasons use a strict vocabulary and unknown fallback. Stored synthetic progress cannot
  become measured recognition percentage or ETA. Projection changes no journal, action scope,
  cleanup obligation or lifecycle; existing shared actions and small-screen layout remain usable.
- V136: malformed optional library-registry shapes fall back safely while valid current/recent
  paths keep their semantics. An OSError remembering an opened library emits a curated warning
  and cannot prevent browser opening; no-browser stays strict and unrelated errors stay visible.
- V137: portable transport inspection requires a caller-trusted commit SHA and a caller-owned
  quiescent staging tree. Bound bytes/depth before JSON parsing; validate canonical form,
  types/counts before opening objects; exact marker→manifest→closed object hashes/sizes never
  use v1 mtime shortcuts. Fixed-depth
  regular nonlinked reads retain identity/size/drift checks, bounded streams, guard calls and
  final inventory checks; no input mutation, SQLite/Store, runtime, credential or network access.
  Retain raw descriptor ownership through closefd=false stream construction and one finalizer;
  curate iterator I/O errors separately from caller guard exceptions, which propagate unchanged
  with handles closed.
  Return transport_integrity_only counts/hashes, declared_mapping_bytes and restore_ready=false,
  with database/schema/ownership/completeness/gap/credential/quarantine semantics not_checked.
  Never infer restore safety, authenticity/custody, free space or hostile namespace immunity.
  Full S1/T91 and original v1 behavior remain unchanged (ADR 0081).
- V138: inspection migration holds the library lease before backup/writes, leaves old caches
  NULL and research rows/durations/jobs unchanged. Cache/job lifecycle writes are atomic and
  generation/job/epoch-bound; stale workers and corrupt cache cannot replace ready evidence.
  First complete byte identity anchors before decode and survives failure/cancel/retry/recovery;
  partial initial hashing remains unobserved. Bound JSON/physical reads and curate errors.
  Running cancellation retains busy ownership until drain. Claim/run/recover require the
  caller-held same-Store lease without worker RLock re-entry. Generic insertion/update/claim/
  process/retry/cancel/recovery cannot bypass the inspector. Raw cache stays out of API,
  project snapshots and report/export projections; no auto-start, provider or remote admission.
  Guard failures kill/reap the owned decoder and drain its readers before rethrowing the
  original failure; process-context exit cannot substitute for bounded cancellation.
  Missing cache with an existing inspector refuses without inventing a generation; job
  version/epoch parameters retain exact types rather than bool/float equality aliases.
  This foundation alone grants no public activation; V142 defines the accepted lane/admission extension.
- V139: portable row hashing uses only the frozen schema-12 family, never current Store or
  incoming SQL. Pin catalog bytes/provenance; hash structural payload separately. Domain-bind
  table/schema/ordered columns, typed length-prefixed exact observed values, row frames and
  terminal count. Retain literal text/BLOB and binary64 bits; never coerce booleans/types.
  Snapshot each accepted row before later guards; require exact width and total typed nonnull
  unique PK order. Bound row/value/stream/counts with chunk/row guards. No input DB/path
  opening, SQL, source/capture/restore authority or
  hard process/iterator time/memory claim. S1b reader and full S1 remain pending (ADR 0083).
- V140: S1b opens supplied database objects only in the fixed captured-source isolated child,
  after strict copied claims, bounded hash/stat/size/sidecar evidence and verified SQLite heap
  sentry. Never impose its process-global heap cap in the app. Bound protocol, actual aggregate
  rows/encoded bytes and one inspection deadline; retain guard identity and explicit pipe/child
  ownership from Popen return, before support-object allocation, through kill/drain/reap/close.
  Require exact primitive reply fields before enum checks. Success requires settled I/O and
  final byte evidence.
  Exact frozen schema/UTF8 gate precedes integrity/FK/digests; lift metadata-only value limits
  to the supported data limit after that gate and before checks. Validate all nine approved
  literal CHECK predicates; readonly pragma success alone is insufficient. No unknown SQL,
  Store or migration.
  Return database_claims_only/matched_supplied_database, safe metadata, unchecked scopes and
  restore_ready=false. No OS/Python heap, blocking-OS time, hostile-parser security, package,
  history/capture/quarantine or restore approval follows (ADR 0088).
- V141: new Danish CBS Runtime profiles Edda turbo/large and Røst v3 use fixed da full-ASR
  only; preserve existing defaults, profiles, denials and frozen jobs. Explicit preparation
  verifies finite pinned safetensors/tokenizer/preprocessor inventories before local-only CT2
  conversion, no publisher code. Derived immutable artifacts have source/recipe identities and
  marker-last publication. Cached inference and isolated frozen helper loading verify exact
  bytes. Bound conversion receipts to request/audio/source pin; pure history validation needs
  no current cache/runtime/registry. Implementation is not accuracy or licence clearance.
- V142: whole-file HTTP admission is metadata-only and atomically freezes selected intents,
  source ownership, settings/options/features/consent and exact enhancement history. Background
  inspection/materialization holds the library lease and drains bounded lanes; never reserve
  unready remote membership. One native selected-set barrier creates one owner, while API and
  Runtime members may become ready independently. Preserve append-only sibling ASR and current
  Protected/source/endpoint checks without retargeting or broadening consent. Epoch/phase-bound
  actions cannot become Stop after materialization. Recover only proved local work; corrupt
  inspectors do not starve healthy files. Imports retain committed closed originals after
  notification failure. Playback and original-source readiness remain independent (ADR 0085).
  Deferred preparation consent clones a fingerprint-bound exact frozen cohort into a new
  idempotent request with predecessor receipts; old intents remain immutable. Only confirmed
  prepare_runtime changes, never current settings or allow_downloads. No successful/materialized
  siblings rerun; ordinary Retry cannot acquire consent. Successors do not imply ASR completion.
- V143: UCloud selected alignment uploads only validated bounded clip bundles to one frozen
  MIG singleton. Exact per-unit acceptance/start/output receipts preserve completed work and
  prevent uncertain inference replay. Harvest before owned-folder Trash; partial terminal
  outcomes stay honest and apply only through existing revision/text/bounds guards, preserving
  all unselected passages. Journal/publication writes merge current params transactionally and
  retain concurrent terminal/capacity evidence. No implicit preparation, SSH or token upload.
- V144: producer artifacts capture bounded stable managed bytes and allowlisted environment
  observations at the producer, never retrospectively inferred runtime facts. Bind exact
  credential-free request hash, checkpoint and immutable creation version inside the result
  transaction; normalized SDK dumps are output, not original wire response. Missing captures
  stay explicit. Pure historical receipts omit paths/bodies/private IDs and do not claim
  retained-file availability. The helper alone does not implement capture hooks or working roles.
- V145: fresh WhisperX requests freeze retain-estimates; missing timing_policy preserves exact
  legacy score filtering/serialization, including retries. Retain finite valid low-score timing
  estimates and raw uncertainty hints, never invent missing intervals or bypass word-map/bounds
  guards. UI counts timed lexical words separately from complete/partial/untimed passages;
  coverage is availability, not timing accuracy. No historical result rewrite. Automatic second
  alignment pass is explicitly deferred; manual repairs remain separate (ADR 0089).
- V146: package composition requires a native trusted commit reference and creates its own
  frozen transport capture of exact manifest bytes and immutable inventory/stat receipts.
  Derive the fixed object path and claims only from those bytes, without another callback or
  caller-provided capture/manifest/claims/cache. Use unchanged contained S1b, then recheck all
  captured inventories/witnesses after child closure. Public S1a stays compatible; hash media
  bodies once, with the DB's separate pre/post hashes retained. Return allowlisted combined
  observations, current unchecked scopes and restore_ready=false. No partial success, raw
  bodies/paths/IDs, atomic filesystem snapshot, hostile metadata-reset proof, current-schema/
  history/owner/capture/security/quarantine or restore approval (ADR 0090).
- V147: supplied payload observation requires native immutable bytes and native hash reference,
  then conjunctive caps before JSON allocation: 8 MiB bytes, 500000 value/key/container tokens,
  depth24, 2 MiB raw quoted-string interior and 128-byte atoms. Preserve the exact canonical
  snapshot-v1 envelope, opaque inner JSON, empty content and numeric/Unicode spelling. Stream
  canonical UTF-8 prefix comparison without a whole-output buffer. Keep caller guards outside
  parser/hook/encoder catches, preserving exception identity and curating raw parser causes.
  Return safe syntactic counts/hash/policy and explicit gaps/restore_ready=false. Resource
  refusal does not mean corruption; no existing writer/reader, inner semantic/history/DB/
  ownership/restore change or universal Python/OS heap/security guarantee (ADR 0091).
- V107: above the passages, one compact tag row reports word coverage and complete/partial/
  untimed passage counts, with a correctly scoped incomplete-passage repair action and,
  while uncertainty is shown, "Re-transcribe all N of M flagged passages" (flagged passages with
  a known interval <=60 s, at most 200 per request); hide only when neither coverage nor actions apply.
- V106: every passage lacking word timing in the open result shows the same "Add word timing"
  tag and no highlighting. Unbounded passages are aligned with adjacent unbounded passages only
  inside the gap between known neighbour times (recording start/end when none), at most 60 s;
  the gap is never stored; bounds come only from a passage's own completely aligned words.
  Too-long passages/windows open whole-result enhancement. The comparison shows no timing
  actions, only a count in its note (ADR 0058).
- V104: speaker-label preparation verifies the pinned pyannote files (size + SHA-256) on this
  computer; downloads only those files, retries transient connection/locked-file errors (3
  attempts, resuming), never retries refusals, and names each failure. Hugging Face Check token
  checks token, terms and prepares/verifies the local copy. A UCloud speaker job copies the
  verified files to the runtime and confirms the marker before requesting a GPU; nothing is
  downloaded while a GPU job runs (ADR 0057).
- V103: "Add word timing" (one passage) and "Repair all N of M missing word timings" queue local
  align-only jobs for exactly the chosen bounded passages of the open result; output is added to
  the same result as one timing_realign version when no draft is open, skipping passages whose
  text/bounds changed. Several requests may be queued; a passage is not queued twice. Each tag
  shows only its own passage's state. Unbounded/too-long passages and the comparison pane show a
  static "No word timing" tag; no inferred windows (ADR 0056).
- V102: CBS-runtime jobs run as a UCloud Terminal batch job (batchScript = supervisor; flavour
  terminal-ubuntu/debian/almalinux resolved read-only and journaled per run), never the
  Transcriber app's own program. Speaker weights missing on UCloud are fetched to this computer
  with the saved HF token only under explicit preparation consent, then copied; speaker
  detection runs on UCloud and the token never leaves this computer (ADR 0055).
- V101: Re-transcribe (passage action) and Re-transcribe flagged choose bounded passages
  (<=60 s, <=200); one local/UCloud/auto WhisperX job transcribes each padded clip, keeps words
  centred inside the passage, aligns, and appends one combined result (new wording + timing +
  hints for selected passages, unchanged others; empty recognitions keep their text). Only
  bounds are sent; reviewed result stays active; scope passage_retranscription (ADR 0054).
- V100: UCloud catalog models with engine whisperx ("UCloud WhisperX large-v3 / large-v3-turbo
  (CBS Runtime)") are chosen like any UCloud model. Transcribe queues one UCloud-lane job per
  recording on the API-only runtime: faster-whisper (pinned, VAD) -> alignment -> optional
  speakers in the same job, appending a provider_result with hints. Unprepared runtime/model
  is refused with code prepare_runtime; the browser asks, then retries with explicit preparation
  (fetches only pinned public models). Language da/en. ADR 0053.
- V99: opt-in "Realign edited passages" (per library, off by default): a changed free-text save
  of a bounded passage in a word-timed active result queues a local align-only job (superseding
  a queued one); the editor applies its words to the same result as a revision-checked
  `timing_realign` version only when idle; text/originals/other passages unchanged; stale or
  changed passages are never applied (ADR 0052).
  Fresh `edit-v1` free-text saves instead freeze their explicit per-save choice; the legacy
  endpoint retains its saved-setting behavior and ignores extra fields (ADR 0138).
- Manual word timing: a revision- and Working-choice-fenced edit may change one current
  mapped word's text and/or finite ordered start/end within known passage and neighbor bounds.
  Preserve originals, unrelated data and partial coverage; do not infer or promote timing.
  Record `word_timing_edit`, retain durable provenance through structural and speaker-only
  transformations, and suppress affected source alignment-fit hints (ADR 0137).
  A current partial map also supports a spelling-only mapped-word correction with null
  timing retained exactly, without requiring passage bounds or promoting completeness.
- V98: Automatic rules are per library: local word/speaker minute limits (0–360), CUDA factor
  (1–20) and fallback local|stop for longer recordings without usable UCloud; defaults 10/3/6/local.
  Read once per request, the applied limit is named in each frozen reason; queued jobs unchanged.
- V97: without --data-dir the launcher opens the remembered library (libraries.json, paths
  only; explicit --data-dir launches never change it), else the only library found, else a
  new default; several found (or --choose-library)
  serve a chooser. Discovery includes packaged-app redirects; inspection is read-only. The
  Library dialog switches by restarting on the chosen library, refused while jobs are
  queued/running; new libraries need an empty, unsynced folder (ADR 0051). After a switch,
  reload only when the server reports the accepted target path; timeouts and uncertain
  replies retain the page and ask the user to preserve drafts. Browser pages carry a server
  instance on API and direct resource requests, so stale marked tabs cannot read or write a
  later library. The target-ready check exposes only a boolean. Unmarked local clients and
  browsers omitting browser-identifying headers remain unfenced; real multi-tab and browser
  acceptance is still needed (ADR 0136).
- V96: on Windows and macOS the launcher's defaults share one visible home folder:
  <home>/CBS Transcriber/Projects for new projects and <home>/CBS Transcriber/Library for the
  library (ADR 0050, supersedes V90's locations). An existing library only at the earlier
  app-data default is used in place and never moved; overrides and existing projects unchanged.
- V95: enhancement has a local lane (local WhisperX, passage repairs) and a UCloud lane (jobs
  frozen with execution `ucloud`), each sequential, so a long remote poll never delays local work.
  The lane follows the frozen parameters, including retries; cancellation reaches either owner.
  One active enhancement per recording and lane: the server and editor repair buttons allow a
  local repair beside a running UCloud enhancement, but not beside other local work.
- V94: a derived result (enhancement/repair) whose exact frozen source version is no longer that
  source result's newest version reports it: result lists carry the source and current source
  version sequences, Activity flags queued/finished enhancements, the editor names what the
  derived result lacks. Never rebase, merge or auto-activate; computed at read time only.
- V93: deleting a project is not a heading/toolbar button. "Project Settings" in the sidebar
  above Library (project pages only) opens one dialog with Classification, Storage folder and
  "Delete Project"; deletion opens a further dialog that names what is removed and enables deletion only when the exact project name is
  typed; server refusals (e.g. unresolved UCloud work) stay in the dialog. Saved classification
  changes confirm with a toast; slow requests show a busy cursor; buttons show a pressed state;
  the Windows folder chooser opens in front of the browser.
- V92: History restores an earlier version of the active result as a new newest version
  (`version_restore`, context names the restored version, revision-checked, same-result only);
  no version is removed or rewritten.
- V91: DOCX export: one paragraph per passage (bold [hh:mm:ss] and resolved speaker, then text),
  untimed passages included, XML-invalid characters dropped from the copy only, deterministic
  package bytes; also in project ZIPs.
- V90: the launcher's default parent for new projects is Documents\CBS Transcriber projects,
  or the home folder when Documents is cloud-synced; overridable; libraries/tests keep
  <library>/projects; existing projects never move.
- V89: model review hints (avg_logprob, no_speech_prob, compression_ratio, word alignment
  scores, gpt-4o token probabilities) are stored per result beside it (schema 10), bound to
  the exact passage text, never in passages/history/reports/exports; older results rebuild
  once from preserved raw output only when text matches the first version. The editor marks
  the lowest share by rank below ceilings, plus Whisper's warnings, labelled as model signals.
- V88: unnamed speaker labels display and export as Speaker_01, Speaker_02, ... derived from
  the raw label (SPEAKER_00->01, A->01, ENH_x_1->01, part prefix kept, UNKNOWN->Unknown
  speaker), so names stay stable under edits; colliding label spaces get a raw suffix. Saved
  names win; earlier generated 'Speaker N' enhancement names count as defaults. Passages keep
  raw labels; nothing is rewritten. Speaker lists/choosers show the reviewed result's voices
  and its researcher-saved names only; new enhancements keep only names they use. Sidebar
  speaker colors match the transcript badges; labels remain readable and wrap responsively.
- V87: `auto` enhancement resolves per recording at queue time into a concrete frozen local or
  ucloud place with `routing.reason`; never changes place later. Local within the length limit when
  ready; UCloud beyond it when approved and prepared (never preparing it, ADR 0059); else local with
  the reason; fail before creating jobs if neither is usable. Passage repairs are local only.
- V85: new UCloud enhancement execution is API-only and resolves its connection from Provider
  Settings. Keep exact frozen text/source versions and append independent results. Validate the
  remote runtime/cache, preserve bounded transfer/recovery/Protected-policy checks, and never put
  control/HF tokens in specifications, uploaded bundles or logs. Do not imply native ASR is a
  source-preserving alignment API. Legacy SSH runs remain recoverable under their frozen identity.

## §R Verified references
- R32: native Transcriber Batch inputDir accepts multiple files with one output per input;
  initialization Bash scripts and non-interactive jupyterLab=false are application parameters:
  https://docs.cloud.sdu.dk/Apps/transcriber.html#transcriber-batch
- R33: WhisperX alignment uses language-specific public phoneme models; speaker diarization uses
  a read token and accepted pyannote terms when downloading gated weights:
  https://github.com/m-bain/whisperX#speaker-diarization
- R34: pyannote Community-1 supports fully offline inference from a downloaded pipeline;
  HF token is asset-download access, not a paid inference requirement:
  https://huggingface.co/pyannote/speaker-diarization-community-1#offline-use
- R35: SQLite value/row limits and cooperative progress callbacks do not bound aggregate
  schema loading or provide hard process timing/heap isolation:
  https://sqlite.org/c3ref/c_limit_attached.html https://sqlite.org/c3ref/progress_handler.html
- R36: SQLite heap limits apply across a process and depend on supported memory-accounting
  configuration; use isolated containment before untrusted opening, not the serving process:
  https://sqlite.org/c3ref/hard_heap_limit64.html
- R37: integrity_check may evaluate CHECK constraints; exact schema allowlisting precedes it
  when unknown stored expressions are prohibited:
  https://sqlite.org/pragma.html#pragma_integrity_check
- R38: immutable URI mode skips locks/change detection and assumes stable read-only bytes:
  https://sqlite.org/uri.html
- R39: Python communicate buffers captured output; initial OS process creation may exceed
  subprocess timeout, so contained inspection owns bounded pipes and explicit termination:
  https://docs.python.org/3.13/library/subprocess.html
- R40: SQLite hard_heap_limit PRAGMA lowers only; same-child enforcement probes must use the
  final cap or a separately bound capability process, not an assumed later raise:
  https://www.sqlite.org/pragma.html#pragma_hard_heap_limit
- R41: isolated/no-site interpreter flags reduce path/environment imports; explicit captured
  leaf compilation binds app code, while -B alone only suppresses bytecode writes:
  https://docs.python.org/3.13/using/cmdline.html
- UCloud outputs JSON/CSV: https://docs.cloud.sdu.dk/Apps/transcriber.html
- OpenAI: whisper-1 verbose_json supports word+segment times; diarized_json supports speakers:
  https://developers.openai.com/api/docs/guides/speech-to-text
- Batch API excludes audio/transcriptions; explicitly removed by user:
  https://developers.openai.com/api/reference/resources/batches/methods/create
- FFmpeg executable distributed by imageio-ffmpeg: https://github.com/imageio/imageio-ffmpeg
- WhisperX 3.8.6 alignment requires start/end windows and supports disabling interpolation
  with `interpolate_method="ignore"`; runtime Python >=3.10,<3.14:
  https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/alignment.py
  https://github.com/m-bain/whisperX/blob/v3.8.6/pyproject.toml
- Pyannote Community-1 can load a local snapshot; telemetry disabled through its documented
  PYANNOTE_METRICS_ENABLED=0 switch:
  https://huggingface.co/pyannote/speaker-diarization-community-1
  https://github.com/pyannote/pyannote-audio#telemetry
- Starlette FileResponse supports byte ranges: https://www.starlette.io/responses/
- Official CBS visual identity: https://design.cbs.dk/en/cbslogo-fonts-colours-and-design/
- FLEURS mirror: extracted Google test split; Danish WAV + reference text; CC-BY-4.0:
  https://huggingface.co/datasets/FluidInference/fleurs-full
- WER/CER: Levenshtein edits divided by reference word/character count; rates can exceed 1:
  https://huggingface.co/spaces/evaluate-metric/wer/blob/main/README.md
  https://huggingface.co/spaces/evaluate-metric/cer/blob/main/README.md
- vLLM transcriptions: multipart /v1/audio/transcriptions; verbose_json supports segments;
  actual model/format support varies: https://docs.vllm.ai/en/stable/serving/online_serving/speech_to_text/
- Hosting agent inspected in CBS Inference/ucloud-vllm-agent: request.name is served alias;
  packaged nginx proxies /v1 with Authorization and 16m request limit; request.task currently
  accepts chat/pii only. Source: config.py, docs/deterministic-hosting.md, packaged nginx.conf.
- NVivo Windows transcript import requires timed deprotected text with Timespan/Content mappings:
  https://help-nv.qsrinternational.com/20/win/Content/files/import-audio-video-transcripts.htm
- NVivo Mac recognizes speaker-colon text and F5 timestamps; colon-space in content can be
  interpreted as a speaker: https://help-nv.qsrinternational.com/20/mac/Content/files/import-audio-video-transcripts.htm
- SRT has numbered start/end cue blocks with comma milliseconds:
  https://www.loc.gov/preservation/digital/formats/fdd/fdd000569.shtml
- WebVTT uses UTF-8, WEBVTT header, dot milliseconds and optional voice spans:
  https://www.w3.org/TR/webvtt1/

## §T Tasks
id|status|task|cites
---|---|---|---
T1|x|foundation, transcript core, storage, tests|V1,V2,V3,I.import,I.export
T2|x|media, providers, queue, API|V4,V5,V6,V7,V8,V9,I.api,I.provider
T3|x|browser workspace and editor|V2,V3,V4,I.transcript
T4|x|docs and complete verification|V1,V2,V3,V4,V5,V6,V7,V8,V9,I.cli
T5|x|official CBS visual identity with local logos/fonts|I.cli
T6|x|pinned Danish FLEURS fixtures, offline accuracy scoring, opt-in app evaluation|V1,V5,V10,V11,I.evaluate
T7|x|saved providers, bulk/file selection, independent results and comparison|V2,V3,V4,V5,V6,V12,V13,V14,V15,I.providers,I.results
T8|x|OpenAI/custom/UCloud tabs, five presets, correct model formats and account model check|V5,V16,V17,I.provider,I.provider-check
T9|x|per-project folder choice, classification prompt and explicit provider approval enforcement|V3,V5,V6,V18,V19,I.project-policy,I.project-storage
T10|x|independent UCloud vLLM connection, secure setup tokens, model discovery and segment responses|V5,V16,V17,V18,V20,I.ucloud-vllm
T11|x|bounded parallel parts, sparse durable checkpoints, explicit retry and safe shutdown|V6,V7,V9,V13,V18,V21,I.parallel
T12|x|independent preparation, concurrent recordings and accurate queued status|V6,V8,V9,V22,I.api
T13|x|explicit Windows/Mac NVivo transcript exports, readable text fallback and import guidance|V1,V3,V7,V12,V23,I.export
T14|x|visually align matching comparison passages, preserve edits/highlighting and show omissions|V3,V7,V14,V24,I.results
T15|x|one Protected classification, migration and explicit provider approval|V3,V6,V18,V25,I.project-policy
T16|x|Danish/English HF model catalog with pinned metadata and candidate/validated status|V10,V16,V20,I.ucloud-vllm
T17|x|optional local CPU/GPU alignment and speaker detection on existing transcripts; new result|V2,V3,V7,V12,V31,V32,V33,I.results,I.enhance
T17a|x|local enhancement for timed windows and short untimed text, optional runtime, durable stages and browser acceptance|V31,V32,V33,I.enhance
T17b|x|bounded coarse-window discovery for long untimed/long-passage alignment, preserving reviewed words|V4,V7,V31,V32,V33,I.enhance
T18|x|run the same optional enhancement pipeline on UCloud with durable jobs and explicit approval|V5,V6,V18,V31,V32,V34,I.enhance
T19|~|compare Danish/English models on accuracy, timing, speaker attribution, runtime and memory|V10,V11,V35,I.evaluate
T19a|x|offline reviewed-result evaluation with timing/speaker coverage, language totals and reported runtime/memory|V10,V11,V35,I.review-evaluate
T19b|.|representative reviewed Danish/English live model baselines; external fixtures/runtime needed|V10,V11,V35,I.evaluate
T20|x|model-first OpenAI labels across presets, saved selections, results and activity|V12,V13,I.providers
T21|x|native UCloud Transcriber provider through the UCloud API project; mocked acceptance complete, live validation pending|V3,V5,V6,V12,V18,V26,V27,I.ucloud-transcriber
T22|x|replace fragile comparison heuristic with whole-transcript token alignment and explicit phrase anchors|V3,V7,V24,I.results
T23|x|UCloud vLLM saved-model layout, offline catalog and matching HF/served IDs|V5,V13,V20,V29,I.ucloud-vllm
T24|x|provider-specific Protected defaults with preserved user choices|V18,V25,V28,I.project-policy
T25|x|continuous transcript playback beside Follow playback and project heading rename dialog|V4,I.results,I.project-storage
T26|x|highlight literal search in both panes; replace sidebar slogan with Provider Settings|V1,V4,I.results
T27|x|fix existing-project folder chooser with safe managed-file relocation|V3,V8,V19,V30,I.project-storage
T28|x|deep project audit complete; reproduced issues fixed, 227 checks including 12 browser workflows; external acceptance separate|V1,V2,V3,V4,V5,V6,V7,V8,V9,V18,V36,V37,V38
T29|~|offline provider speech extension complete; real profile/allocation/release remains external|V5,V16,V20,V29,I.ucloud-vllm
T30|x|automatic comparison with one highlighted search across both aligned panes|V4,V24,V38,V40,I.results
T31|x|Follow playback jumps immediately to the current passage when enabled|V4,V41,I.results
T32|x|clear Add word timing / speakers action instead of provider feature checkboxes|V3,V31,V34,I.enhance
T33|x|remove duplicate Compare providers actions, retain result comparison dropdown|V14,I.providers,I.results
T34|x|recording/project Export Settings, ZIP reports and subtitle/data formats|V1,V3,V12,V23,V43,V44,I.export,I.export-settings
T35|x|flag passages missing word timing only in word-timed results, including comparison|V7,V37,V42,I.results
T36|x|unattended project batch transcription with optional Local/UCloud extras|V3,V18,V45,V46,I.batch
T37|x|detect local CUDA GPUs through the selected enhancement runtime|V47,I.enhance
T38|~|manual evidence/review prototype delivered; automatic topic/aspect sentiment models and quality evaluation pending|V3,V18,I.enhance,I.annotations
T38a|x|primary-source feasibility, Danish/English candidates, evidence/review/execution/evaluation design|V3,V18
T38b|x|manual evidence/codebook/review prototype with validated imported suggestions; no model execution|V3,V67,V68,V69,V70,I.annotations
T39|x|standard WhisperX dependency and fail-fast runtime validation on every local entrypoint|V31,V45,V48,V49,I.enhance,I.install-inference
T40|x|local individual/all-missing passage repair, warm runtime, explicit review, frozen checkpoints|V3,V7,V31,V32,V50,V51,I.passage-repair
T41|x|inline project settings, download link above review controls, remove distracting slogans|V38,V53,I.project-storage
T42|x|project and recording description/notes with isolated revisions, shared card/review editing|V3,V52,V53,I.notes
T43|~|history/report first delivery implemented; next after current work: version redesign, restoration/merging and full artifacts/environment capture|V3,V5,V35,V62,V63,V64,V65
T43a|x|concrete schema/version/lineage/migration and privacy-aware report design|V3,V5,V35
T43b|x|schema-6 atomic result/notes versions and exact frozen lineage; temporary libraries only|V3,V46,V62,V63,V64,I.history
T43c|x|scoped history/exact historical export and metadata-first research report|V5,V38,V65,I.history
T43d|x|explicit enhancement merge preview and atomic safe timing/speaker adoption with verified research ancestry (offline)|V3,V5,V62,V63,V65,V121,I.result-merge
T43e|x|exact new-import raw artifact capture, inherited receipt and private metadata-first report projection|V3,V5,V46,V62,V63,V65,V127,I.import-artifact,I.history
T43f|x|explicit comparison wording preview/adoption, timing invalidation and exact verified source ancestry (offline)|V3,V5,V62,V63,V65,V128,I.wording-adopt
T43g|x|focused history audit: preserve newer notes on delayed restore and captured Whisper pins in reports; no final whole-project audit claim|V53,V63,V65,I.history
T43h|~|public local enhancement, bounded timing, Working/conflict roles and scoped Runtime/API/native artifact provenance verified offline; combined and live acceptance remain|V3,V62,V65,V144,I.history
T44|x|simplify Provider Settings with basic/advanced options and concise guidance|I.providers,V18,V20,V66
T45|x|change/add one passage speaker without changing word timing|V3,V54,I.passage-speaker
T46|x|editable shared OpenAI/native UCloud presets and all documented Transcriber models|V16,V55,I.model-presets
T47|x|token-first native UCloud setup through released dependency with safe resource discovery; mocked acceptance|V18,V20,V26,V56,I.ucloud-transcriber
T48|x|default owned remote input/output cleanup after successful local save, explicit failure/retry; mocked acceptance|V3,V27,V36,V57,I.native-cleanup
T49|x|macOS chooser, Intel core install plan, runtime limits and CI matrix; real Mac acceptance pending|V5,V48,V58,I.install-inference
T50|x|partial-word playback, retained sentence replay, legacy source-context replay and deferred completion notice|V4,V7,V14,V38,V59,V61,I.passage-repair
T51|x|word timing/speaker choices for single and selected-file transcription, native capabilities locked|V15,V45,V46,V60,I.batch

T52|x|one native setup exposes canonical model profiles; separate Provider/Model controls in project and recording review|V18,V20,V66,V78,I.providers
T53|x|manual adjacent merge and cursor split with exact history and explicit speaker/audio bounds|V3,V7,V38,V62,V79,I.history

T54|x|connection-only built-in setup with basic Available models lists and OpenAI visibility check|V18,V20,V66,V77,V80,I.providers
T55|x|preserve current partial word maps across merge/split, per-half completeness and playback/report consistency|V59,V65,V79,I.history
T56|x|recoverable per-recording result Trash/restore with exact history and frozen-source retention|V81,I.results,I.history
T57|x|two-provider basic setup with UCloud label and visible model selection; defer Custom/vLLM UI|V82,I.providers
T58|x|delete individual passages with exact historical retention and atomic review projection|V83,I.passage-delete,I.history
T59|x|group selected native recordings into one UCloud allocation and durable per-file outcomes|V84,I.native-batch
T60|x|reuse UCloud Provider connection for API-only source-preserving WhisperX enhancement (live acceptance pending)|V85,I.enhance-api
T61|x|automatic per-recording local/UCloud enhancement routing with frozen reasons|V87,I.enhance-api
T62|x|Transcriber-style default speaker names and per-result speaker lists|V88,I.transcript
T63|x|uncertainty review hints: capture, rebuild, relative display and navigation|V89,I.transcript
T64|x|stale-source notes for enhancements whose source was edited after freezing|V94,I.transcript
T65|x|separate local and UCloud enhancement lanes|V95,I.enhance-api
T66|x|one visible home folder for library and new projects on Windows/macOS|V96,I.project-storage
T67|x|library chooser at launch and in-app library switching|V97,I.project-storage
T68|x|researcher-set rules for Automatic processing|V98,I.enhance-api
T69|x|opt-in automatic realignment of edited passages|V99,I.transcript,I.history
T70|x|WhisperX transcription as UCloud models on the CBS runtime (live acceptance pending)|V100,I.enhance-api
T71|x|re-transcribe selected or flagged passages with WhisperX into one combined result|V101,I.transcript,I.enhance-api
T74|x|passage word timing in place, several at once; Repair all as one tag and one version|V103,I.transcript,I.history
T75|x|speaker model preparation in Provider Settings and before any UCloud GPU job|V104,I.enhance-api
T76|x|one Add word timing tag for every untimed passage; gap search for unbounded passages|V106,I.transcript
T77|x|Processing settings tab, automatic device/checks, one-click actions, sidebar actions, Versions, File Settings|V108,I.enhance-api,I.transcript
T78|x|collections inside projects (schema 11)|V109,I.project-storage
T79|x|verified local library backups (UCloud storage step 1)|V110,I.project-storage
T80|~|native four-file output recovery verified on existing allocation; unrelated older cleanup/disposition remains pending|V27,V36,V57,V84,I.native-batch,I.native-cleanup
T80a|x|recover four existing native outputs, verify raw/creation receipts and earlier reviewed history, confirm owned-folder Trash without new inference|V3,V27,V36,V57,V84,I.native-batch,I.native-cleanup
T81|x|no breadcrumbs; Review/Compare/Versions row; search and Versions align with transcript edge; single-result comparison guard|V4,V38,V81,I.results
T82|x|keep current playback word/passage above the real audio/video player viewport on resize and comparison|V4,V41,V59,I.results
T83|x|one canonical Activity card per native batch with per-file outcomes and retained cleanup obligations; runtime groups follow T85|V84,V105,I.native-batch
T84|~|native/CBS Runtime separation and explicit Runtime Auto stages delivered; general language defaults/repair integration remain|V47,V78,V100,V108,I.providers,I.enhance-api
T84a|x|configured ASR language evidence and compatible checkpoint/result/report propagation verified offline; Automatic remains open|V3,V68,V108,V119,V120,I.language-evidence
T84b|x|gated full-ASR worker Automatic decisions, conservative alignment language and cached restart verified offline; app integration pending|V3,V68,V108,V119,V120,V123,I.language-decision
T84c|x|Auto full-ASR partial results, language provenance and runtime-free recovery verified offline; public admission pending|V3,V46,V65,V68,V119,V120,V123,V124,I.language-decision
T84d|x|explicit Runtime model Auto choice, both-aligner preparation and strict pooled acceptance/carry verified offline; general defaults/repairs pending|V3,V46,V65,V68,V123-V125,I.runtime-language,I.language-decision
T84e|x|gated local bounded-input language checkpoint and alignment worker verified offline; public source/compatibility/carry integration remains pending|V3,V46,V68,V126,I.clip-language
T85|~|production resident Runtime cohorts and retained-private safeguards verified offline; real model throughput/live acceptance and older singleton recovery remain|V3,V9,V46,V85,V95,V100,V118,I.enhance-api,I.runtime-discard
T85a|x|schema12 owner/member/attempt journals and atomic compatible admission/result receipts|V113,V114,I.enhance-api
T85b|x|bounded file controller, shared allocation dispatch, exact task receipts and recovery|V114,V116,I.enhance-api
T85c|x|concurrent allocation capacity, safe preparation, late admission and canonical runtime Activity|V115,V116,V117,I.enhance-api,I.native-batch
T85d|x|explicit whole-batch disposition for retained failed/partial private runs (offline)|V113,V115,V116,V118,I.runtime-discard
T85e|.|measured resident model reuse and authorized live runtime batch acceptance|V18,V20,V115,V116,I.enhance-api
T85f|x|retain verified runtime task diagnostics through local future settlement; stopped work stays distinguishable from processing failure (offline)|V113,V116,V129,I.enhance-api
T85g|x|read-only Runtime batch counts, verified active stages and truthful saved-result progress with unchanged shared actions|V84,V105,V135,I.runtime-progress
T85i|x|offline synthetic resident-model cohort experiment with exact stage bytes and measured constructor counts; no production cache or GPU speed claim|V3,V10,V11,V65,I.evaluate
T86|.|funded MIG selection/specification/readiness implemented; live MIG acceptance pending|V18,V20,V85,V100,V112,I.enhance-api
T87|~|common immutable-original transcription input, independent playback preparation and verified provider-specific conversion; preserve frozen jobs|V3,V8,V85,I.inference-source,I.enhance-api
T87a|x|direct-original whole-ASR/whole-enhancement source and receipts verified offline; background admission removes synchronous inspection; bounded paths retain explicit separate behavior|V3,V8,V85,V130,V142,I.inference-source
T87b|x|metadata-only acceptance, two inspection/two admission workers, native barriers, consent successors and guarded lifecycle verified; released through T87e activation|V3,V8,V85,V130,V138,V142,I.inference-source
T87b1|x|inert schema13 inspection cache, cancellable local inspector and fenced lifecycle/recovery; no HTTP/readiness activation|V3,V8,V130,V138,I.original-inspection
T87c|x|metadata-first original/preparation evidence reports with strict binding, aliases, context budget and honest local-validation gaps|V3,V5,V65,V131,I.history,I.inference-source
T87d|~|format-aware compressed transport/remote decode delivered by T87f; real multi-file codec/resource/timebase/cost acceptance remains|V3,V8,V85,V130,I.inference-source
T87e|x|broad API/browser coverage, exact copy rehearsal and authorized main/schema13 activation verified after fresh backup; existing research rows preserved, live provider acceptance separate|V3,V8,V10,V138,V142,V143,I.api
T88|.|long-term AI-assisted analysis and NVivo integration; extend T38/T43 without replicating NVivo|V3,V18,V65,V67,V69,I.analysis,I.refi-qda
T88a|.|analysis P0: validate actual NVivo REFI-QDA transfers and establish hierarchical evidence/review data model|V3,V65,V67,I.refi-qda
T88b|.|analysis P1: shared UCloud AI Platform/OpenAI engine, codebooks, deductive/inductive coding, summaries and review|V18,V67,V69,I.analysis
T88c|.|analysis P2: topic discovery, aspect sentiment/stance and expanded validated NVivo exports|V3,V65,V67,V69,I.analysis,I.refi-qda
T89|.|after current work/T43: reviewed Danish/English model evaluation and CBS licence review|V3,V65,I.evaluate,I.review-evaluate
T89a|x|bind opt-in FLEURS evaluation to accepted producer and immutable creation version with safe artifact receipts; no live benchmark|V3,V10,V11,V13,V65,I.evaluate
T89b|x|offline stratified plan/supplied-output comparisons and incremental governing-licence evidence; no inference or clearance|V10,V11,V65,V134,I.model-evaluation
T89c|x|fixed-Danish Edda turbo/large and Røst v3 CBS Runtime profiles, pinned local CT2 conversion and historical receipts verified offline; no real-model acceptance claim|V3,V65,V141,I.model-evaluation
T89d|.|Hviske 5.3/6 separate-engine support planned but explicitly deferred|V3,V65,I.model-evaluation
T89e|.|opt-in real Danish model preparation/inference, representative interview evaluation and CBS licence acceptance|V3,V10,V11,V65,V141,I.model-evaluation
T90|~|prepared/original-ready and older-recording source-proof repairs implemented; real MIG/timing-quality acceptance remains|V85,V103,V106,V143,I.enhance-api
T90a|x|read-only bounded frozen repair source/selected-projection inspector; no audio/transport/admission changes|V3,V50,V65,V132,I.repair-source
T90b|x|local alignment-only bounded clip bundle producer/validator and offset-once output mapping; public remote routing remains closed|V3,V50,V65,V130,V132,V133,I.repair-bundle
T90c|x|prepared-only API UCloud selected-clip execution, per-unit recovery/control and atomic partial Apply verified offline|V3,V85,V103,V106,V143,I.repair-bundle
T90d|x|bounded cloud source-proof path for older/uninspected recordings implemented and verified through T90f, without requiring another transcription|V3,V50,V130,V132,V143,I.repair-source
T90e|.|opt-in real MIG clip-repair acceptance, resource use and timing-quality evaluation|V3,V10,V11,V143,I.repair-bundle
T93|x|fresh WhisperX requests retain valid estimates and uncertainty hints; separate word/passage coverage; legacy frozen behavior unchanged|V3,V59,V65,V145,I.transcript
T93a|.|automatic second-pass alignment deferred by user as later fine tuning; no default extra inference|V3,V145,I.transcript
T91|~|local working library with recoverable UCloud backup/restore; exact destination approval and retention fencing integrated under ADR0109; real transfer/restore acceptance and CBS IT/DPO review remain|V3,V18,V110,I.project-storage,ADR0109
T91c|x|ADR 0096 routes/UI verified with synthetic API/browser checks and activated on schema15; real UCloud transfer/restore and CBS IT/DPO approval remain|V3,V110,V160,I.project-storage
T91a|x|S1a internal portable transport-integrity observation; no SQLite/schema/restore/upload approval|V3,V5,V137,I.portable-transport
T91b0|x|pure typed-table encoding and frozen schema-12 catalog foundation; public database admission remains gated|V3,V5,V139,I.portable-rows
T91b1|x|contained internal hash-bound database-claims reader with explicit unchecked package/history/restore scopes|V3,V5,V137,V139,V140,I.portable-database
T91b2|x|bind contained database checks to an in-call sealed transport capture, retaining explicit semantic/restore gaps|V3,V5,V137,V140,V146,I.portable-package
T91b3|x|pure bounded supplied result-payload observation with exact canonical envelope and explicit unchecked semantic scopes|V3,V5,V147,I.portable-payload
T92|x|offline Windows/Mac installation audit and resilient launcher/registry fixes; real clean-machine/Mac/installer acceptance remains pending|V49,V58,V136,I.cli
T94|~|README/AGENTS clone/ZIP guides complete; anonymous clean Windows/Apple Silicon/Intel acceptance awaits public availability; visibility unchanged|V49,V58,V136,I.cli
T94a|x|checksum-locked ucloud-workflow0.1.9 release wheel, unchanged other dependency graph, Git-free ZIP package plans and mocked client compatibility verified|V49,V58,V136,I.cli

T95|x|Delete cancels exact owned jobs, confirms shared impact, fences Stop/Retry and waits for worker drain; role/quarantine integration and synthetic API/browser checks verified; schema15 activation complete|V8,V18,V38,V148,I.api
T96|x|Activity per-card Remove from Activity, grouped request/upload cards and unfloated card notes; synthetic API/browser checks|V105,V162
T97|x|Fingerprint-confirmed Delete release, retained cleanup cards, surviving-member recovery and automatic settlement verified; merged/activated on schema15 after authorized backup and preservation checks|V5,V6,V8,V105,V118,V148,V165,I.api,I.runtime-discard
T98|~|Recoverable same-volume local deletion staging, database-fact recovery and durable cleanup Activity integrated and synthetically checked; real-platform and crash acceptance remain|V3,V5,V8,V148,V165,V166,V167,I.api
T99|x|Explicit per-user Windows/macOS source-install shortcuts with frozen launch flags and no implicit app/library effects; 58 focused tests, real OS acceptance separate|V169,I.api
T100|x|Fresh Processing preparation/download defaults enabled; saved opt-outs/frozen jobs retained; Auto standing-consent edge fixed; synthetic API/browser checks|V108,V163,V168,I.enhance-api
T101|x|Active Activity × invokes scoped Stop; current original inspections group by verified upload receipt; member-safe Hide/Retry and browser/API checks|V105,V162,I.api
T102|x|Safe explicit same-owner setup-upload retry before any allocation/private publication, bounded diagnostics and restart/CAS guards verified offline|V6,V112,V153,V170,I.enhance-api
T103|x|Native Parakeet selector alias verified for Transcriber1.12, with declared version proof for generic aliases and single/shared regressions|V26,V55,I.ucloud-transcriber
T104|~|Deep transcription/batch and independent Activity audit; executable defects reproduced and isolated fixes under verification|V6,V26,V38,V142,V167,V168
T105|~|Native Transcriber defaults fresh requests to 1g MIG with explicit full-GPU choice; a two-file public turbo run and user-reported large-v3/Parakeet batches completed on MIG. VRAM/utilization, other models, larger workloads, Stop/recovery and B200 comparison remain pending|V26,V55,V65,V76,ADR0123,I.ucloud-transcriber
T106|x|Offline source-installation smoke helper with exclusive redacted report, bounded bundled FFmpeg and temporary synthetic core workflow; actual platform qualification remains T94|V5,V8,V10,V169
T107|~|Marked restore destination reservation integrated and affected 90-pass slice checked; preserve source/collision/resume/registration safeguards; broader and live acceptance remain|V160,V167,ADR0107
T108|x|A01-A14 remaining application audit plan with source/route/UI inventory, failure cases, evidence states and external acceptance boundaries|V3,V5,V6,V8,V38
T109|~|Execute31October-1November weekend plan: close reliability gates, parallel remaining audits, scoped fixes, regression evidence and handoff|V3,V5,V6,V8,V38
T110|~|Bound and cancel FFmpeg locator/version processes before managed decoding; reviewed/integrated, affected post-fix slice passed; preserve decoder identity and shutdown drain|V8,V9,V130,V167,ADR0108
T111|~|Adjustable parallel local tasks: per-lane limits with CPU-based defaults, live claim gating, separate native lane, coalesced idle polling and admission prefilter; synthetic benchmark and focused tests pass; review and other-machine acceptance pending|V8,V9,V22,V142,I.api,ADR0146
T112|x|Undo split from exact verified history, preserving unrelated edits and atomic Working choice; synthetic backend and browser checks pass|V79,V149,V185,I.passage-split-undo,ADR0147
T113|x|MVP manual passage review marks and progress, independent hash/CAS metadata with schema19 migration, deletion fencing and complete-backup preservation verified offline|V186,I.passage-review,ADR0148
T114|x|Four concurrent native UCloud uploads/downloads with coordinator receipts, accurate per-file stages and drain-safe cancellation verified with mocks; real throughput remains acceptance work|V6,V8,V187,I.native-batch,ADR0149
T115|x|Follow spoken words in saved-text preview while editing without disturbing draft/caret; synthetic browser checks pass|V59,V188,I.transcript,ADR0150
T116|x|Compact recording/format checkbox exports and named collision-safe ZIP folders; single-snapshot archives, Windows names and browser scope guards verified|V8,V149,V189,I.export-settings,ADR0151
T117|x|Lazy ordinal-and-word timing selector with inline Edit word correction; synthetic timing, draft, caret and Working regressions pass|V59,V149,V190,I.transcript
T118|x|Automatic same-allocation status reconnection with bounded backoff and typed interruption; synthetic shutdown/relaunch and explicit Retry retrieve without resubmission|V6,V8,V191,I.native-batch,ADR0152
T119|x|Next to review navigates validated manual Not checked/Needs review status, skips Checked and wraps independently of model hints; focused browser/draft/view checks pass|V186,V192,I.passage-review,ADR0148
T120|x|Opt-in original recordings in selected ZIPs, including original-only exports and clear transcript eligibility; bounded owned-file staging/copy, races, names and browser selection checks pass|V189,V193,I.export-settings,ADR0151
T121|x|Bounded owned-log observations with exact frozen input/model matching, sanitized historical notes and same-ID overnight Retry; 42 log and independent polling/guard cases pass|V187,V191,V194,I.native-batch,ADR0153
T122|x|Fresh-only 24-hour native job default; 64 setup/specification/preservation regressions and a focused UI probe pass, preserving legacy/saved/frozen limits|V195,I.native-batch,ADR0154
T123|x|Guarded early native output preservation, same-owner monitoring, terminal-only cleanup and retained-raw recovery verified with synthetic and independent checks; deployed-image/live acceptance remains|V187,V194,V198,I.native-batch,ADR0155
T124|x|Conservative Text v2 word-time retention, exact legacy replay and repinned contained policy checks; 52 final policy/proof cases, 228 legacy regressions and focused browser checks pass|V2,V59,V190,V196,I.transcript,ADR0156
T125|x|Individual proven deleted-passage restoration, lifecycle barriers and exact current Working edit; 173 backend/structure, seven MVP browser and complete portable-chain checks pass|V185,V186,V197,I.passage-delete-undo,ADR0157
T126|x|Background project/recording ZIP Activity with frozen selection, cancellation, retained streaming download and source-operation fencing; 22 final backend/API, 99 broader (two setup skips) and 16 browser checks pass|V5,V8,V189,V193,V199,I.export-settings,ADR0158
T127|x|Reviewed history/response/store fixes loaded in rc4; exact packaged 100/600 synthetic audits pass source/correctness, 0/48 sampled content actions above 3 s, long save median 2.69 s and Undo split 2.49 s; retain n=3/hardware/history limits and separate background times|V3,V8,V79,V149,V200,I.history,I.transcript,ADR0159

T43i|x|explicit immutable Working-version designations, exact default project exports and role/event validation verified and activated on schema15|V149,I.history,I.results
T43j|x|explicit preview-bound conflict/restoration decisions with atomic history and guarded browser controls|V150,I.history,I.results
T43k|x|fresh public local whole-enhancement sealed artifact capture and exact creation/report integration|V151,V144,I.history
T43l|x|bounded local timing artifacts and atomic idempotent same-result Apply preserving newer maps|V152,V144,I.history,I.enhance-api
T43m|~|actual Runtime accepted-request/preparation/stage bytes bound into producer creation provenance and verified offline; live producer acceptance remains|V151,V153,V154,I.history,I.enhance-api
T43n|~|fresh API request descriptor/normalized output capture, creation binding, reports and portable roles/kernel verified offline; live provider acceptance remains; native capture delivered by T43o|V161,V144,I.history,I.providers
T43o|~|fresh native Transcriber single/batch submission projection and exact output capture, journal-checked creation binding, reports and portable roles/kernel verified with mocked UCloud; live allocation acceptance remains|V164,V144,I.history,I.ucloud-transcriber
T43p|~|bounded startup Working designation of an untouched sole existing result with owned-folder snapshot recovery; synthetic checks on an isolated branch, independent review and real-library activation remain|V149,V182,I.history
T85j|x|production finite resident Runtime cohorts with one identity-bound real model slot and exact stage/task receipts|V153,V154,I.enhance-api
T85k|x|synthetic late-cohort/Stop/partial/child-loss/restart/constructor checks; real throughput remains T85e|V153,V154,I.enhance-api
T80b|~|approved cancelled five-file Runtime folder moved to Trash through main; five legacy singleton obligations protected pending recovery/disposition|V118,V155,I.runtime-discard

T87f|x|fresh whole Runtime format-aware original transport, pinned remote materialization and exact PCM/preparation receipts verified offline; cost/codec/live acceptance remains|V156,V157,I.enhance-api
T90f|x|older-recording selected-source byte/epoch proof and absolute bounded timeline repair admission verified offline; no whole original upload|V158,V159,I.repair-bundle

## §B Bug memory

Historical entries B1–B354 are preserved verbatim in the [bug archive](docs/archive/SPEC-bugs-archive-2026-11.md). Continue numbering after the latest entry below; historical gaps are retained.

id|date|cause|fix
---|---|---|---
B355|2026-11-03|standalone Runtime preparation reported a generic failure after its supervisor returned runtime-busy against a retained preparation lock|V172; expose only allowlisted supervisor failure codes and describe contention without assuming the lock owner is active; safe recovery remains open
B356|2026-11-03|scrolling the transcript with the mouse unexpectedly disabled Follow playback|V41; scrolling leaves the checkbox unchanged, with real-wheel and explicit-toggle coverage
B357|2026-11-03|adding a recording language control pushed transcript search below the actions at wide viewport|V174; keep all controls on one line when they fit and group search with the actions when wrapping; browser geometry regression
B358|2026-11-03|a delayed recording detail response could overwrite a newer card language save, and notes/language revisions were independently mistaken for ordering|V174; fence language projections by their own revisions and current read/save generation
B365|2026-11-03|an internally created follow-up could trust a Working role asserted by its input; a schema-13 migration fixture relabelled a newer schema and a concurrent FFmpeg test exceeded its fixed deadline while still processing|V180; authorize only the internally captured role, construct the actual prior schema and verify frozen-role invariants independently of timing-dependent worker tests
B359|2026-11-03|batch-created source inspections were listed as separate Activity cards because grouping used only upload receipts|V176; project verified source generations into their consuming request without changing inspection ownership or hiding independent failures
B360|2026-11-03|partial Processing settings writes could reset omitted language, device or runtime and race another settings update|V175; preserve all omitted fields in the writer transaction with concurrency coverage
B361|2026-11-03|language support checks incorrectly refused speaker-only diarization for recordings outside Danish and English|V175; allow language-independent speaker work while retaining frozen preference and timing restrictions
B362|2026-11-03|processing could start while an earlier language save was still pending|V175; recheck pending language saves immediately before fetch, then fence processing and edit requests until they settle
B363|2026-11-03|Select all could be clicked before project recordings loaded, losing the apparent selection|V178; start disabled and fence selection to the currently loaded project, with held-response browser checks
B364|2026-11-03|Project settings changed immediately, so closing the dialog could not cancel selections and stale edits could replace newer settings|V179; stage until Save and guard each existing write
B367|2026-11-03|Backup settings refresh or concurrent saves could grant approval to an unseen identity or overwrite a newer revocation|V181; bind displayed identity and serialize revision-checked writes
B368|2026-11-05|An unmerged existing-Working prototype could designate a sole creation whose passages were already edited, left project.json stale after the role commit and could exceed its startup budget inside one scan or lock wait|V182; require untouched passages, refresh owned snapshots with derived staleness and bound statements and busy waits
B369|2026-11-05|The earlier library-removal prototype's preview created/wrote server.lock, and even a mode=ro SQLite open can create -wal/-shm files or write WAL read marks in the library being inspected|V183; the deletion preview reads a private database copy with unchanged signatures and probes only an existing lock; byte-for-byte synthetic regression
B370|2026-11-05|each idle admission poll fully re-validated every intent still waiting for inspection inside a write transaction under the claim lock; with 16 waiting files two workers held that lock ~39% of the time (99% with eight), stalling other lanes' claims and writes|V22,V142; skip intents whose own inspection is pending/running before validation and coalesce idle sibling polls (ADR 0146), with claim-count and unchanged-token tests
B371|2026-11-09|Split at cursor in a partly timed bounded passage (common after WhisperX estimates) ignored the text cursor and required a playback position inside the passage, so placing the cursor alone always failed|V79; use a playback pause inside the gap at the cursor, else the real start/end of the adjacent current timed word, else the playback position; refuse a cursor inside a timed word; browser regression
B372|2026-11-09|After the undo-merge fence (3 November), a passage merge/split/delete still awaiting its reply disabled every passage control of whichever recording was rendered next, so navigating to another recording left it uneditable until the earlier reply arrived|V79; the pending structural lock applies only to the recording it changes; late replies stay fenced by their own recording context; existing late-merge browser regression
B373|2026-11-09|Library Delete checked peer project containment in only one direction and bound aggregate file counts/bytes, allowing a target nested in a live project's storage folder or a same-size nested file replacement to pass confirmation|V183; block overlap in both directions and bind deterministic nested path/identity/kind/size/timestamp observations for library and external trees; synthetic regressions
B374|2026-11-09|Existing-Working reconciliation treated a newer project.json timestamp as proof that its Working roles were current, so touching a stale file after the role commit suppressed recovery and its warning|V182; compare bounded snapshot roles with SQLite, preserve foreign or unverifiable bytes, and ignore timestamps as publication evidence
B375|2026-11-09|The standalone installation smoke used TestClient's testserver hostname, which the production host boundary rejects; an autouse pytest loopback override masked the failure|V5,T106; the helper explicitly uses a loopback base URL, verified by an isolated subprocess with no pytest client override
B376|2026-11-09|Refreshing a stale merge/conflict preview used its fresh content revision with the visible editor's old Working head and role revision, so Apply kept refusing after a successful refresh|V180; bind Working authority to the exact fresh preview without overwriting visible editor state or drafts, and reject closed or superseded previews before POST; stale-refresh, retained-draft and late-choice browser regressions
B377|2026-11-09|Opening the passage editor replaced the timed passage text nodes, so replay could no longer highlight spoken words while the researcher edited|V188; retain a separately labelled saved-text playback preview using existing valid word maps, with preview-only following and unchanged input caret/draft
B378|2026-11-09|Native per-file messages remained Uploading after transfer verification and during the shared running allocation; reconnection could retain them when the remote state itself had not changed|V187; reconcile actual per-file transfer/shared-allocation stages independently of remote journal state changes, without claiming per-file ASR progress; retain aggregate partial-import errors and successful receipts after drain
B379|2026-11-09|Independent manual review marks do not advance recording or Working revisions, so an earlier Delete preview initially remained valid after review progress changed|V186,V148; include bounded scoped review metadata in the deletion digest and reject stale confirmations; new/update/reset and unrelated-scope regressions
B380|2026-11-09|A single HTTP502 from native retrieve_job polling immediately failed every local batch member despite a recorded remote job last observed RUNNING; the real allocation continued|V191; retry only the read-only status request with guarded backoff within the existing monitor budget, retain the exact remote ID, and distinguish an exhausted monitor from a failed transcription
B381|2026-11-09|Named ZIP folders initially checked device names without trimming spaces before the first dot, so recording/result names such as CON .txt remained reserved on Windows|V189; normalize and trim the reserved stem before classification, with actual-Windows and recording/result ZIP regressions
B382|2026-11-09|Next to review still depended on model uncertainty and stayed disabled with zero model hints despite manual Needs review/Not checked passages|V192; use validated current manual status and visible passage order, with wrapping, all-checked and draft/view regressions
B383|2026-11-09|Portable export names checked device stems before truncation; a long spaced name could truncate to reserved CON or CON.wav on Windows|V189,V193; classify reserved stems again after truncation and verify recording-folder and original-filename regressions on Windows
B384|2026-11-09|Ordinary Text saves used legacy free-rewrite behavior and discarded usable timing even for an appended word|V196; versioned conservative correspondence retains unchanged/replacement intervals and nulls inserts, refusing ambiguity with the draft preserved
B385|2026-11-09|A deleted passage restored then consumed by a later merge could still match its old current gap and be restored a second time|V197; newer structural presence and whole-version barriers invalidate old deletion proof, verified through normal edit lifecycles
B386|2026-11-09|Early result download could replace preserved raw after capture-before-commit failed, losing accepted bytes when Retry observed changed remote output|V198; preserved raw is terminal-recovery-only, with eligibility/transfer/receive guards and failed-commit Retry regression
B387|2026-11-09|Closing Export Settings cleared its context while the synchronous ZIP kept building, so the finished response was discarded and another click repeated preparation|V199; move preparation and ready-download ownership into a bounded server-lifetime Activity task independent of the dialog
B388|2026-11-09|A reserved prepared-download descriptor was not released when ASGI streaming lost the client before headers, because neither generator nor background cleanup began|V199; wrap response dispatch in shielded final cleanup, with header/body/cancellation regressions
B389|2026-11-09|The background-export submit helper announced a stale-library error from an abandoned dialog before checking the current scope|V189,V199; defer scoped notices until the frozen opening view is still current, preserving accepted tasks globally and old single-format behavior
B390|2026-11-09|A targeted structural Undo repeated full canonical payload checks and reconstructed unrelated older inverses throughout the shared history scan, delaying long-transcript edits|V200; reuse exact-byte format validation and stop targeted merge/split discovery only after its requested proof is complete; full advisory/Delete lifecycle checks remain
B391|2026-11-09|A proposed native-JSON response shortcut initially exposed nested _sa-prefixed fields and rejected their hidden nonfinite values, unlike the legacy encoder|V200; preserve the legacy fallback on reserved-key bytes or native-render failure and keep serialization outside semantic-error catches
B392|2026-11-09|Consolidating eager payload encoding into JSON row strings initially moved lone-surrogate refusal from before the writer to SQLite binding inside it|V200; validate both serialized mirrors as UTF-8 before opening a connection, with passage/speaker key/value connection-spy regressions
B393|2026-11-09|The schema7 preservation fixture omitted schema19's new empty passage_reviews table from its explicit additive migration expectations; the unchanged base reproduced the same failure|V186; assert the new table is empty separately while retaining exact comparison of every prior row and backup/reopen checks
B394|2026-11-10|Pristine Save passage and boundary-position Split buttons remained enabled although no valid action was available|V201; gate draft/caret/lock state, including reverted text and selection changes
B395|2026-11-10|Cancel rebuilt every passage, search mark and manual review control instead of discarding the one open editor|V201; restore one passage in place with current hints, manual control and playback state; preserve unrelated node identities
B396|2026-11-10|Repeated full native-batch safety guards consumed the optional log reader's three-second deadline before valid log bytes arrived, suppressing progress and early downloads|V202; exclude measured guard work from I/O time while retaining all guards, transport limits and stable-output publication checks
B397|2026-11-10|An older uncertainty browser fixture attempted an ambiguous whole-passage rewrite after ordinary Text saves gained conservative timing correspondence|V196; use an unambiguous single-word correction to retain the hint-invalidation oracle without weakening the production refusal
B405|2026-11-10|A terminal native batch could fail one valid remote output before durable capture and replace its original read error with a generic batch message while eleven results were already saved|V204; bounded same-allocation transient output reads and curated failed-member diagnostics retain saved siblings and require no new ASR submission
B407|2026-11-10|New guarded optional output listing could soften a one-time approval/source exception as unavailable metadata and continue after a later successful guard|V204; capture and propagate the exact listing guard exception before the optional-read catch
B408|2026-11-10|Incremental-publication tests counted every Event.wait as a remote poll, so new read-retry backoffs invalidated positional snapshot assertions|V198,V204; observe actual poll milestones while retaining the two fresh stable-read requirement
B409|2026-11-10|A later parse failure could still display a previous attempt's HTTP503 diagnostic because the target's old read entry survived Retry|V204; clear only the target's stale read observation when parsing/validation is reached and preserve siblings' entries/results
B410|2026-11-10|Native download guards loaded every sibling transcript and Working payload on each streamed block, amplifying a 2.9MB replay into 564 full recording hydrations|V205; fresh metadata projection keeps current guards without transcript hydration; streamed twelve-member and cross-project denial regressions
B411|2026-11-10|Concurrent identical hint loads repeated GETs and hints arriving after Save/Split rebuilt every passage a second time|V206; coalesce complete live contexts, fence late replies and refresh only changed hint decorations with retryable failures
B412|2026-11-10|Save/Split repeatedly decoded and canonically encoded unchanged current mirrors and freshly generated successor history for Working validation|V200,V207; bounded format/binding facts retain fresh actual-byte hashes, strict JSON-origin seeding and the legacy cold fallback
B413|2026-11-10|Conservative Text correspondence rejected ordinary manual changes including a repeated word appended at the end|V208; fresh versioned manual policy preserves proven timing and nulls uncertain words instead of blocking wording
B414|2026-11-10|Split remained enabled inside a completely timed word and then failed on the server, while valid between-word splits depended on timing or a paused audio point|V208; lexical control/server boundaries and timing-independent fresh split policy, preserving legacy inverse behavior

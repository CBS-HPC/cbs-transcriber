# T87 — Common original input and independent playback preparation

27 October 2026. **Accepted direction; implementation brief, not delivered behavior.**

The user decided that every new transcription backend must start from the same immutable
original recording. Required decoding, compression and splitting must start directly from
that original, independently of browser playback preparation. This brief records the
read-only implementation review so later work does not need to rediscover the seams.

The current batch-failure diagnosis takes priority. Do not restart the running app, migrate
its active library, replace frozen jobs, or interrupt owned remote allocations to implement
this proposal. Develop and verify against temporary libraries; activation is separate work.

## Scope and decision

Implement the two bounded stages below. Together they form the smallest complete delivery:
new jobs use original-derived input and no longer wait for browser playback. Stage 1 alone
is useful internal progress, but must not be described as independent playback readiness.

Keep the existing remote `audio.wav` task protocol. The app can decode the original locally
to the existing mono 16 kHz PCM16 WAV before uploading it. Native UCloud Transcriber already
uploads the original and should keep doing so. There is no need to change the remote worker,
runtime image, GPU product, model preparation, submission/reconciliation, or cleanup protocol.

Uploading an original or another compressed inference source to CBS Runtime and decoding it
remotely is a **later transport stage**. It needs its own frozen protocol, pinned Linux
decoder/recipe, checksum and preparation receipts, bounds, recovery tests and measured costs.
It is not part of this minimal implementation.

The earlier [compressed-input experiment](../evaluations/remote-media-preparation-2026-10-27.md)
recommended reusing browser MP3 as an opt-in candidate to preserve today's PCM. The user's
later common-original decision supersedes that recommendation as a future default. Retain
the experiment as evidence: direct-original PCM differs from the current lossy two-step
path, and neither better nor worse speech accuracy was established.

## Existing seams and verified risks

- `media.prepare_media()` selects `0:a:0`, creates browser MP3 and optional video, and extracts
  duration from FFmpeg's container `Duration:` log (`media.py:63–126`). This is currently the
  only readiness/metadata stage.
- `media.prepare_chunks()` reads `audio.mp3` and trusts an existing list-shaped `plan.json`
  when its files exist (`media.py:141–195`). API transcription therefore includes a browser
  MP3 encode followed by a second MP3 encode.
- `enhancement.enhance_window()` reads the same playback MP3 for local and CBS Runtime WAV
  preparation (`enhancement.py:1077–1194`). It also preserves complete stage checkpoints.
- Native singleton and batch paths upload `original<suffix>` (`ucloud_native.py:859`,
  `native_batch.py:523`). Their transfer receipt presently confirms remote file size, not a
  remote content hash (`ucloud_native.py:686`). Do not claim stronger remote verification.
- Playback status gates transcription admission (`app.py:1955`), ordinary workers
  (`jobs.py:315`), runtime attachment (`runtime_store.py:622`), native batch guards/retry,
  enhancement admission and editor buttons. Merely changing the FFmpeg input path leaves
  this coupling in place.
- Legacy runtime transcription identity includes `recordings.duration`
  (`runtime_store._source()`), and language evidence compares frozen request duration with
  the effective recording duration (`enhancement.asr_language_evidence()`). Re-probing and
  overwriting the existing recording duration can invalidate old ownership/checkpoints.
- The synthetic experiment proved that automatic stream selection chose the second/default
  stream, remuxing changed decoded samples by 21.3125 ms despite equal initial PTS, and a
  truncated M4A decoded successfully to approximately half the intended length. Strict flags
  rejected that truncated input but still left a partial WAV. Existence, checksum, matching
  starting PTS, or process exit zero alone is not complete preparation evidence.

Source line numbers above identify the reviewed implementation and may move. The named
functions are the durable anchors.

## Stage 1 — Freeze original input and prepare directly from it

Use a small versioned, server-built audio-input contract on fresh jobs, for example
`audio_input.policy = "original-v1"`. Its absence means the existing legacy behavior. Never
infer the policy from creation time, current settings, available files, or missing playback.
Unknown/malformed policy values fail closed; do not silently fall back to another source.

Freeze at least:

- original SHA-256, byte count and allowed format/suffix, scoped to the internally resolved
  recording ID; no browser-supplied filesystem paths;
- explicit first audio stream selection `0:a:0`, inspected stream count, and a versioned
  timebase/normalization recipe;
- the validated effective input duration and its measurement basis;
- decoder identity and conversion recipe identity, with each job's actual prepared window
  recorded in its preparation receipt.

Capture hashes while copying future uploads where possible. Existing recordings acquire
evidence only when a fresh request validates their original; do not backfill historical
results or claim that an observed file hash was captured at their original import time.
Hash/check the same stable regular file before and after inspection/conversion or transfer;
reject changes, symlinks/junction escapes, missing originals and incomplete source writes.
Use managed ID-owned paths and the existing snapshot/queue lock order. Expensive inspection,
hashing and decoding must stay outside queue and SQLite write locks, followed by a bounded
identity/readiness recheck before committing the frozen job.

### True fresh-freeze boundaries

1. `app.transcribe()` constructs every item before the single
   `Store.add_transcription_jobs()` transaction. Freeze the audio policy for API/native jobs
   and WhisperX transcription jobs here, after original validation.
2. `features.prepare_followups()` freezes the policy inside **each nested follow-up's params
   at the parent's enqueue time**. `Store.add_result()` later inserts those already-frozen
   params when the provider result commits. It must not choose a new policy then.
3. New explicit enhancement, re-transcription and passage-timing requests are separate fresh
   boundaries. If delivered through the shared preparation helper, freeze their policy at
   their request/enqueue boundary too, retaining their exact frozen passage windows/text.
   Enumerate these producers in tests; do not rely on a generic insertion default.
4. `Store.add_job()`, `_insert_job()`, recovery, ordinary retry, native retry, runtime attempt
   retry and cached-result finalization are **not** new-policy selection boundaries.

In particular, do not add an unconditional default to `_insert_job()`: legacy frozen parent
jobs may create their follow-up after the new application version is installed. Their
follow-up must retain its original playback-derived interpretation. An old runtime
`batch_plan`, request, prepared WAV, chunk plan or completed stage keeps its current bytes
and meaning. A retry of a new-policy job also retains that job's original policy and source.

Change the API chunk and enhancement WAV preparation inputs directly to the verified
original for `original-v1`, with **explicit `-map 0:a:0` in every decode/silence-analysis
command**. Preserve the current chunk offset combination and passage clip-relative/absolute
offset rules. Never silently substitute playback if decoding the original fails.

### Preparation completion and reuse

New-policy preparation needs a strict durable receipt linking the original identity and
recipe to the actual prepared bytes/hash, selected window, channels, sample rate/width and
frame count. API chunk plans must bind the same source/recipe and each part's actual bytes,
offset and duration. Use a versioned plan form; keep the legacy list form on the legacy path.

Write pending output, complete cancellation-aware decoding, validate format and completeness,
flush/close, then publish the output and completion receipt atomically enough that an orphan
file is never mistaken for completion. Do not rename a partial WAV after a failed decode.
For a new-policy retry, reuse only an exact matching completion receipt. Never regenerate or
overwrite inputs after runtime publication intent/acceptance, and never relabel carried
checkpoints with another prepared-input identity. Complete cached outputs remain finalizable
without starting a decoder, checking model readiness, or requiring browser playback.

Keep the existing remote request/WAV schemas intact in this delivery. Record original and
conversion evidence locally in strict allowlisted job/result-origin metadata, tied to the
existing prepared-file receipts. Do not add unrecognized request options or weaken the
worker's exact language/request/WAV binding. Preserve old source-identity serialization;
only new-policy runtime members include the new frozen input contract. Source hashes belong
to each member, never to allocation compatibility or another member's identity.

## Stage 2 — Independent original-input readiness

Persist original-inspection state/evidence independently from browser `status` and existing
`duration`. The smallest representation is one nullable recording-level metadata document
with pending/ready/error state and the strict original-input receipt, rather than a general
artifact registry. A small additive JSON field is sufficient; record the exact persistence
choice and migration in an ADR and test only on synthetic libraries before activation.

Keep `recordings.status` as browser-playback readiness for compatibility. Do not overwrite
existing `recordings.duration` during original inspection or later playback completion.
New jobs process against their **frozen effective duration/timebase**, including validation,
routing, scoped requests, result merging and language evidence. Older jobs retain their
current duration/identity behavior. New metadata must not increment transcript/notes
revisions or rewrite result projections/history.

For new-policy work, use original-input readiness rather than playback status in admission,
worker processing, native guards/retry and runtime membership. A browser-preparation job is
not a transcription conflict once original input is validated. Browser conversion failure
must not invalidate a separately validated original or completed transcription. Actual input
validation failure must still prevent processing and private upload.

Perform original inspection before costly browser video conversion and make its readiness
observable independently. Source-ready selected recordings must be allowed to queue while
others await inspection; visibly identify waiting/failed items, never silently omit them or
make one browser conversion reject the entire ready selection. For the minimal delivery,
admit only source-ready items into runtime allocations: do not reserve GPU capacity or
attach unready members simply to model a future dependency scheduler.

Update editor/card/API readiness consistently. Playback/Replay/seek remain unavailable until
their own derivative is ready, while Transcribe can be available from original readiness.
Keep navigation/draft guards and distinguish playback failure from source-input failure.

## Timebase, validation and format limits

The original is a common source, not a promise of byte-identical prepared input across
backends. Preserve the recording's real timeline, leading silence and first-audio selection;
do not derive an offset from nominal duration or initial PTS alone. Do not trim silence,
remux, normalize loudness, choose another track, interpolate, or apply a guessed correction.
Scoped decoding must retain the existing requested source bounds and apply its offset once.

Use the bundled FFmpeg; do not assume a separately installed `ffprobe`. Metadata inspection
is not full decode validation, and container `Duration:` is not proof of the selected audio
stream's complete sample interval. Strict decode/error handling plus actual prepared sample
validation must reject the documented truncation case. Uncertain stream/timeline or
completeness must remain unready/refused until supported, with a curated error rather than
upstream media/log bodies. Bound subprocess output, time, disk use and cancellation.

Retain current upload-format/20 GB limits and backend-specific constraints. API parts remain
within their frozen upload-byte limit and maximum chunk duration; WhisperX retains its
current six-hour/PCM limits. Do not accidentally impose WhisperX's duration limit on native
Transcriber or declare every allowed upload extension supported by every provider.

Native metadata currently proves an `input_file` parameter, not codec support, exact
decoder behavior, stream selection or timeline normalization. For a guaranteed first-stream
policy, restrict fresh canonical native requests to validated single-audio-stream inputs
until the exact selected app contract establishes an explicit selector. Retain old frozen
native jobs unchanged. Multiple-track/native offset support requires separate evidence; do
not infer it from application version or successful local decoding.

Keep Protected approval and frozen endpoint/model checks before every private upload/retry.
Preserve redirect refusal, readiness before CBS Runtime private upload, uncertain-submission
rules, allocation ownership and durable save-before-cleanup. Native local source/hash
evidence must not be advertised as remote checksum verification when only remote size was
confirmed. Reports expose bounded safe hashes/recipe/measurement evidence, not paths,
filenames, credentials or raw media, and retain legacy unknown evidence honestly.

## Required verification and implementation gate

GO for implementing the two stages on temporary libraries. Do not enable the new workflow
until these offline checks pass:

- New API chunks, silence analysis and local/runtime WAV preparation use the exact original
  and explicit first-stream map, even when playback is missing, different, failed or later
  replaced. Native uploads the same original. No silent playback fallback.
- Source identity survives original-path/symlink attacks and rejects mutation during
  inspection/preparation/upload, hash mismatch, wrong recording, missing files and malformed
  policy/receipt. No source/body/path leaks in errors.
- Distinct multi-track/default-track fixtures, leading silence, codec priming/trim, nonzero
  and negative PTS, video/audio offsets, VBR/long input and scoped seek boundaries establish
  the supported original-to-prepared-to-playback mapping. Reject unsupported cases; never
  manufacture timing. The existing experiment did not cover all these cases.
- Strict truncated/corrupt/no-audio tests prove no partial output/receipt is accepted. Check
  actual WAV shape/frame count, upload limits, cancellation, disk/rename failure and restart
  between output publication and receipt publication.
- Legacy queued/running jobs, old list plans/WAVs/checkpoints, old runtime source identities,
  completed cached-stage finalization and delayed legacy follow-ups retain exact behavior.
  New retries reuse their frozen original/duration/recipe and refuse conflicting receipts.
- New runtime member retries, late admission, carried stages and uncertain publication never
  change accepted input bytes or allocate again. No unrelated worker/code-hash changes.
- Original readiness allows new API/native/runtime jobs while playback is preparing/failed;
  invalid originals remain blocked. Ready files in mixed selections proceed visibly, with
  no capacity reserved for unready runtime work. Old jobs retain their readiness path.
- Playback completion cannot overwrite frozen duration/source metadata or invalidate old
  ownership. Browser controls, status labels, result arrival and late responses preserve
  active views, notes and drafts.
- Protected denial/revocation still blocks private upload. Result/version origin contains
  matching server-built input/conversion evidence; reports preserve privacy and do not
  invent historical capture or stronger native remote verification.

This review used source code and synthetic evaluation documentation only. It made no code
changes, provider calls, private media/library reads, allocations, downloads or app restarts.
No live UCloud, cross-platform decoder, speech-quality or NVivo acceptance is claimed.

# T84: automatic language handling review

**Status:** design proposal, 27 October 2026. No implementation or runtime change.
Inspected baseline: `0159052`, with concurrent T85 work excluded from this review.
This document is deliberately separate from SPEC, TODO and architecture decisions, which
are owned by the active implementation chat.

## Recommendation

Offer **Automatic**, with Danish and English overrides under Processing → Advanced, for
new processing settings. Implement it in the worker and frozen request protocol before
changing the default. Keep `options.language` as the immutable requested choice; record
the concrete language used in a separate, verified processing receipt. Existing saved
`da`/`en` choices and frozen jobs must retain their present meaning and hashes.

Use the existing pinned multilingual faster-whisper model for detection where ASR has
not already supplied usable language evidence. No new detection service, dependency or
model is needed for the initial Danish/English scope. This does not establish detection
quality or processing speed; those require measurements on representative recordings.

Automatic language and Automatic processing place are different decisions. Continue
resolving local/UCloud routing at enqueue time. Resolve language only when the permitted
audio and an inference runtime are available, without changing the frozen routing choice.

## Current paths and gaps

| Path | Observed behavior | Required change or compatibility boundary |
| --- | --- | --- |
| Processing settings | `LocalSettings.language` accepts only `da`/`en`, default `da`; settings row 3 is optional. `EnhancementOptions` inherits this default. | A UI-only Automatic option would fail validation. Missing language in a legacy job currently means Danish; do not change that interpretation. |
| OpenAI / compatible API | `Settings.language` defaults to an empty string. `transcribe_file` omits language when empty; catalog entries can request `languages=[value]` instead of `language=value`. | Preserve omission and the catalog-specific parameter. Never send the string `auto` as an ISO language code. Record a response language only when the provider actually supplies it. |
| Native UCloud Transcriber | `build_specification` sets the language parameter only for a nonempty configured value. An inherited template can already contain a language. Native recovery freezes the submitted language for exact output-name matching. | Automatic must be represented by the actual resolved application specification, not inferred from an empty connection field. Preserve existing templates, frozen submissions and filename matching on retries. |
| CBS Runtime transcription | `app.plan_whisperx` maps Danish/English aliases, otherwise falls back to Processing language, and rejects any other choice. `transcribe_audio` calls **faster-whisper directly**, passes the forced language and discards `TranscriptionInfo`. | This is not WhisperX's ASR wrapper. Add an explicit Automatic path and capture the direct API's language evidence before using an aligner. |
| Alignment of existing text | `recognize_windows` forces the saved language. The worker selects Danish Punkt only for `da`, otherwise English; alignment loads `snapshot(options["language"])`. | Passing `auto` unchanged would select the wrong tokenizer and an absent alignment snapshot. Resolve first; choose tokenizer and alignment model from a validated concrete supported code. |
| Passage re-transcription | Each bounded clip is transcribed with the same forced language; returned language information is discarded. | Keep bounds and exact text-preservation rules. A clip needs its own evidence or a clearly identified compatible source receipt; do not silently inherit Danish. |
| In-place timing / passage repair | One-click builders take the current Processing language. Cached local workers are keyed by Python, requested language and device; alignment cache uses language/device. | Freeze the requested policy at enqueue. Key actual alignment model reuse by resolved language/model/device, including transitions between jobs in one warm worker. |
| Automatic routing / follow-ups | `Router` selects execution once; its cloud readiness cache is currently keyed by project and diarization. Follow-ups freeze their own enhancement options. | Never detect audio language in a read-only setup/routing check. Freeze requested language in every follow-up and include any language-dependent preparation requirement in readiness-cache identity. |
| Retries / pooled runtime | The remote validator accepts only `da`/`en`. Batch identity includes normalized options; accepted request bytes, stage files and output receipts are hashed. | Changing a job's requested language after attachment would change compatibility and invalidate evidence. New language support must travel with a new hashed worker/runtime protocol; old requests stay byte-for-byte unchanged. |

Local evidence: `enhancement.py` (`LocalSettings`, `local_settings`, `enhance_window`, result
producers); `providers.py:583`; `app.py:1167`, `1277`, `1740`; `enhancement_worker.py:285`,
`452`, `487`, `654`; `local_runtime.py:38`; `routing.py` (`Router`); `features.py`;
`ucloud_native.py:297`; `native_batch.py:291`, `484`; `runtime_store.py:336`, `1601`;
`runtime_batch_remote.py:662`. Line numbers describe the inspected snapshot and may move.
`SPEC.md` V108 explicitly preserves saved `da`/`en` until detection exists; T84 still lists
true automatic handling as open. `docs/local-enhancement.md` documents the pinned Danish
and English alignment models.

## Dependency evidence

In pinned faster-whisper 1.2.1, `transcribe(language=None)` detects language for a
multilingual model and returns `TranscriptionInfo`, including language, probability and
language probabilities. `detect_language` can operate on audio independently; its
sampling parameters are explicit. The API also offers detection per segment through
`multilingual`, which the app currently does not enable. An explicitly forced language
gets probability 1 in this API: it must not be reported as measured detection confidence.
See [`WhisperModel.transcribe`, `detect_language`, `TranscriptionInfo`](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py).

WhisperX 3.8.6's ASR wrapper has its own detector that examines the initial 30 seconds
and warns about shorter input. That wrapper is not the app's current transcription
entrypoint. See [`FasterWhisperPipeline.detect_language`](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/asr.py).

WhisperX alignment needs a language-appropriate phoneme model. Its loader selects by
language or explicit model name; unsupported default languages raise an error. Some
characters/words can remain unaligned even with the correct language model. See the
[alignment loader](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/alignment.py)
and [alignment limitations](https://github.com/m-bain/whisperX/blob/v3.8.6/README.md).

UCloud documents that selecting a language forces it, while leaving the native Batch
language unset asks Whisper to recognize it. This supports omission for **new clean
specifications**; it does not prove that an inherited configured template is unset.
See [Transcriber Batch options](https://docs.cloud.sdu.dk/Apps/transcriber.html#transcriber-batch).

## Small implementation seam

Use one worker helper to resolve or reload language evidence and one versioned receipt
shape. Keep detection in the selected runtime, where audio already exists. Do not mutate
`EnhancementOptions` or reserialize accepted requests to replace `auto` with `da`/`en`.

For new requests, retain the familiar option shape:

```json
{"language": "auto"}
```

Keep `"da"` and `"en"` valid fixed choices. A receipt can use the following minimum
shape; concrete keys need acceptance with the worker/output contract before implementation:

```json
{
  "schema": 1,
  "requested": "auto",
  "effective": "da",
  "origin": "asr_detection",
  "status": "resolved",
  "scope": "recording",
  "input_sha256": "<exact decoded worker input hash>",
  "request_sha256": "<exact frozen request hash>",
  "window": {"start": 0.0, "end": 30.0},
  "detector": {"id": "<model ID>", "revision": "<pinned revision>", "version": "1.2.1"},
  "probability": 0.91
}
```

The example score is illustrative, not a recommended threshold or accuracy claim. Record
the real inspected window, including VAD/window mappings where relevant; do not always
stamp the example's 0–30 seconds. Use `probability: null` for a manual override. Preserve
an unsupported detected code as evidence rather than relabeling it Danish or English.
Allow `uncertain`/`unsupported` states with no alignment language. Treat upstream language
labels as bounded untrusted metadata. Do not add transcript bodies or private paths.

A dedicated `language.json` checkpoint is a straightforward option. If selected, include
it in the new protocol's strict input/stage/output allowlists, size/hash validation, stage
carry and durable output receipts. Require it only for new Automatic jobs; legacy
completed checkpoints must still finalize without detection or loading an inference
runtime. Existing fixed-language jobs need no new file to remain valid.

At the first inference stage, persist the decision before accepting dependent stage
checkpoints. A retry reloads the verified receipt. A changed input hash, request hash or
detector policy is a different request, not permission to overwrite old evidence. Once
a complete ASR checkpoint exists, recover language from its associated receipt rather
than invoking ASR again. The current provider result source metadata should expose a
small allowlist of requested/effective language and origin for research reports.

## Behavior by operation

1. **New ASR transcription:** use `language=None` for Automatic at the direct
   faster-whisper call and capture its `TranscriptionInfo`. Use its concrete resolved
   code for downstream recognition-window calls, tokenizer and aligner. Fixed choices
   retain current decoding behavior. Detection failure, unsupported language and no
   speech must be distinguishable; do not force an English tokenizer in any case.
2. **Existing-text alignment:** reuse source language only when a verified receipt
   actually belongs to this source/audio and scope. Otherwise run the cached pinned
   multilingual detector on permitted audio. Alignment cannot reliably align translated
   text to a different spoken language; do not present audio detection as proof of text
   language. Keep an explicit override and a clear mismatch/uncertainty outcome.
3. **Passage-only operations:** resolve within the actual bounded audio unit, retaining
   exact recording-relative bounds and permitted context. A source recording decision
   is a recorded prior, not proof that every short passage has the same language. Do not
   decode/upload a whole recording merely to detect the language of a small repair.
   Existing adjacent untimed-gap bounds and the 60-second safety limit still apply.
4. **Diarization only:** do not load an ASR/detection/alignment model merely to obtain a
   language field. Speaker detection does not require choosing a transcript language.
5. **Unsupported/uncertain alignment:** never substitute the other pinned aligner or
   invent word times. Keep existing source words/text/bounds unchanged. Full ASR should
   retain its usable transcription with an explicit partial-capability warning; alignment
   completion must not imply word coverage. Alignment-only work should report why it
   could not add timing, without claiming a successful timing result. The precise partial
   job/result status needs a regression test because today's combined producer expects
   its requested stages.

For short, silent or noisy input, model probability alone is not validated certainty.
Before enabling detection broadly, evaluate a fixed policy on Danish/English short clips,
silence, noise and counterexamples. Record the policy/version. If the evidence is
insufficient, keep the passage untimed and offer the Advanced override for a **new**
request; do not silently choose Danish. No latency claim is made for loading the coarse
detector on a small repair.

## Recording language is not mixed-language support

The first deliverable can determine one language for a recording or bounded repair.
It must say so. An interview with a Danish introduction and English discussion can yield
a misleading recording-wide choice; alternating Danish and English inside one passage
needs finer evidence. A few English names inside Danish text are another distinct case.

Do not enable a per-segment ASR flag and label the entire alignment pipeline multilingual.
That also needs language-bearing segments/windows, stable text association, per-language
aligner selection and safe recombination without guessed boundaries or timing. Design and
evaluate that separately. Until then expose the recorded scope and incomplete timing;
do not promise that one Automatic decision handles code-switching correctly.

## Compatibility and rollout

**Stage 1 — compatibility contract and fixtures.** Extend new-request validation to
Automatic and define the evidence/checkpoint contract. Preserve the legacy frozen-reader
default `da` even if the new settings default becomes Automatic. Do not add new default
fields to old normalized option dumps or change old batch identity hashes. Test exact
legacy JSON/request/identity bytes, including snapshots with no language field. A frozen
job uses its old reader/protocol; new jobs explicitly serialize their requested choice.

Keep explicit saved `da`/`en` settings unchanged. A missing settings row/field currently
also means `da`; distinguish that legacy case from a newly initialized library when
writing the new Automatic default. A small settings-version/initialization marker is
enough if the existing library initializer cannot already distinguish them. Do not run a
database schema migration merely to change a UI default, and do not backfill language
evidence into old results. Existing-library users may explicitly switch to Automatic.

**Stage 2 — full ASR and native/API omission.** Capture ASR language once, implement
supported/unsupported alignment outcomes and verify restart/checkpoint recovery. Update
`app.plan_whisperx` and feature follow-ups, then native clean-spec and provider parameter
tests. Freeze native submitted language exactly as today. Require explicit runtime
preparation for the newly hashed UCloud worker as existing policy dictates; old running
owners are not upgraded or restarted.

**Stage 3 — alignment-only and bounded repairs.** Reuse the helper/receipt, support valid
source evidence and short-clip uncertainty. Update warm-worker alignment cache identity,
UCloud stage carry, Automatic readiness caching if requirements differ by language, and
timing-realign application tests. Only after these paths pass should T84 claim Automatic
as the general default. Mixed-language per-passage support remains a separate later step.

No new schema or runtime protocol is approved by this proposal. Add an architecture
decision when implementation changes frozen language/checkpoint semantics, and update
SPEC V108, TODO T84, local-enhancement docs and Processing help together.

## Required verification

All automated tests use temporary libraries, stubbed detector/ASR responses and mocked
remote execution. Small licensed real clips are a separate opt-in language evaluation;
routine tests must not fetch weights, call a paid API or submit UCloud jobs.

| Test | End-to-end assertion |
| --- | --- |
| New/legacy settings | New library selects Automatic; saved Danish/English and a legacy missing settings row keep the chosen compatibility behavior. Advanced overrides save and apply only to newly queued jobs. |
| Frozen serialization | Old da/en and omitted-language fixtures retain identical normalized option hashes and runtime batch identities; no request bytes change on retry. |
| OpenAI parameters | Automatic omits both `language` and `languages`; explicit catalog variants send the existing correct parameter. Response evidence never invents a language. |
| Native template | New unset specification remains unset; an inherited forced language is detected and represented honestly. Filename recovery still uses the frozen submitted value, including known-language tokens for actual Automatic runs. |
| Danish/English ASR | Stub ASR detects each code; correct pinned tokenizer/aligner is selected; immutable requested `auto` stays unchanged; receipt and result source contain the effective code. |
| Override provenance | Fixed Danish/English bypasses detection. Engine probability 1 from a forced language is not stored/displayed as detection confidence. |
| Uncertain/unsupported/no speech | No Danish/English fallback, invented word times or false word-timing success. ASR text and original source passages are preserved and the outcome is explicit. |
| Alignment-only import | Missing source evidence invokes the detector once; valid exact-source evidence is reusable; translated/mismatched or stale evidence is not trusted silently. |
| Passage repair | Detection reads only the frozen permitted unit/context, output offsets remain recording-relative, unselected dictionaries are byte-equivalent, and unknown language adds no timing. |
| Warm worker | An Automatic Danish job followed by English and a fixed override cannot reuse the wrong alignment model/tokenizer; diarization-only work does not load a detector. |
| Restart and checkpoints | Interrupt after language receipt, after ASR and during alignment; resume does not redetect/retranscribe accepted stages, and complete legacy checkpoints finalize without inference. Corrupt or mismatched receipts fail closed. |
| Shared UCloud owner | Automatic members may resolve to different languages without changing owner identity or mixing per-file receipts. New validator/stage allowlists accept only the intended protocol; old owners retain frozen behavior. |
| Follow-up and timing realign | Bulk ASR → enhancement freezes the request once; a later Processing change cannot alter it. In-place timing applies only at the same result/revision/text/bounds and records effective language with the job. |
| Mixed-language counterexample | A recording-wide detection is not presented as passage-by-passage proof. Evaluation reports Danish/English and code-switching cases separately; no unsupported multilingual claim. |
| Browser/report | Automatic is the normal choice, override is Advanced, effective language/scope and incomplete timing are understandable, and research exports distinguish requested choice from observed evidence without private paths. |

No app tests or inference benchmarks were run for this design-only review. The inspected
code and the pinned upstream sources establish feasibility and the compatibility risks;
they do not establish detection quality, real Mac behavior or live UCloud acceptance.

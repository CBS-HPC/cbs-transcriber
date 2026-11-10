# Bounded local alignment-language worker — offline verification

27 October 2026. T84e / ADR 0072 / V126. This stage adds an internal standalone worker
operation, not a public repair feature or changed default. The running app/library is unchanged.

## Contract

- Explicit `operation: bounded_alignment` is local and cached-only, with one known-bound text
  unit covering its entire prepared <=60-second WAV, <=120 words/1,200 characters. Reject
  wrong request scope or larger actual WAV before decode/inference imports. Fixed jobs and
  full-ASR Auto keep their existing options and checkpoint meaning.
- Direct detection uses the pinned coarse multilingual model with verified extractor geometry
  and the first actual contiguous <=30 seconds, VAD off. Verify the complete prepared float
  mapping before slicing. No ASR text generation or implicit model download.
- Separate `alignment_language.json` captures exact prepared-input/request hashes and windows,
  model pin/engine, experimental policy and requested/report language. No speech-presence
  claim; `text_compatibility` remains `not_assessed`. A supported candidate selects a copied
  language, tokenizer/aligner/cache without changing frozen request bytes.
- A decision is durable before alignment. Retry never repeats a completed decision; interruption
  before that checkpoint may repeat detection. This single clip uses no inner alignment-window
  cache; interrupted alignment may repeat before its atomic final output. Invalid/unbound
  artifacts are refused rather than stamped with new provenance.
- Final `alignment_attempted` is digest-bound to the decision, with exact source IDs/tokens
  and validated word intervals. It is not verified text/speech agreement or complete timing.
  Pure cached validation needs no current inference runtime/pin substitution. `execute` can
  return a valid final result before imports; the CLI still performs its existing doctor check.
- Uncertain/unsupported language retains the decision and adds no timing. Curated errors expose
  neither runtime bodies nor secrets. Public existing-text/follow-up and remote admission remain
  closed even if a client supplies this marker. Processing stays da/en.

## Verification

Final affected verification: **334 distinct cases passed across two final runs**, no skips:

- **308 passed in 67.44 s**; `clip-language-worker-final-verified.xml` and `.txt`. This is 95
  new clip cases, 116 full-ASR Auto, 43 fixed worker/transcription/re-transcription/window,
  52 preparation cases and two existing real JSON-writer regressions.
- **26 passed in 11.09 s**; `clip-language-boundary-final.xml` and `.txt`. This is nine new
  clip error/admission cases plus six existing public and 11 legacy/remote boundary cases.
  It includes the marker-on-otherwise-valid-full-ASR refusal.
- Independent read-only review gave **GO** after 85 overlapping targeted cases passed in
  25.09 s (19 deselected), covering scope, partial components, growth, stale source,
  checkpoint retry, tamper and cached completion. Do not add this overlap to the total.

Reproduce the affected worker selection:

```powershell
uv run pytest tests/test_worker_clip_language.py tests/test_worker_auto_language.py tests/test_worker_language_evidence.py tests/test_whisperx_transcription.py tests/test_retranscription.py tests/test_enhancement_windows.py tests/test_auto_language_preparation.py tests/test_enhancement_cloud.py::test_v34_json_checkpoint_unchanged_bytes_skip_replacement_but_changes_remain_atomic tests/test_enhancement_cloud.py::test_v71_changed_json_preserves_old_file_until_windows_reader_releases_it -q
```

Boundary selection:

```powershell
uv run pytest tests/test_clip_language_boundary.py tests/test_auto_language_admission.py tests/test_auto_language_boundary.py -q
```

The root boundary selection initially proved the three new curated messages absent (three
expected test-first failures, six passing gate checks); adding the new fixed messages produced
26 passes across clip, existing public admission and legacy/remote boundaries in 9.41 seconds.
An additional marker-on-valid-full-ASR assertion is included in the final affected selection.
Evidence: ignored `test-results/clip-language-boundary-red.xml`, `clip-language-boundary.xml`
and `.txt`. These tests use temporary libraries and mock every runtime/provider boundary.

Independent review reproduced two new-boundary gaps (B165/B166). The shared ASR interval helper
expects later host cleaning and skipped a partial word with an unknown start/score but an end
of 999 seconds. The clip-only validator must check every present component. A synthetic WAV
growth hook also showed that size checked only before input binding could reach optional
imports after growing beyond the cap. The focused red selection had eight failures (seven
malformed partial maps plus growth); the fixes remain local to the new clip boundary.
The focused green selection passed 14 cases in 7.48 s; `clip-language-interval-green.*`.

Two initial affected runs each encountered Windows `WinError 5` replacing volatile
`progress.pending` with `progress.json` in different positive synthetic cases. The first
reported 292 passes/one failure; the next 305 passes/one failure after new validation cases.
Retained files had Archive attributes and owner FullControl, with no read-only/deny ACL.
Controlled tight writer loops without an intentional reader failed after 54 of 1,000 writes
in the workspace and 89 of 1,000 in Temp; 40 spaced writes passed. A real Windows reader
denying replacement reproduces the same error while preserving target/pending bytes; identical
content remains a no-op and changed replacement works after release. The actual original
lock owner is **unproven**. Ignored evidence: `clip-json-reader-probe.json` and
`clip-json-writer-b0ayix71/report.json`.

Following ADR 0034, no production writer retry, deletion or timing change was introduced.
The new semantic fixture captures only volatile progress events; request, decision, alignment,
error and other writes retain real file I/O. Existing real Windows reader-lock regressions
remain separate. This tests the new language/repair contract, not physical progress I/O under
real workload. B167 records the fixture/verification limitation rather than declaring an
unidentified external lock fixed.

Whole-repository Ruff lint/format, changed Python compilation and Git whitespace checks pass.
No frontend/dependency changes; no browser or Node/Prettier rerun was needed for this stage.

## Limits and next integration

Synthetic audio and stub detector/aligner only. No quality, latency or memory benchmark; no
model weights, inference, paid calls, private transcript access, live UCloud job/upload, real
library migration, backend restart, merge or push. No UI changed, so no new browser acceptance
is claimed for this hidden operation.

Public integration still needs frozen original source/version/clip mapping, bounded text/speech
compatibility assessment, host validation/settlement, repair application and provenance, plus
remote clip-bundle/carry contracts before UCloud use. A recording-wide ASR decision cannot prove
the language of a mixed-language clip. No broad Automatic default before complete operation
coverage and licensed policy evaluation. Mac/UCloud/NVivo acceptance and the final audit remain
separate from this contract verification.

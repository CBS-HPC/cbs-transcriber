# Configured ASR language evidence — offline verification

27 October 2026. T84a / ADR 0067 / V119–V120. This is the provenance foundation for
automatic language handling; it does not enable Automatic or change saved/frozen defaults.

## Delivered

New fixed Danish/English WhisperX full transcription and passage re-transcription stages
retain configured decoder language, a null detection probability, the requested model and
captured pin, observed/unknown faster-whisper version, exact WAV/request hashes and actual
sample-rounded windows in the prepared-audio timebase. Existing stage filenames and output
allowlists stay unchanged. The parent validates present evidence before attaching it to a
result and its version. Research reports use a bounded allowlist and describe configured
decoding explicitly; arbitrary passage IDs and private paths are excluded.

Legacy stages without receipts remain byte-identical, usable without new input/runtime
requirements and explicitly unknown. Historical receipt validation uses captured identities,
not today's inference package or model registry. A frozen batch pin, when present, must match.
No reviewed text, timing, speaker, result selection or frozen request is rewritten.

Independent review reproduced a stale decoded-audio cache when different WAV samples had
the same frame count. Cache readiness now binds source and float-PCM hashes plus frame count,
with the marker written last. New ASR also verifies the actual mapped samples, including an
old Unix mapping retained after path replacement. Changed/corrupt/partial caches are rebuilt
or refused before inference. Completed legacy ASR is never regenerated to add provenance.

## Verification

- Final worker-focused run: **43 passed** in 9.72 s, covering fixed da/en requests, padded
  passage windows, strict invalid-input rejection, legacy checkpoints, hash-bound caches,
  source drift, float-cache corruption and actual mapped-buffer identity.
  Evidence: ignored `test-results/language-worker-verified.xml` and `.txt`.
- Source/history/report focused run before the final integration case: **83 passed** in
  26.76 s. These overlap the worker run and must not be added together.
- A final contract case uses the real worker and real decoded synthetic samples with only
  the ASR model stubbed, then follows its receipt through result persistence, immutable
  version metadata and the metadata-only research ZIP. It passes without model downloads.
- Independent read-only review: **GO**, including a separate red reproduction and green
  verification of the stale-cache defect. The reviewer reported no remaining stage blockers.

- Final combined affected run: **172 passed, 1 skipped** in **89.70 s**. This includes all
  120 language/worker/persistence/history/report cases above, 49 remote-controller cases and
  three coordinator cases proving independent results/cleanup, failed-alignment retries
  carrying verified ASR, and cached finalization without readiness/private upload.
  The remote symlink-input case is skipped because this Windows process cannot create
  symlinks. Evidence: ignored `test-results/language-integrated.xml` and `.txt`.
- All **eight staged Python files** compile and pass Ruff lint/format, reading the actual
  staged blobs so the other chat's shared-file edits are excluded. No frontend formatting
  is involved.

## Limits and concurrent work

Tests use synthetic PCM WAVs, stub ASR, temporary libraries and mocked UCloud transport.
No inference quality, detector confidence, GPU performance or live deployment claim is made.
No weights, private transcript bodies, paid calls or real UCloud jobs were used. No backend
restart or real-library migration occurred. This stage has no frontend changes, so existing
browser evidence was not repeated.

Automatic detection, supported/uncertain/unsupported decisions, alignment-only language
resolution and default initialization remain open under the T84 proposal. Native Transcriber,
OpenAI language omission and existing queued request semantics are unchanged. Live UCloud,
Mac and NVivo acceptance remain separate.

Another chat is editing layout/merge/research files in the same checkout. Its changes are
preserved and excluded from this stage's commit; whole-checkout lint failures in unfinished
merge code do not constitute verification of that separate workload. The whole-checkout
attempt found three lint issues in `result_merges.py` / `transcript_merge.py` and formatting
in merge-only additions to `history.py` / `result_merges.py`; these files are actively owned
by that chat and were not changed to silence its in-progress checks.

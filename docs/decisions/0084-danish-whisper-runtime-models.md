# 0084 — Danish Whisper models on CBS Runtime

Accepted 28 October 2026 after primary-source and independent design review. Implement
Edda v0.2 turbo, Edda v0.2-large and Røst v3 as optional fixed-Danish, full-ASR CBS Runtime
profiles. Keep native Transcriber, existing Whisper choices, saved profiles and frozen jobs
unchanged. Hviske 5.3 and 6 require distinct engines and are explicitly deferred by the user.

Explicit preparation fetches only a finite pinned original-source inventory and verifies its
bytes before converting its local safetensors snapshot with the already locked CTranslate2
and Transformers stack. Never execute publisher Python or enable trust_remote_code. Preserve
the 128-mel preprocessor and exact tokenizer. Source snapshots remain immutable; derived CT2
files have their own source/recipe-specific cache, strict hashes and marker-last publication.
Inference is cached-only. No startup/catalog selection downloads weights or launches jobs.

A frozen helper/catalog companion travels with the worker and is verified from exact bytes,
including standalone isolated execution. New transcription checkpoints include asr_conversion
bound to source pin, recipe, actual derived artifacts, request and prepared audio. Pure
checkpoint/history/report validation needs no current cache, weights or runtime. Source pins
and converted-artifact identities remain distinct. Old choices keep their receipt rules.

Profiles force Danish and reject English, Automatic and passage re-transcription for this
first slice. Existing alignment and optional diarization supply timing and speaker labels.
Model implementation does not establish accuracy, resource measurements or CBS licence
clearance. Preserve attribution and pinned licence evidence; actual model acceptance remains
separate from mocked tests. No weights or allocations are required during implementation.

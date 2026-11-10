# 0006 — Public reference fixtures and explicit provider evaluation

Accepted: 20 October 2026.

Use five Danish FLEURS test recordings with exact reference text, a pinned source revision,
hashes, attribution, and CC BY 4.0 notice. Commit this small public subset so CI needs no
dataset service or network. Keep private recordings outside Git.

Score text with a standard-library Levenshtein implementation, documented Unicode
normalization, and corpus-weighted WER/CER. Reject incomplete coverage. Store raw text and
counts so scores can be inspected. A small read-speech subset provides an initial regression
reference; it does not establish interview, speaker-label, or timing quality. Existing
synthetic and browser tests continue to verify those app behaviors.

The repository evaluation script supports offline saved predictions and an explicit
`--allow-api-calls` run through the app's normal upload/conversion/queue APIs. It creates a
separate project, uses current provider settings without changing them, and leaves recordings
for review. It scores original model text, exports NVivo text, and writes ignored local reports.
Failed or interrupted runs retain project/job IDs and receive no accuracy report. They never
automatically retry uncertain requests. Unit/integration tests use a mocked provider and
temporary storage; no paid service is part of CI.

This avoids an additional datasets/ASR/metrics dependency. A larger, representative interview
benchmark and numerical quality gates can follow once real baseline runs are available.

Sources and scoring details: [fixture documentation](../../tests/fixtures/fleurs_da/README.md).

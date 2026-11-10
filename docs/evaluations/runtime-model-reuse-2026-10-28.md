# Offline Runtime model reuse experiment — 28 October 2026

Historical reference experiment. The subsequent production cohort implementation and its
separate verification are recorded in [ADR 0094](../decisions/0094-finite-resident-runtime-cohorts.md).
The measurements below remain the original synthetic reference measurements.

T85e remains open. Shared allocation pooling works, but resident GPU model reuse
is not implemented or accepted by this experiment. The verified serving source
`c3c94e6` starts a separate `enhancement_worker.py` process for each batch task in
`runtime_batch_remote.start_worker`. Its batch path does not enable the local
passage worker's alignment cache. Model files cached on the remote drive and model
objects retained in GPU memory are separate properties.

## Executable, isolated experiment

`scripts/evaluate_runtime_model_reuse.py` runs the actual worker `execute`, fixed
ASR, alignment mapping and stage JSON writers with synthetic model adapters. It
creates only temporary synthetic three-second WAV files. Torch, WhisperX,
faster-whisper, model snapshots, NLTK and speakers are replaced by local adapters;
no weights, GPU, credential, provider, real library or production worker runs.

The baseline runs a complete pipeline per file, with its normal alignment cache
disabled. The reference retains one fake model identity at a time and processes a
finite cohort by ASR, alignment, then speakers. It evicts before changing identity
and at cohort completion. It reuses existing completed stage files between these
passes, preserving byte-identical final stage JSON in this experiment.

Run with the existing environment, without synchronization:

```powershell
uv run --no-sync python scripts/evaluate_runtime_model_reuse.py --files 4
uv run --no-sync python scripts/evaluate_runtime_model_reuse.py --files 4 --cohorts 2 2
```

The measured development worker bytes had SHA-256
`a30b1761e60563d994aa77ade319303f78065f56a609538fa0e8269bedb526cb`.
The script emits its source hash on each run because parallel development can
change that source. It does not assert that development bytes are running live.

| Four files | Baseline model constructions, ASR / align / speakers | Reference constructions | Reference cache hits | Identical stage bytes |
| --- | --- | --- | ---: | --- |
| One cohort of four | 4 / 4 / 4 | 1 / 1 / 1 | 9 | yes |
| Two finite cohorts of two | 4 / 4 / 4 | 2 / 2 / 2 | 6 | yes |

Measured Python/JSON harness wall times were 1.210 versus 1.401 seconds for one
cohort, and 1.352 versus 1.692 seconds for two. The reference was slower here:
fake model construction has no real loading cost and the reference runs extra
stage/checkpoint passes. These numbers measure harness overhead only. They are
not GPU throughput, model load time, memory use, quality or a predicted speedup.

## Progress and production limits

The baseline captures the actual worker's loading, transcribing, aligning,
speakers and complete progress writes. The ASR fraction is weighted pipeline
progress, not a percent of the recording transcribed or a saved-result count.
For the synthetic segment ending halfway through a three-second input, the ASR
write is 0.175 (`0.05 + 0.25 * 0.5`).

The reference omits its progress records deliberately. Calling existing `execute`
with reduced stage flags reports `complete` for each reduced request, which cannot
serve as a truthful full-task completion protocol. A production resident worker
needs stage acknowledgments distinct from task outcomes, exact model identities
and pinned revisions, healthy child ownership, bounded cohort/late-admission rules,
and existing immutable accepted/outcome, Stop, shutdown and partial-carry behavior.
The reference provides none of that production authority and is not imported by
application code.

The measured constructor counts justify investigating a single-model stage-cohort
seam. They do not establish memory safety or a useful real speedup on the bounded
MIG. Leave defaults and live execution unchanged. A later authorized public-fixture
acceptance run must measure cold/hot stage load times, inference time, peak GPU/host
memory, first-result latency, output integrity and bounded shutdown/cost separately.

## Verification

`tests/test_runtime_model_reuse_evaluation.py`: **8 passed in 10.42 seconds**.
The cases check real worker constructor requests with fake models, exact final
stage hashes, finite late cohorts, single-slot failure eviction and input bounds.
The initial test collection failed because scripts is not an installed package;
the test now loads the standalone script through standard-library `runpy`.
No application behavior or invariant changed to resolve that harness import.

Scoped Ruff check and format check passed for the script and new test files.
No broad tests or live allocations were used as evidence for resident model reuse.

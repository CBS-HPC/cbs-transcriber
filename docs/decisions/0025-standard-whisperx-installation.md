# 0025 — Standard WhisperX installation and runtime preflight

Accepted: 21 October 2026. The user explicitly requested WhisperX as a standard dependency.
Supersedes ADR 0016's separate-environment default and that part of ADR 0023.

Install WhisperX 3.8.6, faster-whisper 1.2.1 and compatible Torch 2.8 CPU packages through the
normal pyproject/uv.lock. Use the official explicit CPU wheel index for Torch-related packages;
generic dependencies continue to use PyPI. Match the project's Python range to 3.13 (<3.14),
within WhisperX's supported range. Installation includes code and libraries, never model weights.

Default local execution uses the app interpreter. Advanced users may select another prepared
environment for CUDA. Native UCloud/SSH runtimes still need independent preparation; local
installation does not deploy to them. Model downloads, gated terms and HF tokens remain explicit.

The missing-package report also exposed a validation gap: batch ASR preflight checked runtime,
but individual enhancement accepted an unprepared interpreter. Reuse one readiness guard across
single-result creation, batch follow-ups, local retry and worker execution. Run interpreter probes
outside the shared queue lock; revalidate source revision/status before inserting a new job.
Workers with missing stages check before conversion. Fully cached stages need no inference runtime.
Retries retain their original interpreter/options; choosing a different interpreter requires a
new enhancement, preserving source/result/checkpoint identity.

CPU readiness inspects package metadata without importing Torch for GPU discovery. Explicit GPU
checks retain PyTorch-based CUDA detection and a longer budget for cold DLL imports. No inference,
audio transfer, model downloads or automatic device selection occurs in either probe.

Sources:
- https://github.com/m-bain/whisperX/blob/v3.8.6/pyproject.toml
- https://docs.astral.sh/uv/guides/integration/pytorch/

Verification uses missing-runtime regressions, claim-lock/revision checks, cached-stage retry,
normal locked installation, real offline imports and the default interpreter doctor. Real
alignment/diarization quality remains separate from installation evidence.

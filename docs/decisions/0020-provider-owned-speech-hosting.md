# 0020 — Provider-owned speech hosting

Accepted 20 October 2026.

## Context
CBS Transcriber configures several temporary UCloud vLLM speech endpoints. Its
catalog must not imply that a selected model is deployed, sized or tested. The
hosting agent owns allocation, runtime, endpoint and credential provisioning.

## Decision
Implement speech hosting in ucloud-vllm-agent's separate feature/transcription-models
branch (offline increment ab49949), preserving its published main and API dependency.
Use the existing owned lifecycle, an isolated audio environment, the HF served model ID,
actual multipart/auth speech checks and explicit request-bound manual capacity review.
Do not reuse dense-chat KV estimates or call smoke verification a load benchmark.
Keep candidate profiles/runtime/allocation evidence unset until real validation.

## Consequences
The Transcriber catalog and endpoint configuration work offline. This app does not
import an unreleased provider branch or duplicate hosting code. Real speech allocation,
CUDA/audio compatibility, sizing, quality, provider release and downstream adoption
remain separate acceptance work. Native UCloud Transcriber and optional SSH enhancement
retain their independent configurations and job ownership.

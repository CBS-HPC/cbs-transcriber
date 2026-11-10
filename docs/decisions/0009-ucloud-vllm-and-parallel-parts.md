# 0009 — UCloud vLLM connections and bounded parallel parts

Accepted: 20 October 2026. Supersedes the single-worker choice in 0004; extends 0007 and 0008.

UCloud vLLM is an independent provider type and settings tab. UCloud Transcriber remains
the separate import/future job integration. Existing configurations infer OpenAI or Custom
when they have no type. The type, response capability and concurrency freeze in each job.
No database schema change is needed because provider/job settings are already JSON.

Use the existing OpenAI-compatible client with the deployed HTTPS /v1 URL and served alias.
The alias can differ from the Hugging Face repository. Model discovery reads only /models,
shows advertised aliases and cannot establish ASR capability. Segment mode uses verbose_json
without word-granularity parameters. Speaker mode requires actual diarized_json support and
does not send OpenAI's chunking_strategy to vLLM. Never fabricate missing timestamps.
The inspected nginx template limits requests to 16m; default UCloud uploads use 14 MB.

Serving credentials, UCloud control tokens and HF tokens have separate credential-store
scopes. Setup tokens never enter provider settings JSON, queued snapshots or inference
requests. Their presence flags are public; their values are not. A setup configuration can
await a serving URL. It cannot queue transcription until its endpoint/key are configured.
Nothing reads another project's .env or allocates remote resources.

The inspected hosting agent lives under CBS Inference and currently accepts task=chat/pii.
It owns setup, templates, Link reservation, job identity, journals, sizing and lifecycle.
Automatic speech deployment/URL provisioning requires that project's reviewed ASR workflow;
adding a URL or saving tokens here must not pretend that workflow exists. No deploy command
or untested chat profile is repurposed for audio. The app connects an already deployed speech
service; HF models/credentials can be recorded for subsequent hosting work.

Use two recording workers and two separate media workers. Independent files can prepare
and transcribe concurrently. A shared claim lock prevents duplicate claims; cancellation
targets the worker owning that job. All workers retain the same library lease during shutdown.
Each recording uses a small thread pool processing at most 1–4 parts, for up to twice that
many requests across two files. Provider-wide UCloud capacity limits remain later work.
Old settings retain concurrency 1; new OpenAI configurations suggest 2 and UCloud suggests 1.
Only each recording's coordinator updates its chunk checkpoints. Every successful part has
its explicit plan index and atomic raw response; sparse checkpoints survive out-of-order
completion. Stop scheduling on
the first observed failure or cancellation, settle accepted requests, retain their successes,
and require explicit retry. Rate limits never trigger an automatic retransmission. Results
are combined in plan order with original offsets and part-scoped speaker labels.

Graceful shutdown waits for accepted requests, retaining the library lock until checkpointing
finishes. This can take as long as an in-flight provider call; a forced process exit still leaves
uncertain work interrupted for explicit retry. No scheduling or concurrency change alters an
already frozen job. There is no automatic provider switch or global speaker matching.

Review evidence: prefix-only job.chunks could lose a later successful part on retry; V21
requires indexes. The old three-second shutdown join could release the lease while requests
still checkpointed; shutdown now settles the worker. The model list cannot prove audio
support. V20 and mocked provider/API/browser tests cover credential separation and aliases.
Gate: GO for connection/configuration and bounded local parallelism; no automatic ASR hosting.

Source: [vLLM speech API](https://docs.vllm.ai/en/stable/serving/online_serving/speech_to_text/),
hosting-agent config.py, WORKFLOW_CONTRACT.md, docs/deterministic-hosting.md and nginx template.

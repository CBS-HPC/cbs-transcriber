# 0053 — WhisperX transcription as UCloud models on the CBS runtime

Accepted 23 October 2026.

Researchers wanted a full WhisperX transcription that runs primarily on UCloud. It should sit
next to the UCloud Transcriber app's own Whisper models, use exactly pinned model revisions, and
keep the model's confidence values. The user decided against a separate button or provider
family: WhisperX is chosen like any other model, under **Provider: UCloud**.

## Decision

- **Catalog models.** The UCloud catalog gains models marked `"engine": "whisperx"`:
  `whisperx/large-v3` and `whisperx/large-v3-turbo`, named "UCloud WhisperX … (CBS runtime)",
  with capability `words`. The catalog validates the engine and the pinned `whisper_model`.
  Profile backfill adds them to every complete UCloud connection, so they appear in the Model
  list and in Available models, with the usual enabled flags and Protected policy.
- **Queueing.** When such a profile is selected, the Transcribe request queues an enhancement-kind
  job with `transcription: true` on the UCloud lane (ADR 0049). The job freezes that exact profile
  (`freeze_profile`: approval, setup and compute checks) and the API-only runtime identity
  (ADR 0046). Requested speaker labels run in the same job; no follow-up is created. The language
  must be Danish or English, because word alignment needs a pinned wav2vec2 model. An empty
  language uses the library's local default.
- **Engine.** The worker's new transcription stage runs faster-whisper with its bundled VAD
  (beam 5, no conditioning on previous text) and checkpoints `transcription.json`. Segments
  without an audio interval join a neighbour's text; no time is invented for them. Its passages
  then go through the existing alignment and speaker stages and `merge_output`. Passage-level
  avg_logprob, no_speech_prob and compression_ratio, and word alignment scores, become review
  hints (ADR 0048). The result is an ordinary `provider_result` with provider type `whisperx`,
  the engine version and the pinned model revisions.
- **Pinned weights.** Pinned public weights (MIT):
  - `Systran/faster-whisper-large-v3@edaa852…`
  - `mobiuslabsgmbh/faster-whisper-large-v3-turbo@0a363e9…`
  - also `medium`, and `small` (the existing coarse model) for the engine
- **Preparation is explicit and asked for.** Before queueing, a read-only check of the UCloud
  drive confirms that the runtime stamp and this job's model snapshot folders exist. If not,
  the request is refused with code `prepare_runtime`, and the browser asks for confirmation:
  a one-time download of pinned public packages and weights into the project drive. On yes,
  the request is repeated with `prepare_runtime`.
- **Fetching models.** The supervisor receives the job's public model keys (`__MODELS__`,
  validated). A full preparation prefetches them; on an existing runtime, an explicit
  preparation runs only `fetch_models`, under the same lock. The gated speaker model is still
  copied from the local cache, never downloaded remotely. Readiness inside the job still
  refuses to upload any audio while a required model is missing.
- **The app's own model field.** The Transcriber app's model parameter is not used by the
  supervisor. Script jobs for WhisperX profiles fill it with the catalog's default native model.

The worker can also run this stage locally (`execution: local`), but the interface offers
WhisperX transcription only as a UCloud model.

## Consequences

Each recording is one UCloud allocation on the connection's fixed product (B200 today; see
T73 on MIG). WhisperX results can be compared, exported and reviewed like any provider
result. The first run on a drive pays a one-time download of roughly 5 GB. Live acceptance
with the user's UCloud project is still pending.

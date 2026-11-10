# 0046 — API-only UCloud enhancement and a reusable runtime

Accepted 22 October 2026. Supersedes ADR 0018 for new enhancements; extends 0016, 0045.

The user asked that UCloud word timing/speaker enhancement need only the Provider Settings UCloud
connection, use the API instead of SSH, and reuse a prepared Python environment rather than
reinstalling it per run.

## Execution

New UCloud enhancements freeze `ucloud_api`: the chosen native UCloud connection profile (the
default if eligible, otherwise the first complete B200 connection approved for every affected
project), its exact settings, the runtime ID and an explicit `prepare_runtime` flag. Credentials
remain in the OS store. Device is CUDA. Jobs frozen earlier with `cloud_settings` keep the SSH
runner for retry, Stop and reconciliation; no new SSH configuration is offered.

The job reuses the connection's Transcriber 1.12 Batch application with interactive mode forced
off, no transcription input, and its initialization script input set to a generated per-run
`supervisor.sh`. The owned run folder `cbs-enhancement-<job-id>` and the runtime folder are mounted
as folder resources at `/work/<basename>`. The script contains no credentials or user-supplied
strings; it unsets HF tokens and runs offline.

Handshake, entirely through files read/written with the UCloud file API:

1. The app journals folder ownership, uploads the supervisor/worker files, journals submission
   intent and submits once. Uncertain submissions are never repeated.
2. The supervisor verifies the runtime stamp, writes `doctor.json` and `cache.json`, then
   `supervisor.json` phase `ready`.
3. Only then does the app re-check Protected approval and upload `request.json` and `audio.wav`,
   followed by `manifest.sha256`. Missing models, runtime or GPU stop the owned job before any
   private upload.
4. The supervisor verifies checksums and runs the existing source-preserving worker through
   `enhancement_remote.py run`; the app polls bounded status files and downloads stage outputs.
5. Outputs are validated with the same merge used for the final result, the job is stopped if
   still active, and the run folder (it holds audio) moves to UCloud Trash (ADR 0045).

Retry reconnects to the recorded job; files on the drive survive the job's end. Supervisor or
worker failures stop the owned job with curated messages.

## Reusable runtime

`catalogs/ucloud_whisperx_runtime.txt` is a hash-locked requirements file for Linux x86_64,
CPython 3.12 and CUDA 12.8: WhisperX 3.8.6 and faster-whisper 1.2.1 constrained to the app lock's
versions (torch 2.8.0+cu128). Its hash names the folder
`<storage>/cbs-runtime/whisperx-3.8.6-cu128-<hash12>`, so a new recipe never mixes with an old one.

Preparation is explicit: only a job started with "Prepare the UCloud runtime" mounts the folder
writable. It downloads a checksum-verified uv 0.9.29, installs a uv-managed CPython 3.12 into the
folder (independent of the container image), installs the lock with `--require-hashes`, prefetches
the pinned public models and NLTK `punkt_tab`, then writes `stamp.json`. A directory lock rejects
concurrent preparation. Normal runs mount the runtime read-only.

The gated pyannote speaker model is never downloaded remotely. When speaker labels are requested
and the runtime lacks it, the app uploads the pinned snapshot from this computer's Hugging Face
cache; the HF token never leaves the computer.

## Limits

Not live-verified: the initialization script's ordering relative to the Transcriber entrypoint,
GPU visibility to the script, network access during preparation, flock on the drive and the
image's glibc compatibility. The first live preparation job is the acceptance test; failures
surface as curated messages and `prepare.log` in the run folder.

Sources: [Transcriber parameters](https://docs.cloud.sdu.dk/Apps/transcriber.html#app-parameters),
[mount mapping](https://github.com/SDU-eScience/UCloud/blob/c216a062878e88c6b07a416bd754a1e899d85b5d/provider-integration/im2/pkg/integrations/k8s/containers/mounts.go),
[uv 0.9.29](https://github.com/astral-sh/uv/releases/tag/0.9.29),
[pyannote offline use](https://huggingface.co/pyannote/speaker-diarization-community-1#offline-use).

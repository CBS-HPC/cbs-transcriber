# UCloud transcript enhancement

**Settings → Local Processing** controls where word timing and speaker detection run.
Automatic chooses this computer or the configured UCloud connection. Whole-result enhancement
creates an independent result and keeps the reviewed one; passage timing tags use a separate
in-place repair workflow. Single/small repairs stay local. Large eligible selections can use
the prepared UCloud runtime under the saved processing rules. New CBS Runtime work uses a verified funded
B200 MIG slice, independently of the native Transcriber execution path.
CBS Runtime models are not native Transcriber models (ADR 0062).

## Requirements

- A UCloud connection under **Settings → UCloud** (token, project, storage and the
  Transcriber 1.12 Batch application, found by **Connect UCloud**). There is no separate
  enhancement token, template or SSH key.
- Protected projects need that connection to be allowed for Protected projects.
- No SSH is used. The app talks to the job only through the UCloud API and files in the job's
  own run folder.

## The reusable runtime

New/unset UCloud preferences enable **Prepare UCloud models automatically when needed**;
saved opt-outs remain off. An explicitly selected UCloud request can freeze that standing
permission, while Automatic placement still requires an already-prepared runtime. Opening
Settings and saving preferences start no preparation or inference. A manual **Check UCloud again**
can start base runtime preparation when saved preparation permission is enabled; passive checks do not.
With permission off, the up-front preparation question covers the request before acceptance
when the bounded readiness check can identify what is missing. If readiness cannot be determined,
Activity can offer **Prepare and continue…** for its exact frozen recording/selection.
That confirmation creates an idempotent successor request; the earlier request stays in history.
Generic Retry does not grant consent. Preparation installs, once per app runtime
version, into `<storage>/cbs-runtime/whisperx-3.8.6-cu128-…`:

- uv 0.9.29 (checksum-verified) and a uv-managed CPython 3.12,
- a virtual environment with the hash-locked packages in
  `src/cbs_transcriber/catalogs/ucloud_whisperx_runtime.txt` (torch 2.8.0 with CUDA 12.8),
- the pinned public alignment/recognition models and NLTK `punkt_tab`.

This takes 20+ minutes and several GB of project storage. Later jobs mount the runtime read-only
and run offline. **Check UCloud runtime** reads your storage to report whether it is prepared;
it starts no job. A new app runtime recipe uses a new folder; you can delete old ones in UCloud.
Fixed-Danish Edda/Røst profiles have separate pinned source and converted CT2 caches; see
[the model catalog](provider-models.md). Selecting them does not download models or allocate GPUs.

Speaker labels use the gated pyannote Community-1 model. It is not downloaded on UCloud. If the
runtime lacks it, the app copies the pinned snapshot from this computer's Hugging Face cache, so
use **Settings → Hugging Face → Check token** to validate access and prepare the pinned
local files. No local speaker inference is needed. Your Hugging Face token never leaves this
computer, and speaker files are copied and verified before any GPU allocation is requested.

## What happens during a run

1. The app verifies the exact funded MIG product read-only before enqueueing. It creates the
   run folder `cbs-enhancement-<job>` and uploads a generated supervisor
   script and the worker. It records the submission before sending it and never resubmits an
   uncertain one.
2. A Terminal `batchScript` runs the supervisor, rather than the Transcriber application's
   entrypoint. It checks the runtime, GPU and cached models and reports **ready**. New MIG jobs
   require at least 8 GiB on the first CUDA device; host RAM is not used as GPU evidence.
   Missing pieces stop
   the job before any recording is uploaded.
3. The app uploads the audio and frozen text with checksums. The job verifies them and runs
   the same worker as local enhancement.
4. The app downloads and validates the outputs, stops the job if it is still running, saves the
   new result, and moves the run folder (which holds the audio) to UCloud Trash. Empty Trash in
   UCloud to free the space.

Retry reconnects to the same UCloud job. Stop in Activity terminates only this run's job.
Shutting down the app leaves the job running for a later retry.

## Selected passage repairs

**Repair all** uses selected clip workload, rather than the full recording length, when choosing
local/UCloud processing. The first cloud route needs an already-ready original-source receipt,
an approved prepared connection and its frozen MIG product. Older/uninspected recordings use
the explicit local/Stop fallback; this action does not start a full-file inspection or prepare
models implicitly. No more than 200 clip units enter one request.

Only selected bounded audio and text are uploaded. Completed clip outputs survive Stop/failure;
strictly verified partial timing can still be applied to unchanged targets. A newer word map or
wording is retained, and a repeated Apply response cannot create another timing version. Retry
uses the same recorded allocation and does not repeat terminal/uncertain inference. Start a new
request for unresolved clips when the earlier allocation cannot continue.

[Mocked integration evidence and live acceptance limits](verification-2026-10-29-selected-repairs.md).

## Older SSH enhancements

Jobs created by earlier versions with an SSH template keep their recorded settings for retry,
Stop and reconciliation. New SSH enhancements are no longer offered.

## Acceptance still needed

Earlier live runs exposed preparation and entrypoint problems, recorded in SPEC's bug history;
the current Terminal protocol and preparation checks have mocked regression coverage. The new
MIG selection was verified against read-only metadata, without starting an allocation. A live
MIG run must still verify performance, GPU visibility, preparation, drive locking, reconnect,
partial failures and cleanup. Automated tests never start a real allocation. Dynamic shared
runtime batches, accepting additional files and concurrent allocations remain T85.

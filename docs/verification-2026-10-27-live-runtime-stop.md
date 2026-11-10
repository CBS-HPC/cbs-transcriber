# Live CBS Runtime interruption — 27 October 2026

Read-only diagnosis of the batch submitted after backend activation. The serving source
was the clean verified commit `c3c94e6bfd4f9baf87e0435d502e450df314d025`.
No processing, Retry, Stop, cleanup or restart was initiated by this investigation.

## Recorded sequence

- Five selected files formed one CBS Runtime batch and one owned remote allocation.
  Its frozen product was `gpu-nvidia-b200-1-mig.1g`; options were Danish, Whisper large-v3,
  transcription, alignment and speaker detection.
- The files were queued at 21:31:43 UTC. Submission intent was recorded at 21:32:44 UTC;
  the recorded allocation start was approximately 21:32:53 UTC.
- Two task inputs were published and accepted. The controller permits one GPU inference
  child, so accepting a second task does not mean simultaneous GPU processing.
- A durable Stop intent was recorded at 21:36:36 UTC. Three unpublished members were
  cancelled immediately. The published tasks were collected as interrupted/stopped;
  collection finished by 21:37:49 UTC.
- Bounded reads of the exact owned controller/task metadata showed controller phase
  `stopped`, zero completed tasks and five non-complete terminal tasks. The first task's
  last progress was `transcribing`, fraction `0.1800946543`. Its `error.json` was absent.
  The second accepted task had neither progress nor error metadata.
- Both published task receipts say `outcome=interrupted`, `error_code=stopped`,
  `worker_gone=true`, with no completed stage files. No member saved a result or final
  output receipt. Private inputs and retained evidence were left untouched; cleanup is pending.

The allocation's platform state is `SUCCESS`. This describes the controller process exit;
it does not mean transcription succeeded. The local owner and all five members are
`cancelled`, and there are no completed outputs for this batch.

The first recording is 4,544.96 seconds long (about 75.75 minutes). The progress fraction
is weighted child-pipeline progress: during transcription the worker writes
`0.05 + 0.25 * min(1, last_segment_end / duration)`. The observed value corresponds to
the latest decoded segment endpoint reaching approximately 52.04% of the recording
(about 39.42 minutes). It is not whole-batch completion or a throughput/ETA measurement.
The ASR checkpoint is written after the segment generator completes; alignment and speaker
processing follow. The absence of a completed file while this stage was running is therefore
consistent with the recorded mid-ASR interruption.

## Origin and limits

In this commit, `runtime_store.stop` is the only code setting `stop_intent=true`. Its
entry paths are explicit batch/member cancellation and cancellation of native-parent
follow-ups. This batch has no native-parent follow-up links. Automatic coordinator failure
does not create Stop intent; it can reuse cancellation only when that intent already exists.

`recovery_epoch` is set by explicit Retry/Reconnect and consumed by a coordinator claim.
The retained marker proves a reconnect cycle, but lacks a historical timestamp/state log:
it cannot establish an earlier coordinator error or its cause. HTTP access logs are disabled,
so the investigation cannot attribute the cancellation request to a particular user action.

An independent read-only UI audit found no automatic Stop/Retry path. A mocked harness
executing the actual delegated handler confirmed separate cancel/retry/cleanup/reconcile/
discard endpoints; unrelated clicks did not cancel. Four actual polling iterations issued
only job/activity GETs. The Activity Stop button is outside forms, the keyboard handler
ignores controls, and unload only guards drafts. This narrows the available paths without
attributing the recorded request to a person or client.

The evidence establishes that shared GPU transcription started and later followed the
explicit cancellation path. No ASR exception is recorded. It does not establish why
cancellation was requested, what was displayed beforehand, or whether an earlier transient
failure occurred. No model, memory, readiness or submission defect is asserted.

## Confirmed diagnostic defect

The first two local attempt codes and member messages are `processing`, although their
authoritative remote receipts say `stopped`. `Coordinator.collect` first saves the specific
code, then `Coordinator.completed_futures` overwrites it with generic `processing` when
the cancelled child settles. `runtime_store.checkpoint_attempt` propagates that generic
code into the displayed job message.

A scoped read-only assertion that each stopped task retain its authoritative code failed
for both published members. The narrow regression should exercise accepted work stopped
before any stage output exists, drain the child future, and assert local code/message
`stopped`, remote outcome `interrupted`, zero saved results and no resubmission. The
specific code must survive child settlement. This classification loss does not establish
an underlying inference failure or explain why the cancellation was requested.

## Verified narrow correction

`completed_futures` now retains the current attempt's saved diagnostic and uses processing
only when none exists. Task-ID fencing, state calculations, allocation ownership, cancellation,
retry, cleanup and remote worker protocols are unchanged. Existing historical receipts are
not rewritten. Independent review found no defect in this narrow correction.

The real collect → child Future → Activity regression command was:

```powershell
uv run --no-sync pytest tests/test_runtime_failure_classification.py -q -o addopts=''
```

Before the fix: **2 failed, 2 passed**, reproducing stopped/interrupted becoming processing.
The cases also cover unknown remote codes and local failures without an outcome.

The broader runtime batch/store/remote-controller/queue run reported **119 passed, 1 failed,
1 skipped** in 234.41 seconds. The failure was an existing shutdown test's `accepted.wait(8)`
startup deadline, before collection. Its exact rerun plus all four new cases and four
Stop/cancellation guards passed: **9 passed** in 25.23 seconds. No deadline/lifecycle changes
or broad repeat was made. The skip requires Windows symlink-creation permission.

```powershell
uv run --no-sync pytest tests/test_runtime_failure_classification.py tests/test_runtime_batches.py::test_shutdown_preserves_owned_job_and_same_task_ids_for_explicit_recovery tests/test_runtime_batches.py::test_stop_harvests_output_completed_during_termination tests/test_runtime_batches.py::test_stop_cancels_unpublished_local_preparation tests/test_runtime_store.py::test_stop_resolves_interrupted_unpublished_members_after_proven_pre_post_failure tests/test_runtime_store.py::test_stop_does_not_invent_absence_for_interrupted_published_inputs -q -o addopts=''
```

Ruff lint/format and whitespace checks pass. This correction is verified offline; the
serving checkout remains `c3c94e6`. It does not establish live end-to-end inference acceptance
or resolve the unverified origin of the cancellation.

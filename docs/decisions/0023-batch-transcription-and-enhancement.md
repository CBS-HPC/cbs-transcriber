# 0023 — Unattended transcription and optional follow-up

Accepted: 21 October 2026.

Use one Add word timing / speakers action for existing results. Project Batch transcription
opens the same processing settings with a choice of transcription only or transcription plus
requested features. The user chose immediate unattended execution rather than a future schedule.
Provider checkboxes and the duplicate Compare providers dialog are removed; result comparison
continues through Compare with. Other providers can produce additional results by selection.

Reuse features.py to preflight every recording/provider before queueing any work. Native
capabilities remain enabled and need no redundant enhancement. Official OpenAI models still
determine formats; explicitly configured compatible backends can declare words and speakers.
Check local runtime/CUDA readiness before ASR, or independent UCloud setup and Protected approval.
Speaker detection of untimed text also needs alignment. No setup tokens belong in job params.

Freeze a child ID and enhancement options/cloud identity in each ASR job. Store.add_result
inserts the provider result and a child job containing that exact source snapshot in one SQLite
transaction. Result/job uniqueness makes retries idempotent; raw output and reviewed results
survive. An enhancement failure can be retried without rerunning ASR.

Hold the shared queue lock when checking cancellation and publishing ASR. Cancelling its parent
also targets only that recorded child, including another worker's active child. Existing UCloud
approval rechecks, remote journals and shutdown handling remain in force. No schema change.

GPU detection invokes the optional interpreter's doctor with --hardware. PyTorch reports CUDA
availability, names and memory; missing/broken PyTorch means unknown, not proof of absent hardware.
The app does not load weights, contact a provider or silently select CUDA during detection.

Tests use mocked providers, local runtime/GPUs and UCloud execution. Live model quality,
cached-weight readiness and real UCloud allocations remain separate opt-in acceptance.

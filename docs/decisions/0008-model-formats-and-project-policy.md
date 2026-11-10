# 0008 — Model contracts, project folders and data classification

Accepted: 20 October 2026. Extends 0002, 0004 and 0007.

OpenAI, Custom, and future UCloud settings have separate tabs. Known official OpenAI model
names determine response capability, including old queue snapshots: Whisper supplies word
timestamps, diarize supplies speaker segments, and the three plain transcription models use
JSON text. Compatible backends retain configurable capabilities. GPT Transcribe takes a
`languages` array. The account check calls only `/models`, sending no audio and retaining no
draft key. Model visibility cannot establish billing, quota, or a successful audio request.

New projects choose a parent folder; the app creates a unique child folder containing managed
recordings, source files, raw responses, `project.json` with original/current saved results,
and per-result NVivo files under `exports/`. SQLite in the library remains authoritative and
also contains transcript text. Folder outputs are readable snapshots, not an independent
database; opening arbitrary project snapshots is not yet supported. Snapshot failures are
reported by the project API and never undo a committed edit. Existing projects retain their
legacy paths. Backups must include the library database and all chosen project folders.

Projects are standard, sensitive, restricted, or confidential. The user selected explicit
provider approval for protected projects. Approvals are opt-in per provider/classification;
local endpoints receive no implicit approval. The API and worker enforce this, including
retries and checks between chunks. Frozen endpoint/model identity must still match a live,
approved configuration. Revocation blocks subsequent uploads; already accepted requests
cannot be recalled. These are user approvals, not an assertion of institutional certification.

Review: `providers.transcribe_file` formerly trusted capability over model, reproducing an
invalid diarization request. `app.transcribe`, retry, and `JobQueue.process` all need policy
checks; dropdown filtering alone would permit bypass. `Store.directory` must resolve project
ownership before any file operation. Migrations retain legacy paths and back up schema 2.
Gate: GO with V16–V19 and mocked request, policy-bypass, restart, storage, and browser tests.

Sources: [OpenAI transcription request schema](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create),
[model listing](https://developers.openai.com/api/reference/resources/models/methods/list).

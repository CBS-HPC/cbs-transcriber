# 0012 — Native UCloud Transcriber jobs

Accepted direction, 20 October 2026; implementation and live validation tracked in TODO.

Use the released `ucloud-workflow` package from the UCloud API project for authenticated
control-plane requests and template specification handling. Do not copy its job client or
load another project's environment. Native batch Transcriber is independent of UCloud vLLM.

Require an explicit known-good Transcriber Batch template, project ID and existing writable
storage folder. Retrieve resolved application metadata and validate the configured parameter
names/types. Preserve its machine, application version and unrelated resources/settings.
Use one native job per recording and upload the immutable original; UCloud does its own
transcription/chunking. This is distinct from OpenAI Batch, which remains excluded.

File transfer uses UCloud's negotiated WebSocket V2 / legacy chunked upload and download sessions. Session URLs
and tokens are transient; never send the control token to those endpoints. Native control
tokens use the existing scoped credential store, shared only for the same UCloud origin.

Persist upload completion, submission intent and returned remote job ID in the existing
local job snapshot. Retry monitors the same known remote job. An uncertain submission
requires reconciliation rather than another automatic allocation. Shutdown interrupts local
monitoring while leaving native batch execution intact. Explicit cancellation terminates
only this run's recorded remote job; retain inputs/outputs for recovery.

Download bounded JSON/CSV output through the existing safe transcript normalizer and save
a new result. Preserve reviewed text, speakers, active result and originals. Enforce current
Protected approval before uploads and submission. Changes to native project/template/storage
identity invalidate approval of frozen pending work.

Mocked tests must cover template/parameter rejection, retained machine/resources, transfer
credential isolation, submit uncertainty, retry/restart, shutdown/cancel and output isolation.
A live synthetic recording is a separate acceptance step; setup checking must allocate
nothing and upload no audio.

Sources: [UCloud API project](https://github.com/CBS-HPC/ucloud-api/tree/v0.1.9),
[Transcriber](https://docs.cloud.sdu.dk/Apps/transcriber.html),
[UCloud file session schemas](https://github.com/SDU-eScience/UCloud/blob/master/provider-integration/shared/pkg/orchestrators/files.go),
[chunked upload protocol](https://github.com/SDU-eScience/UCloud/blob/master/frontend-web/webclient/app/Files/Uploader.tsx).

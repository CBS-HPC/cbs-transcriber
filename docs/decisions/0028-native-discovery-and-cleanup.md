# 0028 — Shared presets and token-first native setup

Accepted 21 October 2026. Supersedes the mandatory-template and retained-success-output
portions of ADR 0012; the released API dependency, credential and recovery rules remain.

Keep OpenAI/native model presets in one validated packaged JSON catalog with an optional
external file override. The browser reads that catalog through the local API; backend
OpenAI request formats use the same definitions. Preserve saved/custom models and frozen jobs.

Use the existing ucloud-workflow v0.1.9 dependency, never its CLI environment loader or a
copied control client. Read account project/storage helpers, app search and pinned application
metadata, allocated compatible GPU products and same-provider writable drives. Unique choices
fill automatically; ambiguous project/compute/storage choices remain human-readable selections.
Setup performs only discovery, including the read-only app search POST. It creates no folder
or allocation and transfers no recording. Save exact resolved identities before enqueueing.
Advanced known-good templates remain supported and retain their machine/settings.

For direct setup, construct a Batch specification from declared primitive parameter defaults,
force the configured single input/output/model/JSON overrides, and reject unknown required or
extra file/initialization inputs before upload. Create the app storage folder only at execution.

New configurations default to successful-run remote cleanup; legacy configurations/jobs retain
their policy. Journal a successful fresh unique-root creation before considering that tree owned;
a first 409 fails closed. Download, validate and preserve raw JSON locally. Journal cleanup
pending before committing the independent transcript result. Flush the validated raw copy to
disk before permanently deleting only the exact journaled cbs-localjobid input/output tree.
Verify absence; a rejected/unknown deletion remains pending with visible cleanup-only retry.
Keep that journal beyond Activity's normal limit and block local deletion while unresolved.
Never purge shared Trash, template folders or a drive root. Logs/job metadata outside the
app-owned input/output tree are not covered by this cleanup.

Provider support for permanent DELETE is not assumed: the API project's live evidence includes
a rejected DELETE. No silent Trash fallback or false deletion claim. Tests cover mocked
success, provider refusal, crash, disk failure, ownership conflict and idempotent retry.

Sources: [UCloud API v0.1.9](https://github.com/CBS-HPC/ucloud-api/tree/v0.1.9),
[Transcriber Batch](https://docs.cloud.sdu.dk/Apps/transcriber.html#transcriber-batch),
[app schemas](https://github.com/SDU-eScience/UCloud/blob/c216a062878e88c6b07a416bd754a1e899d85b5d/provider-integration/shared/pkg/orchestrators/app_catalog.go),
[file schemas](https://github.com/SDU-eScience/UCloud/blob/c216a062878e88c6b07a416bd754a1e899d85b5d/provider-integration/shared/pkg/orchestrators/files.go).

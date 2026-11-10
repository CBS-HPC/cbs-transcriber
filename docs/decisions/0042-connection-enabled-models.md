# 0042 — Connection setup and enabled models

Accepted 22 October 2026; supersedes the built-in model-picker UI in ADR 0039.

OpenAI and native UCloud Transcriber setup configures a connection. Model choice belongs in
the transcription controls. Advanced settings lists the catalog plus existing saved model IDs
as enable checkboxes; the OpenAI availability check stays in the basic connection area.
Custom and vLLM retain their independent configuration/model controls.

Each saved provider profile has an enabled flag, defaulting to true when absent. There is no
SQLite schema migration. Public views expose the default; reading old rows and frozen snapshots
does not rewrite them. An explicit enabled_models list in a built-in save atomically updates
flags only for that endpoint (and native project), including legacy IDs. Omission preserves
flags. Protected approval remains independent, including explicit denials.

OpenAI connection saves append missing catalog profiles, as native complete-B200 saves already
do. Profiles share the endpoint credential scope. Existing per-model settings, identities,
recording selections and the default pointer remain intact; no key copies or endpoint checks
are needed. Catalog changes do not silently overwrite saved settings.

New-run UI choices and enqueue/selection APIs exclude disabled profiles. Disabling is not a
cancellation request: frozen running/queued jobs and retries keep their original settings and
can finish. Saved transcripts/results are unaffected. All-disabled connections are allowed,
with the transcription action unavailable until an eligible model is enabled.

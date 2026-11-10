# 0039 — Fixed compute and model-based settings

Accepted: 22 October 2026. Supersedes 0038. Amends the fallback button label in 0037.

The user clarified that native Transcriber always uses **gpu-nvidia-b200-1-gpu**. It is
mandatory, not a preference. Read-only discovery validates application compatibility and
funding and resolves its full provider/category/product reference automatically. Never offer
a compute chooser or an alternative GPU. If absent or ambiguous, return an incomplete setup
with a specific explanation; an existing exact B200 reference can disambiguate identities.
Discard stale non-B200 direct selections during discovery. Advanced templates must themselves
use B200; they do not silently change machines. New-job API preflight requires the resolved B200
reference, and workers reject a configured B200/template identity mismatch before upload.
Already frozen legacy jobs retain their recorded identity; reconnect old configurations before
starting new jobs. UCloud vLLM and enhancement compute remain independent.

OpenAI and native Transcriber tabs use their model dropdowns without a separate Saved provider,
Add or Delete row. Selecting a model loads an existing configuration for that endpoint/model,
or prepares a new one rather than overwriting another model's configuration. Unsaved key text
can carry between models only at the same endpoint. Custom/OpenAI-compatible and vLLM tabs
retain their named configuration manager. Unknown saved OpenAI models remain available in
the model dropdown. No selection saves, allocates, uploads or checks model availability.

The untimed playback fallback's button says **Replay**, as requested. Its tooltip continues
to disclose whole-recording playback when no passage/source interval is known. No saved
timing, transcript version or export changes.

Verification uses mocked discovery/new-job APIs and workers, plus Chromium tests for settings,
model switching, preserved drafts/configurations and real local audio playback. No real cloud
job, upload or paid API request is needed.

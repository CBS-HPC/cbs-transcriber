# 0014 — Offline speech models and provider eligibility defaults

Accepted: 20 October 2026. Supersedes the served-alias UX in 0009 and all-off creation
defaults in 0008/0011. Existing approvals and frozen job identity checks remain.

UCloud hosting is temporary. Use existing named-provider storage for separate saved models,
with Add model in the vLLM tab. Keep each model's endpoint, capabilities and results independent.
Models can be configured offline. Remove endpoint discovery from this tab; retain the read-only
diagnostic API for compatible callers and OpenAI's model check. Transcription still needs a
running service and its separate serving key.

Newly saved vLLM configurations use the HF owner/model ID as served ID. The UI derives it
from the HF field; the server canonicalizes API saves too. Reading legacy queued snapshots
keeps their original identity. Hosts must serve the same HF ID; saving does not deploy a model.

Package a small offline Danish/English catalog following the hosting agent's provenance
pattern: pinned revisions, parameter sources, cards, license metadata and candidate status.
All entries remain unvalidated for UCloud speech deployment, with unknown tested allocation.
Suggested response settings need actual runtime validation. Selection fetches no weights,
executes no repository code and starts no job. Hviske retains its non-commercial/custom-code/
text-only caveats. Custom HF models and revisions remain editable.

Per the user's policy choice, new UCloud configurations default to allowed for Protected
projects; OpenAI and Custom default to blocked. The API exposes these defaults to the UI.
Explicit creation choices win; omitted approval on updates retains the previous choice.
Existing configurations are not reclassified. Editing fields retains the visible choice
until the user saves. Workers still compare the queued endpoint/model/native setup against
the current configuration and recheck eligibility. Changed identity blocks pending protected
work even if the new configuration is allowed. Eligibility is not institutional certification.

Tests use temporary storage, local metadata, mocked providers and Chromium. No live resources,
private uploads or model downloads are needed.

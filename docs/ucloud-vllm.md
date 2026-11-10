# UCloud vLLM provider

This provider is independent of **UCloud Transcriber**. The latter imports output from
UCloud's existing Transcriber application; vLLM serves a Hugging Face model behind a model
API. Use the **UCloud vLLM** tab in Provider settings.

## Connect a running speech service

1. Add a UCloud vLLM configuration with a descriptive name.
2. Enter its HTTPS serving endpoint, for example `https://YOUR-HOST/v1`. A root HTTPS URL
   is normalized to end in `/v1`. HTTP is accepted only for a loopback development tunnel.
3. Enter its **vLLM serving API key**, the value configured as `VLLM_API_KEY` on the server.
4. Choose a pinned catalog candidate or enter an HF owner/model ID. Configure hosting to serve
   this same ID; the read-only Served model ID field mirrors it. No availability check is needed.
5. Start with **Segment timestamps** and parallel parts **1**. The hosted model must actually
   implement `/v1/audio/transcriptions` and the selected format. Save, select the configuration
   beside a recording, and explicitly start transcription.

Use **Add model** for several independent saved configurations, including ones whose hosting
has stopped. Endpoint/model-list discovery is not part of this tab. The catalog uses local
pinned metadata; all current entries are unvalidated speech-hosting candidates. See
[catalog usage](model-catalog.md). Do not change models automatically when transcription fails.
Plain JSON is available for text-only responses. Word timing needs actual word support;
missing timestamps stay untimed. Speaker mode requires a model/runtime implementing
`diarized_json`; the client does not send OpenAI's automatic chunking parameter to vLLM.

The inspected hosting project's nginx template proxies `/v1/` and Authorization to vLLM.
It has a 16m request limit; this provider suggests 14 MB for headroom. Review the actual
deployed template's upload/time limits; catalog selection does not verify those limits.

## Hosting setup credentials

Model/revision fields are in the model panel. Expand **UCloud & Hugging Face setup** for
server/project and setup credentials:

| Field | Purpose |
| --- | --- |
| UCloud server | Control-plane server root; defaults to `https://cloud.sdu.dk`. |
| UCloud project ID | The project that will own hosting resources. |
| UCloud API token | Authentication for future UCloud provisioning, not model inference. |
| Hugging Face token | Authentication for gated/private model access during hosting setup. |
| Hugging Face model/revision | The intended owner/model and revision for the hosting project. |
| Serving endpoint/key | Connection to the deployed speech API; separate from setup credentials. |

Saved tokens use the OS credential store, or this server session if Remember key is off.
UCloud tokens are scoped to the control-plane server; the HF token is shared by HF setups;
serving keys are scoped to their serving endpoint. They are never stored in SQLite, project
snapshots, job parameters, Git, browser storage or command-line arguments. Blank fields retain
saved tokens. **Forget hosting tokens** explicitly clears the corresponding shared setup
credentials. Switching tabs/configurations or closing settings clears password inputs.

You can save a configuration while awaiting its URL. Such a configuration remains editable
in settings and is unavailable for transcription. Saving tokens creates no UCloud job, Link,
public endpoint or model download. No credentials are read from another project's environment.
New UCloud configurations default to allowed for Protected projects. Change that choice per
model if needed. Existing saved approvals or denials stay unchanged; current eligibility and
frozen endpoint/model identity are checked before protected uploads.

## Hosting-agent integration boundary

The inspected Codex project **UCloud vllm agent** now lives in the **CBS Inference** workspace.
It owns HF inspection, sizing, pinned profiles, CPU/GPU preparation, nginx/Link setup,
deployment journals, recovery and release. Its current configuration accepts only
`task=chat` and `task=pii`. It does not yet have a reviewed speech-model deployment path.
Its chat/PII validation must not be presented as speech validation.

An offline speech extension now exists on ucloud-vllm-agent's
`feature/transcription-models` branch (ab49949), with separate audio runtime,
request-bound manual sizing and multipart/auth checks. It has mocked/offline package
verification only. The published release remains unchanged; no ready-to-launch speech
profile or tested allocation is claimed. See [ADR 0020](decisions/0020-provider-owned-speech-hosting.md).

Consequently this branch provides the connection and secure setup fields, but **does not
automatically deploy speech models or create their URLs**. Those actions need an ASR workflow
in the hosting project, including audio dependencies, task-aware sizing, remote credential
provisioning and audio endpoint tests. A UCloud token and HF token alone do not establish
the project's templates, mounts, products or permissions. URL creation must use that project's
resource ownership/receipt workflow, not an invented hostname or a second job-submission path.

Live speech hosting has not been tested here. Automated checks use a synthetic local vLLM
endpoint and fake credentials. No UCloud allocation or real interview upload occurs in tests.

Reference: [vLLM speech-to-text API](https://docs.vllm.ai/en/stable/serving/online_serving/speech_to_text/).

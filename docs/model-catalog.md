# Speech model catalog

Provider Settings → UCloud vLLM → **Add model** saves a separate model configuration.
Filter by Danish or English and choose a candidate to fill its model ID, pinned revision
and suggested response format. Or enter a custom HF owner/model and revision. The served ID
matches that HF name; the host must serve the same ID.

Configurations remain saved when hosting stops. Leave the endpoint blank until a service
is running, then enter its URL and serving key. No availability check is required to save
a model. Catalog selection does not deploy, download or execute anything.

The packaged JSON in `src/cbs_transcriber/catalogs/speech_models.json` is exposed through
`GET /api/providers/catalog`. Reviewed 20 October 2026, it follows the hosting agent's
candidate/validated and pinned-provenance pattern: revision, parameter source, model card,
license metadata, Danish/English language scope, suggested response and runtime endpoint.
Tested allocations are unknown and evidence lists empty until actual speech hosting is tested.

Candidates: Whisper base/small/medium/large-v3/turbo, Parakeet v3, Danish Hviske v5.3 and Røst
Whisper v2, and English-only Distil large-v3.5. This is not an accuracy ranking. HF parameter
metadata can differ from rounded marketing counts. Language labels describe this app's
Danish/English scope, not each multilingual model's exhaustive language list.

All entries remain **candidates**. Runtime compatibility, memory, output timing and allocations
have not been validated here. Hviske suggests JSON, requires reviewed custom code, uses
CC-BY-NC-4.0 and does not document word timing/speakers. Røst's OpenRAIL metadata requires review
of its linked terms. See [research](speech-model-research.md) and
[the decision](decisions/0014-offline-models-and-protected-defaults.md).

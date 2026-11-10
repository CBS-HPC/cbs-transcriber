# Provider model presets

OpenAI, native UCloud Transcriber and CBS Runtime choices share
`src/cbs_transcriber/catalogs/provider_models.json`. Backend format normalization and the
browser dropdowns use the same catalog; no HTML/JavaScript model list needs updating.

Edit that JSON and restart the app. For an installed package, copy it outside the installation
and set `CBS_TRANSCRIBER_MODELS` to the absolute path of your maintained copy before launching.
The file uses `schema_version: 2` with `openai`, `ucloud_transcriber` and
`ucloud_cbs_runtime` groups and their default model IDs. Native Transcriber entries belong
only in `ucloud_transcriber`; WhisperX entries with `engine: "whisperx"` belong only in
`ucloud_cbs_runtime`. The two UCloud groups may not reuse model IDs. An empty UCloud group
uses `default: null`; at least one UCloud group and the OpenAI group must contain models.
Older schema-1 overrides are split in memory by their explicit engine field, preserving
their IDs, names and source file. They are never rewritten automatically (ADR 0062).
Invalid files stop startup with a descriptive error. Never put tokens or personal data in it.

Each entry has a model ID, display name and capability: words, words_speakers, speakers,
segments or json. OpenAI entries also have a unique key used by the preset selector;
legacyName preserves automatic labels from earlier releases. language_parameter is normally
language; a model requiring a languages array can explicitly set it to languages.
Native entries include approximate memory_gb guidance and optional aliases used only when a
declared UCloud enumeration offers that spelling. Memory guidance does not verify GPU capacity.

The six native presets reflect the [UCloud model table](https://docs.cloud.sdu.dk/Apps/transcriber.html#settings):
base, small, medium, large-v3, large-v3-turbo and nvidia/parakeet-tdt-0.6b-v3.
The selected Batch application's actual model enumeration is checked before upload. An older
app may offer fewer models. Word/speaker output still depends on its actual processing/output.
Catalog selection neither starts a job nor verifies execution.

Settings configures each OpenAI/native connection without a model dropdown.
**Available models** in the basic tab lets users enable or deselect catalog and saved model
IDs. The OpenAI availability check stays visible in the basic tab and does not alter these choices.
The UCloud list and Model dropdowns distinguish **UCloud Transcriber** from
**CBS Runtime / WhisperX**. Catalog labels end in **(Transcriber)** or **(CBS Runtime)**,
respectively. CBS Runtime models use the Terminal-based CBS worker, not the native Transcriber
application; they share only the UCloud connection and its credentials.
A saved enabled flag is separate from Protected-project approval. Both must permit a new run.
Disabling hides a model and blocks new API enqueue/selection, preserving existing results,
selected IDs and already frozen jobs/retries. Old configurations default to enabled.

Transcription controls use separate **Provider** and **Model** dropdowns. Configuring native
Transcriber once creates the missing catalog model profiles for that server/project. Startup
also fills missing profiles offline for complete fixed-B200 setups, including newly added
catalog entries. OpenAI connection saves also append missing catalog profiles. Existing model
settings, explicit Protected approvals/denials and defaults remain
unchanged. An explicit enabled-model selection updates the matching group atomically; omission
preserves existing flags. Generated profiles inherit the complete source connection and approval; their
keys share the existing control-endpoint credential. No availability check or job is started.

**UCloud → Advanced settings → CBS Runtime language** edits one saved WhisperX model
at a time, with version checks. **Use Processing** (blank) retains the saved Danish/English
fallback; **Automatic (experimental)** opts only that model into full-ASR language decisions.
Explicit Danish/English overrides remain available. Saving a connection does not opt other new
models into Automatic. Existing aliases display canonically without changing their stored value;
unknown legacy values remain visible. This control does not check an endpoint, prepare weights,
change Protected approval, or rewrite frozen jobs. General Processing/repair defaults stay fixed.
See [ADR 0071](decisions/0071-explicit-runtime-automatic-language.md) for acceptance and carry.

Edda v0.2 turbo, Edda v0.2-large and Røst v3 are fixed-Danish CBS Runtime profiles.
Their reserved model IDs are `whisperx/edda-turbo`, `whisperx/edda-large` and
`whisperx/roest-v3`; each catalog entry requires its matching `whisper_model` choice and
`fixed_language: "da"`. Settings locks their language to Danish. Existing Whisper choices
retain the language controls above. The source inventories and conversion recipe live in
`catalogs/danish_whisper_models.json`; an ordinary preset cannot redirect them to an arbitrary
repository. Explicit preparation verifies the pinned safetensors/tokenizer/preprocessor files
before local CT2 conversion. Inference uses the verified derived cache. Source and conversion
receipts stay distinct in research reports; implementation does not establish accuracy or CBS
licence clearance. See [ADR 0084](decisions/0084-danish-whisper-runtime-models.md).

OpenAI uses a blank language to omit the hint; literal `auto` is refused for new OpenAI saves
and official-host transcription requests. Native Transcriber also refuses literal `auto` in a
new resolved specification, including inherited templates, while preserving owned-job recovery.
Leave its hint unset only when the actual template is unset. Genuine compatible custom endpoints
retain their configured capabilities; no saved historical settings are rewritten.

Saved provider configurations, credentials and frozen queued jobs retain their existing
storage and identity. Editing a preset does not silently rewrite saved user configurations.
Existing custom-backend configurations remain readable. Custom and UCloud vLLM setup tabs
are deferred; native Transcriber offers only its documented presets. The separate pinned
Hugging Face candidates for vLLM remain in `src/cbs_transcriber/catalogs/speech_models.json`.

# Connect a transcription service

MVP 0.1.0 offers OpenAI and UCloud. Open **Settings**, save a connection, then choose
**Provider** and **Model** when starting transcription. Connection setup does not transcribe
recordings. Importing, reviewing and exporting an existing transcript needs no service key.

The unreleased post-MVP branch disables the direct OpenAI service while retaining saved
results and shared compatible-protocol code. Its first-use dialog offers UCloud setup and an
optional HF token; saving that token queues access/configured-model preparation in Activity.
UCloud-only connection setup starts no preparation. UCloud model preparation requires an
enabled CBS Runtime profile; native Transcriber models do not select those weights. The
published MVP retains the behavior described below until a separately approved update.

## OpenAI

Enter an API key in the OpenAI tab and save. **Available models** controls which models appear
for new work. New setups initially enable GPT Transcribe; other choices can be enabled here.
Disabling a model keeps saved results and already queued jobs.

**Check model availability** only reads the endpoint's model list. Listed/Not listed describes
visibility to that key; it does not verify billing, quota or successful audio transcription.
The check uploads no audio and does not save an unsaved key.

Model capabilities differ. The configured Whisper choice supplies word timing; the diarization
choice supplies speaker labels/passage timing. GPT Transcribe and the other plain-text choices
need separate processing for missing word timing or speaker labels. Choose optional extras
beside Transcribe; features already supplied by a model stay checked/locked.

Sending audio uses the provider's normal charges. Comparing saved results is local and needs
no request; transcribing with a second model sends another request. The app does not use the
OpenAI Batch API or automatically summarize/clean up your transcript. For changing service
capabilities, see [OpenAI speech documentation](https://developers.openai.com/api/docs/guides/speech-to-text).

## UCloud

Enter your UCloud access token in Settings → UCloud. Discovery fills unique eligible project,
application, compute and storage choices; choose explicitly where there is ambiguity. An
account's ownership metadata can suggest a project but does not establish permission.
Saving does not upload a recording or start compute.

A model appears for new work only after the saved token's connection has been checked. In
MVP 0.1.0, after entering a new token choose **Connect to UCloud**, then **Save**. On the
unreleased post-MVP branch, **Save** runs that connection check itself whenever a token was
typed or the saved token is unchecked, in Settings and in the first-use dialog. While a saved
token is unchecked, the Provider/Model lists say **Check the connection in Settings**.

New native Transcriber jobs use the fixed **B200 1g MIG** policy. Missing or ambiguous funded
compute blocks new work; there is no fallback to a different GPU. Reconnect older or
template-bound configurations before new jobs; frozen existing runs keep their original choices.
Enable supported models under **Available models** and select one when transcribing.
New setups initially enable Whisper large-v3. Selecting several compatible recordings creates
one shared allocation, with a separate saved result/outcome for each recording.

After a validated local save, the app normally moves its owned remote run folder to UCloud
Trash. Trash is not emptied; empty it in UCloud separately to free space. If removal cannot
be verified, Activity retains cleanup-only Retry. Pending cleanup is an unresolved receipt,
not proof that the files are still present. See [native setup and recovery](ucloud-transcriber.md).

Full-profile CBS Runtime models use the app's own pinned WhisperX runtime, separately from
native Transcriber. They are hidden in MVP. Selecting a catalog entry does not prepare a
model or prove its quality/licensing. Edda and Røst profiles are fixed-Danish choices whose
real comparative inference and CBS licence acceptance remain separate. See
[presets](provider-models.md), [UCloud processing](ucloud-enhancement.md) and
[Danish model review](danish-model-licence-review-2026-10-27.md).

## Language, protected data and keys

Choose Standard or Protected when creating a project. Protected projects offer only models
explicitly allowed in provider **Advanced settings**. New UCloud connections default to
allowed; OpenAI defaults to blocked. Saved choices are retained. This is your recorded
permission, not CBS institutional certification. Revoking permission can block work not yet
uploaded; it cannot recall accepted uploads. Classification changes wait for active work to stop.

New requests use the recording language, then the project default, then saved model/processing
fallbacks. Model support varies; known incompatible choices are refused and unverified ones
may fail at preflight/provider execution. A configured language is not detected evidence.
Queued jobs preserve their original settings. Automatic language behavior is limited; see
[local runtime/language limits](local-enhancement.md) and [model settings](provider-models.md).

Remembered keys/tokens use Windows Credential Manager or macOS Keychain. macOS may ask for
Keychain permission. An unremembered key lasts for the server session. `OPENAI_API_KEY` is
also accepted for the default OpenAI endpoint. Keys are not returned to the browser or stored
in Git. Configurations at the same endpoint share its key; deleting a configuration retains
that key and its saved results. A changed endpoint does not inherit another endpoint's key.
See [optional credential removal](storage-and-removal.md#uninstall-and-optional-cleanup).

Speaker-model preparation separately needs a Hugging Face read token and acceptance of
pyannote/speaker-diarization-community-1 terms. **Settings → Hugging Face → Check token**
validates access and downloads/verifies that model locally. Public alignment models need no
token. Cached models run offline; the HF token is not sent with recordings.

## Activity, Stop and Retry

Keep the app/computer running for local/API processing. Completed API chunks checkpoint and
are retained on Retry. An interrupted/uncertain request may already have reached the provider;
retrying its missing chunk may incur another charge. The app does not automatically repeat
uncertain submissions or switch providers. Stop prevents further local scheduling but cannot
revoke an accepted provider request. Graceful shutdown waits for accepted calls to settle.

UCloud work may continue after the app stops. Reconnect/Retry uses its recorded allocation;
an uncertain submission must be reconciled by its exact unique name or your explicit
confirmation that no job was created. Reconciliation itself uploads nothing. Live
shutdown/relaunch handoff acceptance remains open; do not assume every failure is recoverable.
Native batch Stop affects that allocation's files. A shared enhancement allocation with other
recordings may continue; stop its own card explicitly if needed.

**Clear finished** hides settled activity; it does not delete jobs/results or imply cleanup.
Unresolved recovery/cleanup remains under **Needs attention** with its own actions. Removing a
finished card only hides the card. Deletion can cancel jobs and ask about affected shared
allocations/remote folders; see [deletion consequences](storage-and-removal.md#delete-recordings-or-projects).

API requests do not follow redirects. Set the final transcription `/v1` endpoint if a proxy
redirects. For explicit processing placement, Runtime batches and retained-file disposition,
use the linked specialist guides. Full-profile Custom/UCloud vLLM
setup is deferred in the normal UI; existing configurations/results/frozen jobs remain
compatible. [vLLM guide](ucloud-vllm.md) · [speech catalog](model-catalog.md).

## API parts and local capacity

For API providers, long recordings use bounded mono 64-kbit MP3 chunks split near silence
where possible. Returned times are offset into the recording timeline. Voices in different
chunks get part-specific labels because their identities cannot safely be assumed identical.
Native Transcriber uploads selected originals and uses its own processing.

**Parallel parts** permits 1–4 simultaneous requests within one recording. Each accepted part
checkpoints its index; results combine chronologically even if replies finish out of order.
On error, new scheduling stops while accepted requests settle. Retry sends only missing parts.
Part concurrency is frozen at queue time, so changing a connection does not speed up an
already queued job or change its Retry settings.

Settings → Local Processing → **Parallel local tasks** separately limits files handled by
playback preparation, source inspection, API requests and request preparation. Higher settings
use more CPU/memory and count against provider rate limits. Changes affect new claims;
running files continue. Native Transcriber and local enhancement have separate processing
lanes; provider-wide capacity management remains later work. See
[local processing and recovery](local-enhancement.md).

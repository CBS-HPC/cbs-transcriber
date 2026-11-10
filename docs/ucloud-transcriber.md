# Native UCloud Transcriber setup

This provider uses [UCloud Transcriber Batch](https://docs.cloud.sdu.dk/Apps/transcriber.html),
independently of UCloud vLLM. It uses the released
[UCloud API project v0.1.9](https://github.com/CBS-HPC/ucloud-api/releases/tag/v0.1.9)
for control-plane requests and job specification handling. Code is MIT licensed; its original
documentation is CC BY 4.0. No other project's `.env` is read.

## Configure

1. Open Settings → **UCloud**. Enter your API token and choose
   **Connect UCloud**, or simply Save. The server defaults to `https://cloud.sdu.dk`.
2. The app discovers resource-enabled projects, Transcriber Batch, funded compatible GPUs
   and writable storage on the same provider. Fresh native requests use a B200 1g MIG;
   there is no automatic full-GPU fallback.
   A compatible funded identity resolves automatically; unavailable or ambiguous compute blocks
   new work. Existing exact references can disambiguate. If there are several projects/drives, choose a human-readable
   option; no IDs need typing.
   The selected project, machine and storage are displayed before saving.
   With several projects, the app first tries to match the native token's exact metadata record
   and use its eligible owner project. This is an association, not a claim about access scope.
   If the record is unavailable or has no usable project, choose a project explicitly.
   Saved/advanced project selections are retained. Discovery errors identify the failing stage.
   **Transcriber application/version** identifies the UCloud program, separately from the speech
   model. The Batch flavor runs unattended and stops when complete. Discovery expands available
   flavors in each application group; search alone can show only the interactive default.
   UCloud represents the current Batch flavour as a web-capable application. The app recognises
   that verified contract, resolves its declared input/output field names and explicitly disables
   JupyterLab. It does not offer the interactive Transcriber GUI.
3. **Available models** enables or hides the six documented native presets. Previously saved
   profile languages remain effective; a blank language uses the UCloud application
   or template default; it does not necessarily mean automatic detection. After saving,
   select Provider **UCloud** and a model marked **(Transcriber)** beside **Transcribe selected**
   or on the recording review page. Large-v3 is the native default. The models marked
   **(CBS Runtime)** use a separate worker and MIG allocation. Model support depends on the
   actual app version. See [editable model presets](provider-models.md). There is no editable
   custom native model ID; older saved configurations and frozen jobs remain intact.
4. Save. The token belongs in the OS credential store or this server session. Discovery
   uploads no recording, creates no folder and allocates no compute. The app-owned storage
   folder is created only when you start transcription. No Hugging Face token is required.
   Connect does not save the token: refreshing before Save clears an entered draft token.
5. New configurations allow Protected projects by default; review that saved policy for
   your institutional workflow. It is a user configuration, not institutional certification.

**Advanced settings** retains custom server/project/storage and known-good Batch template
overrides. A template is optional and must be compatible with the required compute. Its application version,
mounts and unrelated processing settings are retained. Use one without initialization scripts or a
second input directory. **Check UCloud setup** reads its declared parameter/model names and
storage permissions. Custom parameter names remain available for version differences.
New jobs reject a native template bound to a full B200; reconnect with a compatible 1g MIG template.
Reconnect old configurations before creating new jobs; the new-job API requires the resolved
compute reference. Already frozen legacy runs retain their original allocation identity.

The input mapping must be an `input_file`; the output folder must be a file/directory
parameter. Model/language/format overrides must match the application's declared text/enum
fields. The default format override is `json`. Leave its parameter name blank to retain the
template's formats, which must include JSON. A blank language retains the template language.
Template parameters for speaker counts and other processing options remain intact.

Checking metadata is not proof of a successful transcription. Short native 1g MIG runs have
succeeded for some models; other models, larger workloads and the chosen UCloud project still
need acceptance. Development tests use mocks;
they do not allocate compute or upload research recordings.

## Transcribe and recover

Select the recordings, Provider **UCloud** and the desired **(Transcriber)** model. Compatible
members of one selection share a unique `cbs-batch-<batch-id>` folder and one native allocation,
with separate local results. Different model/connection selections remain separate. The immutable
originals are uploaded, then the native job starts. Upload negotiation supports WebSocket V2 and legacy chunked HTTP.
The uploaded file's reported size is checked before submission. Session tokens/URLs are
transient, and control-plane credentials are never forwarded to transfer endpoints.

Activity shows native state and **Open UCloud job**. One unambiguous original JSON output is
downloaded after success, preserved locally and normalized through the existing importer.
Word timing and speaker labels are available when the returned JSON supplies them. Existing
reviewed results and their active selection are preserved.

- **Retry** reconnects to the recorded remote job; it does not launch another one.
- **Stopping the local app** leaves native batch work running. Restart and explicitly retry
  to resume monitoring; the original remote job ID is retained.
- **Stop** on a native batch requests termination of its one recorded allocation, affecting
  every member of that batch.
  If monitoring is interrupted or cancellation cannot connect, inspect the UCloud job directly.
- If submission was attempted but no ID was received, automatic retry refuses to allocate
  another job. Inspect the exact CBS run name shown in Activity before deciding to start a
  new transcription. Activity offers **Reconnect UCloud job**: paste its ID; the app checks
  the exact unique CBS name before attaching it. This reads metadata without submitting work.
  **No job created…** records your explicit confirmation after inspecting UCloud; it does
  not independently verify absence. Retry can then submit again and journals the new attempt.
- Recording/project deletion requests cancellation of affected work and confirms the impact
  on any shared allocations and the owned folders it will move to UCloud Trash. Uncertain
  submissions and failed cleanup remain visible for reconciliation or retry; cancellation
  without a remote ID cannot prove the allocation stopped. Some unresolved legacy journals
  still require reconciliation before deletion.
- **Move UCloud run files to UCloud Trash after successful download and local save** defaults on
  for newly created configurations. Existing configurations and queued jobs retain their prior
  setting. Turn it off to retain successful remote files. Failed/uncertain runs are retained.
- Cleanup follows validated local raw JSON and result persistence, with a disk flush before
  the move to Trash. Only that run's journaled unique input/output folder is eligible. An initial folder
  conflict cannot be adopted. Template folders, drive roots, shared Trash and other jobs are
  never removed. UCloud job records/logs outside the owned input/output tree are retained.
- Activity reports whether the move was verified. A provider rejection, disk/connection issue
  or unverified absence leaves **Retry remote cleanup**, without repeating transcription or
  submitting a job. This obligation stays visible beyond recent Activity history and prevents
  local deletion from losing its journal. The app confirms absence by listing the owned folder's
  parent; it never purges Trash. A four-output existing-allocation recovery and cleanup was
  [verified on 28 October](verification-2026-10-28-native-recovery.md), preserving earlier research
  versions. This does not validate every UCloud project/application version.

Independent selections may run concurrently. Within one native selection, the application
processes the shared input set; the local app does not impose native part parallelism.

The app does not require an HF token for this native provider: model access and diarization
dependencies belong to the selected UCloud application/template. UCloud vLLM setup is deferred;
its earlier saved configurations and frozen jobs retain their identities.

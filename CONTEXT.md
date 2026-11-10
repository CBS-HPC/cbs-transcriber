# Domain glossary

| Term | Meaning |
| --- | --- |
| Project | A collection of recordings belonging to one research activity. |
| Classification | Standard or Protected; Protected projects require saved provider eligibility. |
| Enabled model | A saved model shown in new-run selectors; independent of Protected approval and without cancelling frozen jobs when disabled. |
| Provider approval | The user's permission to use an endpoint/model for a project classification; not institutional certification. |
| Project folder | Chosen managed location for media, sources and readable result snapshots; SQLite remains authoritative. |
| Recording | An imported audio or video source and its associated transcript. |
| Collection | A named group of recordings inside one project (a wave, site or pilot); a recording is in at most one, otherwise Unsorted. Metadata only (ADR 0060). |
| Library backup | A verified copy of the library database plus a hashed list of every original recording (ADR 0061). |
| Runtime cohort | At most eight accepted tasks processed by one child in stage passes, retaining one pinned engine object at a time; late tasks form a new cohort (ADR 0094). |
| Transcript | The text of a recording, organised into passages and optionally timed words. |
| Result Trash | Recoverable removal of a saved model/enhancement result from live choices and exports; retained research history and files are not erased. |
| Provider family | OpenAI, Custom, UCloud vLLM or UCloud Transcriber; the first selector narrows real saved model configurations. |
| Native model expansion | Additive catalog profiles for one complete server/project connection, sharing the endpoint credential and preserving existing rows. |
| Provider configuration | A named endpoint/model/language/output setup; keys are shared per endpoint. |
| UCloud vLLM | A self-hosted speech-model connection, independent of the UCloud Transcriber import workflow. |
| UCloud Transcriber provider | Native batch application jobs submitted through the UCloud API project, with independent remote job IDs. |
| Batch template | A known-good native Transcriber Batch job whose application and unrelated settings are reused; its compute must be compatible with the required native product. |
| Served model ID | Model identity sent to the API; new UCloud vLLM saves use the HF owner/model name. |
| Speech catalog | Pinned offline Danish/English model candidates; selection does not deploy them. |
| Provider preset catalog | Schema-2 editable OpenAI, native Transcriber and CBS Runtime groups shared by browser and backend; old overrides normalize in memory. |
| CBS Runtime model | WhisperX transcription on the app's pinned Terminal-based UCloud runtime; shares a connection with native Transcriber but uses a different engine and separately frozen compute. |
| Runtime MIG policy | New CBS Runtime jobs freeze one verified funded B200 MIG slice; legacy jobs and native Transcriber keep their own product identities. |
| Native auto-setup | Read-only token-based project/app/storage discovery. Fresh native work uses B200 1g MIG; unavailable or ambiguous compute blocks new work. Frozen legacy jobs keep their product. |
| Remote cleanup | Moving an app-owned run's input/output tree to UCloud Trash after local preservation or exact deletion consent, confirmed by listing its parent; the user empties Trash; independent retry. |
| Deletion release | A confirmed decision for exact batch-owned folders, kept independently of removed project/job rows so failed cleanup stays actionable; never a new transcription or Trash purge. |
| Local deletion stage | An exact managed path renamed into a same-volume marked wrapper before logical SQLite deletion; recovery restores live targets or cleans already committed targets, without replaying Delete. |
| Native batch | One UCloud allocation shared by selected recordings with identical native settings; each recording keeps its own job and result. |
| Parallel parts | A bounded number of audio parts sent concurrently within one recording job. |
| Original-input observation | A job's frozen identity, decoder recipe and sample-clock measurements for the immutable managed original; separate from browser playback readiness. |
| Original inspection cache | Internal schema-13 observation bound to one generation, job and epoch; retains a complete byte identity through retry, separate from executable producer admission and playback readiness. |
| Preparation receipt | Hashes, sample geometry and actual source window binding for prepared inference bytes; local PCM evidence does not prove a provider's internal processing. |
| Repair clip bundle | An immutable local inventory of selected alignment clips and exact text mappings; not upload approval or a running remote job. |
| Saved result | One provider run or import, with its own text, edits, timings, and speaker names. |
| Result version | Immutable exact observed state of one saved result, with a content hash and review predecessor. |
| Source version | Exact saved state frozen as input to an enhancement, distinct from its review predecessor. |
| Enhancement merge | Explicit adoption of nonconflicting timing or speaker updates into the reviewed source result, with wording and earlier versions retained. |
| Wording adoption | Explicit researcher acceptance of one comparison passage's exact text into the reviewed result; destination bounds/speaker stay, word timing becomes stale, and source version evidence is retained. |
| Merge base | The exact source version frozen before an enhancement, used to distinguish later researcher edits from machine changes. |
| Legacy observation | Current state captured at migration; earlier edit history remains unknown. |
| Evidence report | A metadata-first methods draft and manifest for preserved versions, with explicit provenance gaps. |
| Topic codebook | Versioned researcher-approved topic definitions with stable IDs and explicit retirement. |
| Coding run | A local interpretation scope bound to exact transcript/codebook versions and hashes. |
| Evidence span | Verbatim frozen passage text at half-open Unicode code-point offsets. |
| Annotation review | An append-only human accept/change/reject decision, separate from transcript changes. |
| Visual comparison alignment | Best-effort vertical matching of passages; does not align words to audio or alter saved text. |
| Active result | The saved result currently selected for review/editing and explicit current-view export; independent of the Working designation. |
| Working transcript | An immutable saved version used by default project exports and research reports, selected by the user or advanced under the fenced automatic rules in ADR 0133. |
| Alternative | A retained live saved result or version that is not the Working version; it remains independently editable and restorable. |
| Role event | An append-only observation marking or clearing an exact Working version, with its local-user or system actor recorded separately from content edits and producer ancestry. |
| Timing application receipt | The atomic link between one completed timing job and its exact immutable timing successor; separate from the saved result's original producer and source version. |
| Passage / segment | A stretch of transcript with optional start/end times and a speaker label. |
| Manual passage review | User progress (Not checked, Checked or Needs review) bound to exact saved passage content, independent of uncertainty hints and Working versions. |
| Current word mapping | Word offsets and intervals still belonging to the present text; independent of complete coverage and whether a researcher has edited words. |
| Passage merge | An explicit adjacent join, retaining source text/timing and recording the chosen speaker and prior version. |
| Passage split | A Unicode text-boundary edit using real word intervals or a deliberate passage audio position; untimed halves remain untimed. |
| Timed word | A word associated with an interval in the recording. |
| Partial word playback | Highlighting only individually valid words while the passage remains flagged as incomplete. |
| Source passage replay | Replaying an exact frozen source interval for a legacy untimed fragment; does not locate that fragment's words. |
| Word alignment | Matching existing transcript words to audio; it does not rewrite the reviewed text. |
| Enhancement | Optional word alignment and/or speaker detection, saved as an independent result. |
| Passage repair | Local alignment of selected actual audio windows, appended as one result while preserving other passages. |
| Description / notes | Plain-text project/recording metadata, saved with its own revision; not transcript input. |
| Speaker assignment | Choosing a voice label for one passage, distinct from renaming that label throughout a result. |
| Batch workflow | Selected project recordings transcribed immediately and optionally enhanced with one frozen processing setup; ordinary API requests, no OpenAI Batch API. |
| Export Settings | Shared result/project download dialog; project ZIP reports exported and skipped results. |
| Prepared export | A temporary ZIP built independently of its dialog and downloaded through Activity; frozen selected content, not a backup or restart-durable processing job. |
| Parallel local tasks | Per-step limit on how many files this computer prepares, inspects, admits or sends to a provider API at once; it gates new claims only and never stops running work (ADR 0146). |
| Local inference runtime | App Python environment with standard WhisperX/CPU PyTorch dependencies; an alternate CUDA environment is an advanced option. Models are cached or downloaded explicitly. |
| Coarse alignment window | Bounded audio search interval located by matching distinctive recognized phrases to reviewed text; not a word timestamp. |
| UCloud enhancement | The same alignment/speaker worker run as an API-only batch job on the Provider Settings UCloud connection; older frozen jobs keep their SSH template. |
| Review hint | A model's own uncertainty signal for a passage or word, kept beside a result and shown relative to that transcript; not a measure of correctness. |
| Automatic processing | Per-recording choice, frozen at queue time with a reason, between this computer (short recordings) and UCloud (longer ones). |
| UCloud runtime | A versioned, hash-locked Python/model folder on the project drive, prepared once on explicit request and mounted read-only by later jobs. |
| Uncertain submission | An allocation POST was attempted without a confirmed remote ID; retain the journal until exact-name reconnection or explicit user-confirmed absence. |
| Word correction | Replacing an existing word without changing its timing interval. |
| Free-text rewrite | Changing passage text freely, without claiming timings for the new words. |
| Speaker | A label for a voice in a recording; it is not proof of a person's identity. Unnamed labels display as Speaker_01, Speaker_02, ... per result. |
| Transcription job | One requested operation producing text for a recording. |
| Chunk / part | A bounded portion of a recording sent in one transcription request. |
| Library | The user's locally managed projects, recordings, transcripts, and results. |
| Remove from list | Hides a library from the main list; its files and work are kept (ADR 0128). |
| Permanent library deletion | Deletes an inactive library folder and the external project folders it exclusively owns, after a read-only preview and typed confirmation (ADR 0144). |
| Deletion reservation | The record in the app folder that blocks opening or recreating a library folder until an interrupted permanent deletion is restored or finished. |
| NVivo transcript export | A platform-specific timed text file for importing beside media in NVivo Windows or Mac. |
| Readable text export | INTERVIEW document with speaker/timestamp headings; includes untimed passages. |

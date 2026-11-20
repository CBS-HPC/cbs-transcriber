# 0173 — HF access and configured-model preparation in Activity

11 November 2026. The user explicitly chose automatic access checks and local/UCloud model
preparation when a token is saved and the relevant processing is configured. This supersedes
ADR 0171's earlier Save-only/no-preparation choice. The browser still never calls the old
synchronous HF Check route. PII-Tracer remains parked until CBS Runtime stability is accepted.

## Owner and acceptance

Add a Store-bound, server-lifetime HF setup manager, following the existing background export
ownership pattern. A nonempty explicit Save through the primary `/api/huggingface-settings`
PUT (used by HF Settings and first-use setup) persists through the existing credential
mechanism and returns HTTP202 with a public task reference. The Activity task owns metadata
checking, local preparation and coordination with the existing durable UCloud preparation
owner. Do not pretend this is a recording job or allocate a new settings row/table/secret store.
An empty/forget update starts no preparation and invalidates superseded local work; an absent
token field is a read/no-change operation and does not cancel unchanged active work. Identical
active token/plan requests deduplicate; a later explicit Save can recheck
access while reusing verified files. No automatic restart/resubmission of a completed or
interrupted parent follows from merely opening the app.

The existing `/api/huggingface-settings/check` route becomes an asynchronous explicit recheck
of the saved token, returning the same HTTP202/task envelope. It no longer performs inline
downloads. This preserves a way to recheck accepted terms without retyping the token and
avoids a second check API. Refuse a different unsaved draft with a Save-first message; never
silently persist it through Check. The browser's existing Check button uses this new queued
meaning, not the old synchronous implementation.

Legacy HF credential writers in Processing/provider setup remain compatible and do not
silently activate this new workflow. Every actual credential/plan change through any writer
must invalidate/supersede current work and its published receipt: manager stage/process-poll/
publication guards reread the current credential and plan, and HTTP aliases notify invalidation
after their existing writes (including partial failure). Do not start preparation before a
same-request Processing settings save has completed. This avoids unrelated preparation from
the separately deferred custom/vLLM setup path.

Expose queued/check/local/cloud/terminal states, explicit partial/unavailable outcomes and
Stop/Retry in Activity. Failed/unavailable access or denied model terms stop gated preparation;
they do not remove existing usable public/cached models. Require both a valid token and
allowed Community-1 access before downloading or copying its files. Never infer access from
an old token's receipt. Settings GET and Activity polling remain read-only and do not trigger
downloads, network checks or repeated large cache scans.

## Exact configured model plan

There is no saved local ASR-model selection. The local plan is therefore the existing pinned
Community-1 speaker files plus the existing public coarse/da/en preparation needed for
supported word timing. Do not download every advertised Whisper/native/Runtime model or add
a new model chooser. Respect saved download/device/interpreter configuration; disabled or
unsupported local preparation is reported separately, not silently enabled. Use the selected
runtime's existing HF/NLTK cache targets for public prefetch; no new cache location or package
installation. Run local downloads in cancellable/drained children; the app interpreter can
host the HF helper while public inference prefetch uses the selected supported interpreter.
Any source bootstrap must bind the owned package path, never import a different checkout.

The UCloud plan requires an explicitly checked, complete saved UCloud connection and its
configured automatic-preparation policy. Freeze the exact connection/settings and enabled,
supported CBS Runtime ASR profiles with their existing pins, plus base/coarse/da/en and
requested speaker files. Native Transcriber model flags do not select CBS Runtime weights.
No configured UCloud connection or disabled preparation means a truthful skipped/needs-setup
stage, not a new allocation. Do not send HF/control tokens to each other's services.
At least one supported CBS Runtime profile must be enabled; a native-only configuration
reports the missing Runtime choice explicitly and does not allocate just from native flags.

Extend the existing durable preparation owner with an optional versioned frozen model plan;
absence keeps its legacy behavior. A base stamp alone cannot prove all requested caches.
Verify every planned cache, seed only verified gated files, preserve submission intent/epoch,
Stop/recovery and uncertain no-resubmission, and deduplicate only against a sufficient matching
plan. Follow ADR 0172's preparation/reader/retained-lock safety checks. The HF manager never
creates a second remote allocation owner. An attached existing preparation retains independent
controls; HF Stop can cancel a remote child only when this task actually created/owns it.
Explicit user Stop is sticky across later Save, supersession or shutdown while an accepted
handoff drains; those later events cannot erase that exact owned-child Stop intent.

Record the bounded versioned model-plan observation in that owner's existing journal after
successful prefetch, runtime checks and every requested cache check. No new shared-runtime
marker or table is needed. Later explicit Saves can reuse a matching nonarchived completed
owner only after fresh bounded checks of all current cache inventories/pins; a base stamp or
old receipt alone is insufficient. Restored archived owners remain inert. These are scoped
metadata/receipt observations, not cryptographic verification of every public weight byte.

The parent must check access and verify the exact pinned speaker files under its token fence
before accepting the durable child. That child owns the frozen public file/model plan and
may copy only those already verified files without reading/downloading with an HF credential.
Missing or changed files require failure/fresh Save, never borrowing a replacement token.
Access checked at this acceptance boundary is authority for this exact operation, not a
lasting general access claim. Distinguish accepting a durable local preparation task from
the later acceptance of a remote allocation; preserve the exact child link in either case.

## Fences, lifecycle and limits

Keep the HF parent's token fingerprint and plan-comparison state internal and in memory;
the credential-free frozen child model plan and its receipt intentionally persist in the
existing durable journal. No plaintext token or parent token fingerprint enters Activity,
jobs, logs, URLs or snapshots. Recheck the current credential/plan before each stage and
before publishing observations. A superseded task cannot
publish success or dispatch later mutations. Accepted requests drain before cancellation is
settled. Stop kills/drains local children; shutdown stops/drains the parent before the ordinary
queue while retaining the serving lease. An already accepted remote child remains durable and
recoverable after app close; no parent restart-continuation guarantee is made.

Compose manager admission/idle/exclusive guards with capture, library switching and shutdown.
Respect `create_app(start_worker=False)` so tests can accept an inert task without external
work. Report token Save, local preparation and remote preparation partial outcomes honestly;
there is no atomic transaction across credentials, caches and UCloud. Retry retains frozen
choices and refuses stale credentials/plan; changed choices require a fresh Save.

## Gate

Mock all access, model downloads and UCloud. Verify token/terms failure, new/unchanged/changed
tokens, dedup, cache reuse, configured/disabled/unsupported places, stale completion, Stop and
accepted-call drain, worker origin/environment secrecy, bounds, library capture/switch fences,
and server shutdown. Verify the optional remote model plan beyond base-stamp success, matching
owner attachment, lost replies and no duplicate/uncertain submission. Browser tests prove
Activity progress/actions and first-use partial outcomes without inline checks/downloads.
No real model downloads, preparation, private uploads or paid provider calls in this gate.

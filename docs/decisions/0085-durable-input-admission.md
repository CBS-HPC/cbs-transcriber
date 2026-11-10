# 0085 — Durable input acceptance and background materialization

Accepted 28 October 2026 for T87b, extending ADR 0075/0082. No new database schema is needed.
Existing queued producer rows hold strict versioned input intents until background inspection
and preflight produce executable params on the same job ID. HTTP accepts metadata only.

Accept every selected combination atomically with its inspector/cache metadata, settings,
features, explicit preparation consent and confirmation scope. Source inspection and admission
have bounded lanes and retain the serving lease through drain; expensive work stays outside
request/queue/database locks. Unready work acquires no remote allocation or membership.
Each lane has two workers. A blocked long inspection or optional runtime preflight must not
hold up a short, ready unrelated recording. Shared claim/epoch guards retain single ownership.

Native compatible selections freeze their complete member set/digest and materialize through
one barrier/transaction into one allocation. API/CBS Runtime may materialize ready members
independently. Apply current Protected and exact connection/model identity checks; later model
visibility changes alone do not cancel an accepted frozen request. Never broaden consent.
The barrier waits for every member's source/preflight outcome, not only a ready prefix. Valid
members may form one owner after that barrier; excluded members retain terminal outcomes and
resolved cohort evidence. They cannot ordinary-Retry into a second allocation after siblings
materialized. A new explicit confirmed selection remains distinct from that original cohort.

ASR is append-only and does not require unchanged transcript revision merely because an
accepted sibling created a result. Whole enhancement retains its exact accepted historical
version/hash and existing source-change semantics; switching view, later edits or Trash never
silently retarget it. Preserve existing reviewed-result activation and producer lineage guards.

Cancellation/retry bind explicit phase/epoch; stale intent actions cannot become allocation
Stop. Source retry never revives unrelated failed producers. Recovery resumes only proved
local inspection/materialization, never uncertain provider work. Expose safe readiness/action
tokens rather than raw params. Generic job dispatch cannot process an unmaterialized intent.

Preparation consent can be required only after background preflight. A phase/epoch/fingerprint-
bound Prepare action shows the exact recording or native-selection cohort, then creates a new
request from those frozen accepted choices and historical inputs. Preserve the immutable old
intent and an exact predecessor receipt. Only explicitly confirmed prepare_runtime may change;
never adopt current settings or enable allow_downloads. Native consent preserves one new
compatible selection barrier and excludes already materialized/successful siblings. Repeated
confirmed requests return the same successors; changed scope or current Protected authority
refuses. A successor receipt is continuation evidence, not successful transcription. Generic
Retry cannot acquire preparation consent.

Imports publish closed originals, recording/notes/collection metadata and media job together.
Precommit failure may clean only the newly owned folder; postcommit notification/snapshot
failure retains committed data. Preserve snapshot/queue lock order, without eager inspection.
Playback gates may be removed only for validated original-v1 executable work; legacy sources,
durations and checkpoints retain their behavior.

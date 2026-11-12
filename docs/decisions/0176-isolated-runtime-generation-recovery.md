# 0176 — Isolated Runtime generations without reclaiming old locks

Design reviewed 12 November 2026; disabled Stage 1 implementation in progress. The user
prioritizes Runtime stability. This is a separate protocol after the bounded observation fixes
in ADR 0175. Independent implementation review, complete synthetic boundary proofs and controlled UCloud acceptance
are required before enabling it. Read the older prototype as reference only; never import
its private Git ancestors into the public-descendant development history.

Stage 1 is limited to bounded registry/selection/path data, read-only snapshots and candidate
revision/CAS. It cannot promote an active environment, grant execution/capture authority or
accept a caller's readiness assertion. Those operations require the later durable-owner,
exclusive-folder, admission/carry, provenance/restore and live-acceptance boundaries below.

## Goal and identity

Prepare a new immutable environment without modifying, deleting or stealing the historical
runtime/lock. Preserve all older jobs, caches, owners and evidence. The old prototype cannot
be switched on: its settings row 9 conflicts with local concurrency, row 10 belongs to
connection checks, and its admission/HF/restore proofs predate current behavior.

Reserve and collision-check a separately numbered credential-free registry slot (11 is the
candidate; verify the complete current writer inventory before accepting it). Unknown/null
registry versions or a conflicting occupied slot refuse; never overwrite other settings.
Bound target/candidate/history counts and serialized sizes. No incidental schema migration.

Separate three identities: the stable base target (server/project/storage, base runtime ID,
pinned package requirements), the exact preparation operation recipe/worker/supervisor, and
the request's model/file pins. Do not describe a requirements-only target hash as proof of
the full recipe. Each accepted operation/receipt must bind the appropriate exact identities.

The user's later CPU-fallback decision applies to CBS Runtime as well as native Transcriber.
Generation selection consumes a trusted frozen compute decision; it does not inspect balances
or interpret machine congestion. Freeze the exact product/device/flavor before candidate
reservation or confirmation, never later in submit. Use distinct CPU and CUDA-12.8 base targets,
requirements identities and active pointers, preserving the legacy no-argument CUDA runtime
ID/stamp bytes. A CPU doctor must positively prove supported CPU execution/compute type and
the requested model plan; absence of CUDA is not CPU readiness. CUDA retains its existing
GPU/first-device-memory checks. Neither flavor can satisfy or mutate the other's target.
Wallet observation timestamps belong to the compute decision, not the cache target identity.
Carry both exact compute and generation through Auto, delayed admission, followups and Retry;
later funding changes never retarget accepted work. Full-run funding estimation and the pinned
CPU recipe/compatibility are separate proof boundaries before activation.

## Fresh preparation and ownership

An explicitly confirmed fresh-generation request binds the connection, base target, operation
recipe, resource/storage choices and registry revision. It keeps the old environment and may
consume additional storage/preparation time; an ordinary Retry is not fresh-generation consent.
Create a random generation under the app-owned Runtime namespace, with a durable create intent
before the exclusive REJECT-on-conflict folder call. Record ownership only after its exact
acknowledgment. Folder conflict or lost reply is not permission to adopt, repeat creation or
submit another job. Preserve unresolved intent for explicit recovery.

Use the existing durable preparation owner, Stop/capacity/epoch/submission protocol and
Activity card, not a second remote-job manager. Every mount, supervisor path, runtime stamp,
`.in-use` lock and model-cache path binds the frozen generation. Apply ADR 0172's bounded stamp
and known-reader rules and ADR 0173's exact public/model plan. Fresh gated-model transfer
requires the existing fresh HF access and exact-file acceptance guard; a previous completed
task's authority cannot be copied into a different preparation operation. Base/public-only
preparation may prove base readiness, never missing gated/model readiness.

A promoted generation is closed to package installation, model prefetch and model-cache
rewrites. If a request needs missing or different pinned models, require an explicitly
confirmed new candidate B; ordinary prepare/Retry/HF paths must not mutate active A. Reuse
only its verified prepared artifacts. Keep per-job outputs and any required ephemeral
compilation/telemetry caches outside the promoted environment. An append-only cache extension
protocol is outside this first slice and cannot be inferred from prior preparation consent.

Promotion requires the acknowledged folder ownership receipt, same recorded terminal
allocation/resource/mounts, matching generation/owner epoch, exact stamp and doctor/CUDA plus
every requested model-plan cache observation. One registry CAS publishes the active pointer
and prepared owner receipt atomically. Repeated acknowledgment is idempotent. Stop, unknown
submission, stale epoch/registry revision, wrong identity/pins or incomplete evidence cannot
promote. Promotion deletes nothing and does not recapture older requests.

## Selection is accepted once

At each true fresh request acceptance, seal a versioned selection envelope, including an
explicit legacy/no-active choice. The policy envelope itself cannot be null; absence denotes
an old legacy request. Unknown versions/null/forged or downgraded selections refuse. A new
request accepted with no active generation must not adopt one promoted during delayed source
inspection/materialization. A request accepted with A must not silently move to B.

Carry that exact selection through explicit ASR, local/cloud enhancement choice, Automatic
routing, delayed admission materialization, frozen producer followups, bounded repairs and
standalone/shared execution. Workers/helpers/retries read frozen selection only, never the
registry. Keep generation in compatibility/control/reconciliation identity and exact shell/
resource paths. Any not-yet-wired generation-bearing path must refuse rather than fall back
to legacy; activation stays disabled until all supported paths are covered. Old absent-policy
jobs retain byte-exact canonical paths, frozen requests and receipts.

## Evidence, restore and limits

Record truthful generation/base/operation/model bindings in the relevant source/capture
scopes. Batch identity binding alone is not proof for standalone/repair producers. Preserve
raw evidence and historical wording/speakers/timing/Working authority; reports remain bounded
and runtime/network-free. Repin captured sources only after the current contained/historical
checks pass, without increasing size caps incidentally.

Current restore already drops operational settings and archives preparation owners. Prove on
schema 19 that the source remains unchanged, the restored library has no active generation
pointer, its archived owners remain inert and historical generation-bearing results verify.
An operational registry is not restore/upload/cleanup or institutional approval authority.

## Required gates

Verify settings 9/10 preservation/collision refusal; duplicate confirms/CAS; exclusive folder
conflict, symlink/traversal/unknown metadata and lost-create reply; accepted/uncertain POST,
Stop/shutdown/reopen/late success; wrong allocation/mount/stamp/doctor/cache/plan refusal and
atomic idempotent promotion. Test legacy byte preservation, old/new batch separation, None→A
and A→B across every delayed admission/Automatic/followup/repair seam, producer capture and
current portable schema-19 restore/source pins. Exercise generated shell under stubbed Linux
commands while preserving existing ownership/reader lock tests. No real jobs or downloads.

Actual UCloud specification normalization, physical mounts/locks, CUDA/MIG memory, model
preparation/reuse and a real batch/Stop/recovery/cleanup remain separately authorized live
acceptance. The historical lock is retained throughout; terminal owner or age is never reclaim
permission. No new frontend controls or public activation are implied by this backend design.

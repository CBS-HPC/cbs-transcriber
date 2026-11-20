# 0176 — Isolated Runtime generations without reclaiming old locks

Design reviewed 12 November 2026; disabled Stage 1 implemented with independent Go and
[46 synthetic passes](../verification-2026-11-12-runtime-generations-stage1.md). The user
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

## Stage 2 owner/promotion slice — implemented disabled 18 November

Independent review approved the following disabled recovery slice. Freeze the exact Terminal
app and trusted MIG/CUDA compute before confirmation/reservation; unsupported CPU preparation
refuses. Insert the actual durable preparation owner and candidate atomically, retaining the
existing HF-plan, capacity, epoch and Stop rules. Require an already verified Runtime namespace;
fresh namespace creation is a separate unimplemented boundary.

Journal separate exclusive-create intents and acknowledged receipts for environment and public
run folders before POST. Conflicts/lost replies never permit adoption or another create/submit.
Drain accepted replies after Stop and retain evidence, but perform no later POST. Creation
identity and current observation epoch are distinct; late old replies cannot authorize a new
worker. New-generation execution branches before legacy-root idle/stamp shortcuts, checking
only the exact acknowledged generation; legacy behavior remains unchanged.

The sole `verify_and_promote(owner, epoch, client, deadline)` must read actual typed exact-ID
allocation/specification and generation-root stamp/doctor/cache/model-plan evidence. It accepts
no caller-ready payload. The writer rechecks the same observed operation/specification,
folders, owner/epoch/Stop and registry candidate before atomic owner-completion/pointer CAS.
Generic writers and legacy completion helpers anchor the current stored birth identity and
refuse generation removal, frozen/fingerprint downgrade and injected Ready/spec/path proof.

Known-ID recovery, including terminal SUCCESS awaiting verification, observes the same owner.
No-ID Retry after any create intent/ack, name-based generation reconciliation, successor
creation and environment cleanup remain refused in this slice. Supersession of a settled
failed candidate was added separately on 19 November (below).
No public API/UI, active-selection capture, inference admission, portable-source changes or
activation are included. Later carry/provenance/restore/live gates remain required.

Builder `e9dd23d` (452 passed, 3 Linux-only skips). Independent review `229865a` gave Go
for the disabled slice and No-Go for enabling until three defects were fixed: generic
writers compared protected proof with Python `==` (`False`/`0` and `1`/`1.0` passed),
`reconciled_not_created`/`no_post` were not reserved on generation owners, and owner
decoding compared stored records with the current compute policy, Terminal flavours and
script parameter, so a later version change made promoted owners unreadable and blocked
legacy preparation. Fix `600141f` compares protected values by canonical stored JSON,
reserves both receipts, decodes stored values by shape only and refuses an unsupported
policy/flavour at fresh submit before any folder or POST. Every entry point now requires
all three switches. Gate: reviewer file 67 passed (no xfail), builder files 84 passed,
legacy Runtime suites 285 passed/3 Linux-only skips. All three switches remain `False`.

Before enablement, three gaps remain:

- real UCloud create-folder reply shapes (`_acknowledged` accepts items without `id`);
- a frozen record of the Terminal app's script parameter (a fresh submit still builds with
  the current name);
- the shared observation's loose comparison with UCloud's normalised specification. The
  strict canonical replacement and the exact real-reply evidence it needs are in the
  [design note](../proposals/runtime-strict-specification-comparison-2026-11-19.md).

The fourth gap, failed candidates blocking their target forever, now has the disabled
supersession rule below. Independent review of that rule is also required before enablement.

## Failed-candidate supersession — implemented disabled 19 November

A failed candidate no longer blocks its target forever. The user explicitly replaces it with
a fresh generation request. That supersession is a separate authority from fresh admission,
Retry and reconciliation:

- **Eligibility: settled only.** The exact registry candidate must decode to its own
  non-archived generation owner, matching it by target, token, operation, compute and
  expected revision. The owner must be in `failed`, `cancelled` or `interrupted`, never
  promoted or already superseded, and hold no recorded open allocation, uncertain POST or
  reserved capacity (`runtime_store._reserved`).
- **What still blocks.** Queued, running, stop-pending and allocation-holding candidates are
  refused; Stop comes first. An uncertain POST may be a running paid allocation, so it keeps
  blocking its target until exact recovery exists. A missing, mismatched, archived or
  undecodable owner refuses.
- **What does not block.** Uncertain or conflicting folder intents and a pending run-folder
  cleanup concern only that owner's random paths. They stay on it as visible obligations and
  are never adopted, transferred, retried or released.
- **Consent.** `preview_supersession` (read-only, like `preview`) seals the fresh generation
  confirmation, the exact candidate and the retained evidence: the owner's state, job,
  remote state, folder outcomes, cleanup state and a digest of its exact row. `supersede`
  re-freezes everything outside the writer and again inside one `BEGIN IMMEDIATE` writer,
  under the old owner's control slot. Any type-strict difference refuses. A repeated
  identical supersession returns its recorded successor only while its frozen request is
  still current; it never creates a second successor.
- **One atomic writer.** The same transaction inserts the new durable owner (new ID and
  token, same expected revision), records the old owner's receipt (successor, supersession
  and observed digests, registry revision) with its epoch advanced to fence stale writers,
  and lets `runtime_generations.supersede_candidate` swap the exact candidate by registry
  CAS. The revision and active pointer do not change; only promotion advances them. Nothing
  remote is contacted, created, released or deleted.
- **The receipt is permanent.** `generation_supersession` is generation-only proof. Generic
  writers can never write it, and not even a trusted writer may alter or remove it. The
  decoder keeps a superseded owner settled: any write that would requeue, run, reserve or
  promote it fails decoding and rolls back. Retry and promotion refuse it, while cleanup of
  its own run folder stays available and never touches either generation root.

No public API/UI is added, and the slice's three switches still gate every entry point.

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

## Independent supersession review — 20 November 2026

Before enablement, observed remote closure is stricter than the historical shared capacity
classifier: when remote_state is present, only the pinned UCloud positively-final states
SUCCESS, FAILURE and EXPIRED qualify. SUSPENDED can resume and unsupported aliases do not
prove closure. Absence still requires all existing no-ID, no-uncertain-POST and no-reservation
proof. Both candidate settlement and the permanent retired-owner decoder enforce it (B484).
The broad legacy runtime_store terminal set is unchanged.

The supplied complete supersession body must canonically and type-strictly match its claimed
digest and policy before comparison with a recorded lost-reply digest. A reused digest does
not authorize substituted evidence. Exact replay returns the same successor; a changed or
retyped body refuses without rows/settings changes (B485).

This is a disabled offline hardening slice, not completion of Runtime activation. All three
generation switches remain false. Existing locks, old environments, remote folders, jobs and
cleanup obligations are untouched. See the dated verification receipt for gate boundaries.

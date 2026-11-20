# Native failed-output recovery and owned-folder inventory

Design proposal, 10 November 2026. This is **not an accepted ADR or implementation authority**.
No remote inventory, transfer, job creation, cancellation or deletion was performed while
writing it. It develops the missing-output item in the
[post-MVP plan](../work-plans/post-mvp-2026-11-10.md) and preserves ADRs 0021, 0045, 0092,
0101, 0100, 0149, 0155 and 0162. A later implementation must add a numbered accepted decision
and update SPEC/TODO before changing persistence or job semantics.

## Existing behavior and the gap

The failed batch's **Retry failed** action reconnects to the exact recorded allocation. It
retrieves and validates existing output, preserves accepted raw bytes and saved siblings,
and never repeats their ASR. Transient output listing/session/download reads have bounded,
cancellable retry (ADR 0162); authentication, missing output, malformed output, guard refusal
and Stop do not enter that retry path. `native_batch.retry_batch()` explicitly reuses frozen
ownership. An uncertain POST is not retried as a new allocation.

The missing feature is a separate, explicitly confirmed **new run for genuinely missing
files** after the old allocation has been proved terminal. A failed connection is evidence
of unreadability, not evidence of absence. A missing local result may already have exact raw
bytes ready for import. One failed member beside eleven saved members should first recover
from the original allocation; only a proved absent output is a candidate for new ASR.

## Proposed user flow

1. **Recover existing outputs.** Keep Retry failed's current same-allocation meaning. Before
   offering a new run, inspect accepted local raw/capture and producer receipts, then obtain
   current terminal job evidence and a complete bounded listing of the exact owned output
   folder. Run the existing exact UUID/model/language matching and normal parser/receipt
   checks. Save all usable outputs idempotently before producing a missing-only preview.
2. **Show a separate preview.** List saved siblings, recovered-now members, genuinely missing
   candidates and blocked members. Show the original allocation identity/state, the proposed
   new-run membership/model/language and the fact that it incurs another allocation/cost.
   Explain that old outputs and cleanup obligations remain. Default to no new run.
3. **Confirm exact missing membership.** A distinct action such as **Start a new run for these
   missing files** accepts the preview fingerprint and explicit confirmation. It is not an
   alternate interpretation of Retry failed, Reconnect, Stop, Clear finished or cleanup.
4. **Track the successor.** Activity links the old allocation and successor, shows each
   allocation's remote state/cleanup and each recording's result outcome. New results append;
   reviewed saved siblings remain exact. Lost HTTP replies rediscover the same accepted
   resubmission intent instead of creating another run.

The first iteration should accept a bounded whole preview's missing set, with no free-form
remote paths or invented per-file selection shortcuts. If partial choices are later useful,
the preview/fingerprint must bind the chosen subset exactly.

## Required classification before confirmation

| Observation | Treatment |
| --- | --- |
| Exact result already committed, including a producer result in Trash | Saved; never resubmit or restore implicitly. |
| Accepted exact local raw bytes but no committed result | Recover/import first through normal captured producer validation. |
| Exact valid remote output available | Download/preserve/import first; no new run. |
| Nonterminal remote state | Wait/reconnect or explicitly Stop; no missing-only confirmation. |
| Timeout, permission/authentication error, incomplete pagination, unreadable folder or uncertain state | Unavailable/blocked, never missing. |
| Terminal exact allocation, complete current owned-folder listing, no exact candidate and no local receipt/raw result | Missing candidate, subject to source/current-admission checks. |
| Ambiguous name, corrupt/malformed/wrong-model output or conflicting capture | Blocked/unusable output with diagnostics; not silently reclassified as absent. A separate policy would be needed to retranscribe it. |
| Uncertain submission without a remote ID | Resolve exact-name ownership or explicit absence using existing controls; no speculative duplicate. |

Absence remains an observed fact with a timestamp and evidence binding, not a permanent
claim about provider storage. Immediately before admitting a new run, repeat the same
terminal/ownership/output checks. If the provider cannot supply sufficiently fresh complete
evidence, refuse the new-run action and retain manual recovery. An old cached status or a
single 404 is insufficient.

## Authority, identity and durable state

The preview binds exact library/project/recording/job/member IDs; old batch and remote ID;
frozen endpoint/account/compute/app/model/language; local result/capture/raw observations;
terminal and listing evidence; the missing set; source identities; and independent content,
Working-role and deletion revisions relevant to the action. Credentials and arbitrary
provider response bodies never enter the preview or fingerprint.

Admission takes the existing allocation/job control reservation, excluding concurrent
Retry/Stop/Delete/disposition while preparing and accepting the action. Network checks stay
outside queue/snapshot/writer locks. Re-enter the normal lock order for a short transaction
that rechecks all local ownership, current policy/Protected approval, source identity and
preview fences before publishing one immutable accepted intent. A stale fingerprint returns
a fresh preview; confirmation does not carry across changed members or changed obligations.

Use a **new durable successor intent**, not a reset of the old batch or its frozen settings.
It records the old allocation/member lineage, confirmation identity, exact missing-only
membership and frozen original request. Fresh native admission checks the current compatible
compute/template, available source, provider enablement/approval and accepted source policy.
If a frozen old request cannot be newly admitted unchanged, block and explain it; silently
retargeting to current settings would describe a different request. The eventual accepted
design must decide how a user explicitly chooses that different request.

Request replay is idempotent by the accepted intent/confirmation identity. The new allocation
has its own durable submission-intent checkpoint before POST, frozen capture policy and remote
journal. It inherits no permission to replay an uncertain POST. Restart/lost-reply recovery
must find zero or one successor for the accepted intent; another confirmation cannot bypass
an unresolved successor. One compatible confirmed set creates at most one native owner,
with normal capacity limits and member outcomes. The old allocation's journal remains intact.

## Results, siblings and lineage

Preserve all old results, versions, source files, notes, manual review, named voices and
Working pins. Producer idempotency still applies when a result was moved to Trash; a new-run
action must not resurrect it. Every new result's producer is the successor job/allocation;
its retry-of relationship points to the precise predecessor member and confirmation. Do not
pretend it is another download of old output or change `source_version_id`'s existing meaning.

If an old output becomes available after confirmation, recovery must not race admission or
silently produce two submissions. Recheck before the successor POST; if an already-accepted
submission is uncertain or running, preserve its ownership and show the overlap rather than
discarding evidence. Exactly how late old output is retained/imported is a required
implementation policy; the conservative default is preserve it separately and require an
explicit result choice, while never overwriting either result or hiding a charge.

## Cleanup remains independent for each allocation

The old and new allocations have separate exact owned folders, terminal observations, local
preservation receipts and cleanup journals. A new result does not prove that every old output
was preserved. Successful successor completion does not authorize old-folder disposal.

Normal success cleanup keeps its existing receipt/terminal rules. Old missing/failed-output
disposition requires a separate displayed, fingerprint-confirmed decision after bounded
available-output preservation. Complete unsaved output blocks disposal until recovery. Show
inputs, outputs, metadata and retained/unverified artifacts, with their allocation association.
Consent is for those exact folders, not every future folder under the project root.

Hold the allocation's control slot through cleanup/disposition. Record pending obligations
before remote change and retain failed journals/cards beyond Clear finished and local record
deletion. Move only exact app-owned folders to UCloud Trash and verify absence by parent
listing. Never purge Trash. Stop, Retry failed, a new-run confirmation and a terminal remote
state provide no independent output-disposal authority.

## Optional owned-root cleanup inventory

The requested broader inventory concerns the configured owned root, exemplified by
`/<drive-id>/CBS-Transcriber/`. It must be a separately reviewed flow, starting read-only.

- Bind the exact server, project/drive and configured root; normalize/reject traversal,
  malformed paths and unexpected root changes. Do not accept browser-supplied arbitrary
  paths as authority. Use complete bounded paginated direct-child discovery, with explicit
  refusal when it cannot be completed.
- Exclude the exact `cbs-runtime` subtree before descending and never classify shared
  runtime/model folders as allocation cleanup. Preserve `.in-use`/preparation locks and
  unknown shared folders. Do not infer ownership from a familiar prefix or folder age.
- Cross-reference every candidate against all current and retained native single/batch,
  Runtime, repair, uncertain-submission, deletion-release and cleanup obligations, including
  owners outside recent Activity history. An active/recoverable/unreadable obligation blocks
  ordinary selection; provide the corresponding recovery path.
- Display exact folders/files, safe counts/size availability, tracked owner/remote state,
  saved/raw/unsaved output status, cleanup eligibility and every exclusion. Treat untracked
  folders as **unknown, not selected**. Importing their ownership or disposing them needs a
  separate explicit policy and proof, not a select-all shortcut.
- **Select all eligible** means only the displayed, fully proved eligible folders in that
  preview, not a recursive root delete. Bind confirmation to the complete inventory,
  exclusions and obligations; re-list and recheck under control reservations before each
  operation. A changed/new item invalidates the relevant plan instead of widening it.
- Use existing owned-folder Trash/absence verification and independent durable failures.
  Never remove the root, runtime subtree, unknown siblings or UCloud Trash itself. Tokens,
  transcript bodies, private paths from other accounts and free-form remote diagnostics do
  not belong in UI/exported inventory evidence.

The inventory must not create jobs, upload/download private media or perform disposition
while being previewed. A successful read-only inventory is not deletion permission.

## Explicit ongoing-work safeguard — 12 November

The user emphasized that broader cleanup must not delete ongoing activities or their data.
Keep this flow design-only. Queued/running owners, uncertain submissions, unfinished
retrieval/import, complete unsaved outputs and unreadable recovery evidence are excluded,
even when a local card says Failed or Cancelled. A newly accepted job or changed membership
after preview invalidates cleanup eligibility; recheck under the allocation control reservation
immediately before Trash. Incomplete status/inventory never means idle or disposable.

Remote cleanup must not cancel jobs, erase their local Activity owners, hide ongoing cards,
remove recovery controls or discard unresolved journals/provenance. Settled status may update
through existing rules; Clear finished remains a separate user action. Preserve shared
Runtime/model paths and all unknown ownership. Required adversaries include remote RUNNING
with local Failed, queued work, a new member after preview, interrupted import and failed
cleanup after local deletion. Previewing this design performs no remote mutation.

## Smallest offline implementation sequence and gate

1. Add a pure classification/preview prototype over supplied synthetic local/remote
   observations. Exercise complete versus truncated listings, terminal versus unavailable,
   accepted raw, Trash results, malformed/conflicting output and untracked folders.
2. Review the precise successor intent/idempotency/lineage and late-output policy in an ADR.
   Test stale confirmations, changed approval/source, concurrent Retry/Stop/Delete and
   lost replies before exposing a new-run endpoint.
3. With mocked provider clients only, prove eleven saved siblings plus one missing member
   create one successor containing just that member. Cover existing-output-first recovery,
   pre/post-POST crashes, uncertain submission, restart, double-click, cancellation and
   unchanged sibling edits/Working/Trash.
4. Prove independent old/new cleanup, retained failures, complete-unsaved-output refusal,
   excluded `cbs-runtime`, unknown/shared/active folders, incomplete pagination and inventory
   changes. Verify no arbitrary path, root delete, Trash purge or accepted-request leak.
5. Only after review, define separately authorized tiny public/synthetic-provider acceptance
   with explicit budget, expected POST count, exact allocations and cleanup authority.

No production patch or live acceptance follows from this proposal. Existing same-allocation
recovery remains the correct current action for unreadable or already-produced output.

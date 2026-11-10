# 0169 — Versioned invalidation of source alignment-fit hints

10 November 2026. Accepted for an isolated backend implementation on
`codex/post-mvp-timing-hints`, based on checkpoint `7cf165c`. Not merged, installed or released.
The [reviewed proposal](../proposals/legacy-timing-hints-2026-11-10.md) records the reproduced
unmarked/partial-map defects and independent design corrections. Extends ADRs 0137, 0068,
0159 and their exact history/Working/receipt boundaries.

Implementation and independent review are complete on the isolated branch. Its exact
non-browser inventory has 5,574 passes and 26 explicit skips; see the separate
[verification receipt](../verification-2026-11-10-timing-hints.md). This is not live-provider,
platform or installed-app acceptance.

## Decision

Fresh verified timing enqueue freezes the host-only policy
`timing_apply_policy="invalidate-source-alignment-fit-v1"`. Mint it only in the two exact
source/role-checked fresh acceptance transactions: `Store.enqueue_enhancement()` and
`Store.add_job(..., capture_timing_role=True)`, for `realign=True`. Reject caller assertions.
Do not default it in shared insertion, workers, Retry, follow-ups, recovery or Apply.
Generic mutation preserves both presence and value from creation; unknown present policies
refuse. Absence selects exact legacy behavior.
Apply must also verify a host birth commitment to that exact policy generation, presence and
value. A matching parameter alone, or a legacy authority row minted before this policy,
cannot establish new-policy authority. Existing v1 authority bytes stay valid for v1 only.
The fresh authority stores a canonical `cbs-timing-job-authority-v2` envelope in existing
`working_job_authorities.role_json`, containing the unchanged validated role plus the exact
timing policy. This needs no schema migration. Keep old plain-role authority bytes/semantics;
host Apply/Working and contained authority readers validate both forms without treating a
legacy row as the new seal. Check the seal even when a tampered parameter key is absent.

New-policy local and cloud host Apply use a shared policy-aware successor after their existing
proof/source/current-map/terminal/revision fences. An actually replaced passage receives the
host-authored passage marker `alignment_hints_stale=True`, validated as literal true when
present. Do not stamp skipped/unselected passages or rewrite wording/speakers/bounds outside
existing rules. Keep existing word-marker handling; partial maps must also invalidate old
hint spans for omitted words. Never accept a producer's host-authority claim.

The hints reader suppresses the whole passage's immutable creation alignment-fit word scores
when this marker is present. Recognition confidence and independent passage metrics remain;
source/raw hint documents are unchanged. No new score, model invocation, time estimate,
backfill or frontend control follows from this policy.

## Timing adoption and exact generations

Fresh safe enhancement merges and explicit conflict timing adoption use new stored policy/
format generations. Every actual changed timing adoption invalidates the destination,
regardless of incoming marker presence. Carry invalidation conservatively through relevant
structural operations; marker-only/no-op changes cannot inflate timing-update counts.

Retain explicit legacy local, cloud, merge and conflict transforms. Replay old stored versions,
canonical successors and lost replies using their stored generation, never the newest module
constant. Unknown generations refuse. Public conflict projections must label stored v1/v2
truthfully. The absence of a job policy never upgrades a frozen request.

New local Apply/capture receipts bind the policy and exact successor in a new generation.
Host Working proofs, reports and contained local receipt checks dispatch both generations;
old hashes/ownership/receipts remain exact. Cloud host Apply/reopen/lost-reply proofs require
their actual cloud transform, including separately proven legacy behavior. Cloud historical
transformation remains explicitly `not_checked`: the local `timing_capture` kernel is not a
cloud receipt verifier. Ordinary merge report parent-only checks remain honestly scoped.

## Gate and constraints

Reproduce both hint defects first. Verify fresh local/cloud full/partial application, omitted
words, skipped dictionaries, caller/mutation refusal, stale source/map, revision rollback,
Working changes, reopen/lost reply, safe/conflict timing adoption, metadata/no-op behavior and
exact legacy v1 payloads/receipts/projections. Preserve the distinction between confirmed local
defects and the proposal's source-traced cloud suspicion; reproduce cloud cases before claims.

Repin changed local timing capture and contained worker bytes in both pin tables, with old/new
kernel regression and resource gates. At decision time `transcripts.py` had only 223 normalized
bytes of headroom under 64 KiB. The proven right-passage join carry needs 98 bytes, leaving
125; prefer other policy logic outside it and do not raise the cap incidentally.
No persistence schema migration, serving environment change, paid/provider execution,
preparation, downloads, private data or automatic second-pass alignment is authorized.

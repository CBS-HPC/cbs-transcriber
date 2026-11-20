# Alignment-fit hints after same-result timing replacement

10 November 2026, design only. This is a confirmed backend correctness gap, **not solely a
legacy-data issue**: a fresh local realignment of an unmarked result can show the original
model's alignment-fit hints against its replacement timing. ADR 0137 explicitly left the
unmarked case open. No production writer, reader, portable pin, browser or real data was
changed in this investigation. Root integration owns the SPEC/TODO and B419/T133 entries.

## Confirmed failure and small reproduction

The current chain is:

1. `transcripts.hints_document()` stores creation hints with a passage text hash, separately
   from transcript payload/history. `word_kind="alignment"` distinguishes alignment fit from
   recognition confidence.
2. `local_timing._candidate()` replaces the words. It marks replacement words
   `alignment_hints_stale=True` only if the frozen source already has a `manual_timing` or
   `alignment_hints_stale` word. An ordinary unmarked source receives neither marker.
3. `review_hints.current_hints()` checks the unchanged text hash, then
   `_current_alignment_hints()` suppresses only hints overlapping marked current word spans.
   An unmarked replacement therefore retains the old creation scores.

Run this with the existing Python 3.13 environment and this worktree's `src` on `PYTHONPATH`:

```python
from copy import deepcopy
from cbs_transcriber.transcripts import normalize_segments
from cbs_transcriber.local_timing import _candidate
from cbs_transcriber.review_hints import _current_alignment_hints

source = normalize_segments(
    [
        {
            "text": "Hej",
            "start": 0,
            "end": 1,
            "words": [{"word": "Hej", "start": 0.1, "end": 0.5}],
        }
    ]
)[0]
words = deepcopy(source["words"])
words[0].update(start=0.3, end=0.7)
successor = _candidate(source, {"words": words, "word_timing": True})
hints = {"word_kind": "alignment", "words": [[0, 3, 0.1]]}
observed = _current_alignment_hints(hints, successor)
print(observed["words"])
assert not observed["words"], "Old alignment fit survives timing replacement"
```

Executed as `python -c` through the existing interpreter: **expected red in 0.70 s**;
printed `[[0, 3, 0.1]]`, followed by the stated assertion. This is an isolated diagnostic,
not a knowingly failing test committed into the product suite. The source interval was
`[0.1, 0.5]`; the successor interval was `[0.3, 0.7]`.

Positive controls were also executed: a manually marked source produces stale-marked
replacement words and suppresses its overlapping old alignment scores; transcription
confidence remains visible; changing the passage wording makes `current_hints()` remove
the passage through its text hash. These controls locate the missing timing-currentness
proof rather than suggesting that every hint or text guard is broken.

**Partial-output edge, independently reproduced:** take `Hej igen`, mark source `Hej` as
manually timed, then apply a valid partial output containing only `Hej` with
`word_timing=False`. The new `Hej` is stale-marked, but the old `igen` hint at `[4,8]`
survives because no current word overlaps it. This pure diagnostic was expected red in
0.94 s, printing `[[4, 8, 0.2]]`. `_valid_timing()` permits valid partial maps; adding
markers only to words actually returned by the new aligner cannot settle whole-passage
hint applicability.

## Existing coverage and compatibility constraint

`test_manual_word_timing.py::test_later_local_alignment_cannot_reanimate_old_alignment_hints`
covers a *marked*, complete replacement. Its neighboring transcription-confidence test
covers the distinction between the two hint types. Neither test covers an ordinary unmarked
source or a formerly hinted word omitted from a partial replacement. The existing cached-hint
test verifies one current result read, ownership and changed text, rather than timing identity.

Changing `_candidate()`'s default would change serialized successors for old jobs:

- `local_timing._existing()` reconstructs exact predecessor + frozen source + output and
  compares the saved payload and applied/skipped IDs on lost-reply Retry.
- `timing_capture.verify_historical()` performs corresponding bounded reconstruction for
  reports from captured history. It must continue validating old versions exactly.
- `timing_working._timing_receipt()` proves automatic Working advancement across local and
  cloud timing successors. It calls `_successor()` too.
- `result_conflicts.verify_historical()` reconstructs explicit timing-adoption successors
  with `review_payload()`. Changing that function's default would invalidate existing
  conflict-review history. `result_merges` also needs recorded generation identity, not
  merely a new default inside its pure timing merge.

Missing policy must therefore keep the current transformation byte-for-byte. A queued old
job does not become a new-generation job merely because Apply happens after an upgrade.
An already applied receipt must never be rewritten to make a new algorithm accept it.

## Cloud path: source-traced investigation, not reproduced defect

`Store.apply_repair_timing()` currently updates selected word dictionaries directly; it
does not call `_candidate()`. That assignment appears capable of dropping existing per-word
manual/stale markers. Meanwhile the cloud branch of `timing_working._timing_receipt()`
reconstructs with `_successor()`, whose marked-source behavior can differ.

This investigation **did not execute a marked-source cloud Apply/reopen/lost-reply case**.
Do not count that apparent mismatch as a second confirmed product defect or rewrite old
cloud history on this source observation. The implementation gate below must exercise it
with the existing synthetic `test_repair_apply.py`/`test_repair_cloud.py` fixtures and their
real host writer. Fresh policy dispatch still needs explicit local and cloud branches.

**Cloud history scope stays limited.** `timing_capture.eligible()` expressly excludes
`repair_cloud` and other remote work. Local reports enter its verifier through
`context.timing_artifacts`; cloud Apply currently records its job ID without that local
artifact receipt. Repinning the local capture kernel does not validate cloud timing
transformations in reports or portable packages. This minimum slice verifies cloud host
Apply, exact host receipt/reopen/lost-reply behavior and Working proofs, while keeping
**cloud historical transformation validation `not_checked`**. Designing a new captured
cloud receipt/verifier is a separate, larger task.

## Proposed decision: fresh Apply policy, whole-passage hint invalidation

Use a separately named host policy, for example
`timing_apply_policy="invalidate-source-alignment-fit-v1"`. Its name/value is a proposal,
not a newly accepted protocol. Keep it separate from language, model timing estimates,
producer capture and Working-role policy.

**Freeze only at fresh, verified timing enqueue.** One helper belongs in both existing
fresh acceptance transactions: `Store.enqueue_enhancement()` after exact source freezing,
and `Store.add_job(..., capture_timing_role=True)` after its exact-source check. Both must
require `realign=True` and verified timing ownership. Reject caller-supplied policy claims.
Do not install defaults in `_insert_job()`, worker start, Retry, recovery, frozen follow-ups
or Apply. Generic job mutation must preserve presence and exact policy value from creation.
Unknown present policies fail closed; absence dispatches legacy behavior.
The shared insertion guard may validate an internally verified policy, but must not mint it
or accept it from an ordinary caller. Preservation checks apply before completion as well
as after an applied receipt exists; removing the key is not a downgrade to legacy authority.

**Dispatch explicitly, preserving two legacy transforms.**

| Frozen policy | Local Apply/reconstruction | Cloud Apply/reconstruction |
| --- | --- | --- |
| Absent | Exact existing local `_candidate`/`_successor` behavior and v1 receipts | Exact existing cloud transform/receipts; prove old replay separately rather than silently substituting the local algorithm |
| Accepted new value | New policy-aware successor | Same new policy-aware successor after cloud proof/terminal/fingerprint checks |
| Unknown/malformed | Refuse before writing | Refuse before writing |

The new successor keeps all current source/ownership/revision/current-map guards and
applied/skipped behavior. It changes no wording, speaker, passage bounds except the existing
explicit unbounded rule, originals, producer/source lineage or unselected dictionary. It
never sets `manual_timing` on model output and still refuses producer-asserted host markers.

For each actually replaced passage, stamp a host-authored passage-level invalidation marker,
for example `alignment_hints_stale=True`, in addition to preserving required existing marker
behavior. The exact marker must be validated as literal `True` when present. The hint
projection must suppress **all creation alignment-fit word scores for that passage**,
including spans absent from a partial new word map. Per-word markers alone are insufficient.
Retain recognition-confidence and independent passage metrics. Leave the immutable creation
hint document/raw artifacts intact; do not substitute newly computed scores without an exact
new producer-binding design. Unselected/skipped passages receive no new marker.

`review_hints._current_alignment_hints()` recognizes the new passage scope before applying
existing per-word manual suppression. Its public metadata can report a fixed reason such as
`timing_replaced`; do not require a frontend change for the backend slice. Preserve existing
changed-text suppression and conservative behavior for malformed evidence. Confirm that
metadata-only edits, restore, merge/split and later timing keep the invalidation conservative;
no operation may reanimate scores merely by dropping the new marker.

## Timing adoption and enhancement merge are part of the required slice

Independent review found a concrete hole in the earlier proposal: adding a passage marker
only in Apply is insufficient. `transcript_merge._timing()` excludes it, and
`merge_payload()` copies only bounds, words and timing flags into the destination. An
incoming passage-level invalidation can disappear during an explicit same-text timing merge.
The reviewer reproduced this with a sub-second pure probe: partial incoming `Hej` timing
for unchanged destination text `Hej igen` leaves the old `igen` alignment hint applicable
unless the destination is invalidated. `result_conflicts.review_payload()` has a separate
explicit incoming-timing assignment with the same limited copied fields. Its historical
verifier is an additional version-sensitive caller.

The accepted design must therefore include a **fresh host timing-adoption generation** for
both automatic-safe enhancement merge and explicit conflict-review timing adoption:

- Preserve `merge_payload()` and `review_payload()` absent-policy/v1 behavior exactly. Add
  explicit known-generation dispatch; unknown generations refuse. Do not change the default
  implementation used to reconstruct old history.
- For a fresh preview, choose the new policy on the server and bind it into the exact
  snapshot fingerprint. Apply recomputes the same generation under its current writer
  transaction. A held v1 preview cannot silently become a v2 Apply; it must fail its
  fingerprint and refresh, or use an explicitly supported v1 path. Clients cannot assert
  arbitrary policy, marker or merged payload.
- Record the policy in versioned server-authored merge/conflict metadata, for example fresh
  `cbs-enhancement-merge-v2` and `cbs-result-conflict-review-v2`, with a fixed accepted
  `timing_adoption_policy` value. Extend strict `history.merge_metadata()` and
  `conflict_review_metadata()` validation to both exact generations. An absent key/old
  format retains the old meaning; adding a policy to an old record is not allowed.
- Whenever incoming timing is **actually adopted and changes the destination timing map**,
  invalidate the destination's creation alignment-fit hints at passage scope. This applies
  even if incoming has no invalidation marker, because the destination scores belong to
  its own earlier producer/map. Simply copying an incoming flag is insufficient. A
  speaker-only action, unchanged timing, a refused/conflicting timing choice or a skipped
  passage must not acquire a new invalidation. Existing destination invalidation must never
  be cleared by an unmarked incoming passage.
- Carry/validate the marker as explicit timing provenance. Separate current-map comparison
  from marker-only metadata comparison so a marker alone cannot masquerade as an audio
  timing change. Any same-map conservative marker carry must be stated and tested without
  inventing a timing-update count. Preserve literal wording, originals, named voices,
  producer meaning and every unselected dictionary.
- Make `result_conflicts.verify_historical()` dispatch from the **stored** metadata generation
  and reconstruct exactly that successor, including destination invalidation. Never use the
  newest default for v1 records. Old and new pure merge fixtures must retain exact canonical
  successors, parent references, summaries and generation-bound fingerprints.
  `result_conflicts.public_projection()` must likewise project the stored generation: its
  current module-level `FORMAT` output cannot relabel historical v1 as the newest format.

Ordinary enhancement-merge reporting currently validates parent identity/ownership through
`research_reports._merge_evidence()`; it does **not** reconstruct the complete `merge_payload`
transformation. The minimum slice preserves that narrower historical scope, teaches the
metadata reader the new generation and tests old/new host merge outputs. Do not advertise
full ordinary-merge historical transformation validation from a generation/pin update.
If such validation is added, it needs an explicit bounded predecessor/base/incoming/creation
reconstructor with v1/v2 dispatch and its own corruption tests. Explicit conflict review
already has full host reconstruction and must retain it in this slice.

**Bind the transformation into receipts.** Use a distinct new local Apply receipt generation,
and an explicit policy/format on fresh cloud receipts. Include the accepted policy in the
new-generation input hash/context and exact reconstruction. Keep old v1 input hashing
(`source` + `output`), strict field sets and old cloud fingerprint behavior unchanged.
Require receipt/job/version policy agreement. Include the new key in cloud frozen-field
identity/fingerprint preservation only when present, so absent-policy identities remain
exact. Normal writers cannot add/remove/change it or replace an applied output/receipt.

This adds JSON behavior/persistence semantics and needs a numbered ADR, not necessarily a
SQLite schema change. It prevents newly accepted work from producing the confirmed gap.
**It does not retroactively correct already stored unmarked results or old queued jobs.**
Historical display repair requires a separate, bounded read-only policy proving creation
map versus current map from verified versions; do not invent markers or rewrite history.

## Affected paths and captured pins

| Path | Required responsibility |
| --- | --- |
| `store.py`, fresh timing acceptance + both Apply paths | Freeze only fresh policy; preserve source/current-map and atomic writer fences; retain separate legacy cloud behavior |
| `local_timing.py` | Policy-aware fresh successor/input hash/receipt; exact absent-policy `_candidate`, `_successor`, `_existing` behavior; generic mutation preservation |
| `timing_working.py` | Dispatch exact local/cloud successor generations during Working proof, including lost replies |
| `repair_cloud.py` | Preserve/bind the new host policy in frozen identity and application fingerprints; old absent-policy bytes unchanged |
| `review_hints.py` | Whole-passage suppression for the fresh marker; retain text hash, per-word manual suppression and confidence distinction |
| `timing_capture.py` | Accept/reconstruct the new local receipt generation; maintain exact v1 historical validation |
| `transcript_merge.py`, `result_merges.py` | New fresh merge/timing-adoption generation; invalidate destination on changed timing adoption, generation-bound preview/Apply; keep v1 pure transform exact |
| `result_conflicts.py` | New explicit timing-adoption generation and exact stored-generation historical reconstruction; preserve v1 payloads |
| `history.py`, `research_reports.py` | Strict old/new merge/conflict metadata and honest scopes: ordinary merge parent checks, conflict reconstruction, local timing receipt validation, cloud timing transformation `not_checked` |
| Other structural transforms | Verify marker validation/carry; change only when a concrete loss is reproduced |

`timing_capture.py` is captured source. If it changes, recalculate **TIMING_SHA256** in both
`portable_semantics.py` and `portable_semantics_worker.py`. Update the contained worker's
`timing_receipt()` generation/input-hash validation; its own change requires repinning
**WORKER_SHA256** in `portable_semantics.py`. Keep resource caps and both old/new generations
tested. The contained timing kernel is used for metadata/reference/inventory checks; the
worker does not import current `local_timing.py` to run host `verify_historical()`. Do not
claim that repinning alone adds full host transformation validation inside containment.
Its local timing receipt adapter must not be presented as a cloud timing receipt verifier.

There is currently no captured `transcript_merge.py`/`result_conflicts.py` transformation
kernel or dedicated merge SHA pin. Changing those host modules, `result_merges.py`,
`history.py` or host report dispatch does not by itself require inventing a merge pin or
prove contained merge semantics. Retain the portable domain-transformation `not_checked`
scope. If an existing contained metadata adapter changes to recognize new generations,
repin its changed worker through `WORKER_SHA256`; adding captured semantic merge validation
would be a separate protocol/source-budget expansion, outside this minimum slice.

No transcript source edit is required merely to use existing word markers. If the new
passage marker's validation/carry requires changing `transcripts.py`, additionally repin
**TRANSCRIPTS_SHA256** in host and contained worker and check its existing normalized source
cap before changing anything. Current observed size is **65,313 / 65,536 bytes**, leaving
only **223 bytes**. Prefer a host-side helper outside this captured source where appropriate;
do not raise the cap as an incidental workaround or weaken validation to fit it.
Changing only `review_hints.py` has no captured-source pin, but cannot by itself establish
the fresh writer/receipt policy. No remote worker/model pins or model preparation follow
from this host-only policy.

## Smallest safe implementation slice and exact gate

Implement the accepted policy and regression tests together, without UI work, backfill or
real inference. The slice is complete only when these deterministic cases pass:

1. Fresh local **and cloud** Apply from an ordinary unmarked creation-hint result: timing
   changes, all old alignment word scores disappear, confidence/passage metrics remain,
   unselected dictionaries and named voices are exact. Repeat with partial output omitting
   an old hinted word, nullable word bounds and selected/unselected mixed outcomes.
2. New-policy marked/manual source: no output is called manually timed; invalidation survives
   repeated timing and ordinary restore/structure/metadata operations. Forged producer
   markers, unknown policy and normal mutation of the frozen policy refuse before writes.
   Include fresh safe merge and explicit incoming timing adoption: partial output omits an
   old hinted word, incoming has and lacks the marker, destination already invalidated,
   same-map/marker-only input, speaker-only input and conflicting/skipped timing. Changed
   timing adoption invalidates the destination; no-op/speaker-only actions keep honest counts.
3. Local and cloud changed wording/current timing map: existing skip/stale behavior remains;
   no marker is stamped onto an unapplied target and no reviewed wording changes.
4. Old missing-policy local **and cloud** jobs: frozen params/outputs/identity hashes and
   canonical successor bytes match the old implementation, before and after reopen. Existing
   already-applied receipts replay without a new version or revision, including a simulated
   lost snapshot/HTTP reply. Do not assert a cloud legacy marker case is sound until it is
   actually exercised; retain any discovered incompatibility as its own bounded finding.
5. New-policy local/cloud lost replies: one exact successor and one receipt, successful
   reconstruction/reopen, unchanged Working event/role revision on Retry; changed explicit
   Working choice still refuses. Receipt-write failure rolls back version/mirror/job together.
6. Old/new **local timing** historical reports and synthetic portable package validation:
   exact local receipt generation/policy/input hash/parent ownership; altered marker/policy/
   version hash fails at the appropriate existing host or contained check; v1 remains valid.
   Cloud host Apply/reopen/lost-reply is checked, while cloud historical transformation
   validation explicitly remains `not_checked`. No local capture check stands in for it.
7. v1/v2 merge and conflict-review snapshots: new previews bind the generation; stale/unknown
   policy cannot cross Apply; v1 canonical successors remain exact. Reopen and report a
   historical v1 conflict review after v2 exists, then verify a v2 successor and reject an
   altered policy/marker/payload using the proper generation. Ordinary merge reports retain
   honest parent-only validation unless their separate full reconstructor is implemented.
   Public projections label each stored generation truthfully, including old v1 after upgrade.
   Host/captured source pins agree; normalized source caps stay unchanged.

Independent design re-review gave **Go** after the merge/adoption and cloud-scope corrections.
This is approval of the isolated design, not implementation, activation or acceptance. The
reviewer's final projection-generation note is included above. Production work still needs
the accepted ADR, versioned implementation and complete bounded regression gate.

The current reproductions establish a real unsolved backend gap. They do not justify a
default `_candidate()` edit, an old-history rewrite or an independent editor redesign.

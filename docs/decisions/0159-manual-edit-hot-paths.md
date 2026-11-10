# 0159 — Remove repeated work from manual editing

Accepted 9 November 2026 after the exact activated rc3 speed audit found long-transcript
mutation delays. Keep the database, immutable history, Working authority and synchronous
project snapshots. Optimize measured repeated work without creating a projection cache.

Structural readers may reuse history.verify_payload's existing bounded, digest-only format/
canonical-integrity memo. Every use still hashes the actual current bytes, then parses fresh
mutable objects. The memo grants no result ownership, context, predecessor, current revision,
inverse, lifecycle or budget authority. Those checks continue on every request.

Targeted Undo merge/split Apply may stop its newest-first scan after its requested inverse is
fully verified under all used/newer-row checks. Older unrelated proofs cannot invalidate that
already selected merge/split candidate in the existing algorithm. Full advisory discovery and
deleted-passage restoration retain their complete bounded scans and lifecycle barriers.

Serialize completed supported JSON-native recording mutation responses with the standard
JSONResponse, retaining exact wire/status/header behavior. Keep the legacy encoder fallback for nested
_sa-prefixed keys and native-render failures: the old framework deliberately omitted those
keys, including hidden values it could not otherwise render. Do not change GET/global response
handling. Serialize outside semantic-error catches so error classifications remain unchanged.
An incidental extreme-depth failure threshold of the old Python encoder is not a new payload
validation rule; do not add another ordinary recursive walk to reproduce that runtime limit.

Store mutation inputs retain strict list/dict, finite-value, mixed-key and UTF-8 refusal
before a write. Consolidate discarded eager canonical encoding into strict row serialization.
Only after unchanged active-head verification may exact equality of both raw recording/result
mirrors skip duplicate canonical mirror work. Differently formatted legacy mirrors keep the
canonical fallback. The in-transaction history writer still recomputes authoritative payloads
and hashes; snapshots/errors are not deferred. Projection key order can change, while decoded
values and immutable canonical payload/hash remain exact.
Recording reads may reuse their already freshly decoded segments for an owned active result
only when raw segment JSON is exactly equal and replay-window derivation stays read-only.
Keep the separate decode for differing/legacy mirrors and fresh objects between requests.

Use deterministic integrity/work-count tests and independent review alongside component
profiles. Paired component timings on a busy shared host are diagnostic, not end-to-end speedup
claims. Final acceptance needs the consolidated source and immutable packaged browser audit,
with real editability separated from background settlement. Independent source reviews and
focused wire/Store/structural tests pass; consolidated and final packaged speed gates remain.

Evidence: 53 wire cases and 238 scoped regressions; 120 Store save checks, then 101 read/legacy/
roles/Trash/export checks plus the corrected single additive-migration expectation (baseline
proof retained); 28 new Store cases; 199 structural/memo/target cases and an independent
21-case target/warm rerun; five assembled MVP browser cases with unchanged source hashes.
Counts overlap. A pre-existing schema7 fixture now separately asserts the new passage_reviews
table is empty before comparing all prior rows; it is not a waiver of preservation checks.
Whole-repository Ruff/format and scoped independent reviews pass. A quiet 600-passage candidate
recheck passed correctness/source checks: save median 2.57 s and Undo split 2.55 s, versus rc3
3.83 s and 7.29 s. Two of 24 content observations still exceeded the three-second investigation
target (Undo merge median 3.13 s, maximum 3.27 s). This is not an all-samples target pass or p95.
The final exact rc4 package passed 100/600-passage source/correctness rechecks with zero of 48
sampled content actions above three seconds: long save median 2.69 s and Undo split 2.49 s.
Retain the candidate residual and small-sample/hardware/history limits; see the linked audit
for separate API/render/background measurements. The structural exact-mirror shortcut additionally passed 39 focused and 13
independent cases, retaining freshly verified heads and canonical fallback.

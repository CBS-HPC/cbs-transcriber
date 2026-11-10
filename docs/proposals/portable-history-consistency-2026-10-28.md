# Portable history consistency — scoped follow-on

28 October 2026, with a 29 October prerequisite update. **Design notes for the unimplemented
full semantic validator; the delivered pure prerequisite is scoped separately below.**
Prepared against committed `1a710ca`, not the discussion chat's evolving
history/provenance work. Current composition/reader observations must keep their semantic
unchecked categories until a separately reviewed policy implements and verifies each facet.

## Small first scope

After package/database binding, add a bounded schema-12 result-history consistency phase on
the already hardened child connection. Do not introduce another database opener, construct
Store, migrate, register, rewrite payloads or grant restore/remote authority. The committed
Store already supports schema 13; the portable catalog deliberately supports one schema-12
family. Do not infer compatibility from today's Store or silently widen that family.

Candidate existing invariants for the first policy:

- Verify every result-version payload's exact UTF-8 SHA-256 and supported snapshot format.
  Define canonical acceptance from the pinned encoder/decoder behavior before implementation;
  unsupported legacy spelling is not permission to normalize or delete it.
- Verify each saved result's latest version against its stored segment/speaker projection.
  Compare canonical bytes, preserving numeric types, signed zero, Unicode and dictionaries;
  Python graph equality alone can conflate distinct observations.
  Define the accepted sequence scalar domain before choosing the latest version; SQLite
  affinity/CHECK success alone does not establish application-level integer sequence semantics.
- Verify recording active-result membership and the corresponding canonical mirror. With no
  active result, the existing application requires empty segment/speaker projections.
- This first phase does not validate note histories, predecessor/source chains, processing
  artifacts or domain-specific edit semantics; retain explicit unchecked facets. Do not label
  a partial result-history check as complete historical validation.

These are local consistency checks, not working/alternative role design or automatic machine
merging. The wider T43 promotion/terminology/conflict questions remain separate.

## Delivered pure payload prerequisite, separate from this semantic proposal

The first prerequisite is the pure `inspect_result_payload` observation of caller-supplied
native bytes and an exact supplied SHA-256 (T91b3,
[ADR 0091](../decisions/0091-bounded-result-payload-observations.md)). It establishes only supported
snapshot-v1 envelope, canonical UTF-8 spelling and matching byte identity, with safe counts.
It does not open a file/database, import Store or evolving history metadata, validate inner
passages/words/speaker values, or establish sequence/head/mirror/owner/restore semantics.
Its numeric resource caps and narrow policy passed measured design and independent review;
103 permanent cases and isolated-wheel evidence are recorded in the
[verification report](../verification-2026-10-29-portable-payload.md). The broader semantic
policy proposed here remains unaccepted and unimplemented.

The committed `1a710ca` and `b21bfd5` payload encoder/decoder agree, although broader history
metadata evolved between them. Preserve their exact three-key envelope and JSON-native inner
values, including empty results and opaque extension dictionaries; resource refusals must be
explicit rather than presented as corrupt data. Keep distinct integer/real and signed-zero
representations, literal Unicode and the existing canonical key/escape spelling. Do not add
passage validation just because those data usually contain passage dictionaries.

An empty saved result may still be active and have a creation version. A recording with no
saved result may legitimately have no result history. The stored projection JSON need not use
the snapshot encoder's whitespace/key order; future mirror checks must compare its canonical
meaning, not demand that its original column spelling equal snapshot bytes.
Duplicate-key projection spellings, exact sequence domains, gaps/predecessors and null-only
active pointers need their own explicit policy. Existing helpers' permissive parsing/falsiness
does not establish those rules. Result sequences start at one; note revisions start at zero
and cannot inherit result rules. No baseline may be manufactured for a missing result head.

## Bound before decoding

The current 32 MiB value/row bound and 64 MiB SQLite heap policy do not bound Python JSON
materialization. Define per-record/aggregate semantic budgets and a lexical preflight before
`json.loads`: escape-aware depth, value/key/container counts, string spans and numeric token
lengths. Then reject duplicate keys, nonfinite values and invalid Unicode, and compare the
required canonical representation without rewriting retained source bytes. Interview text
must not inherit the manifest's tiny metadata-string bound.
Preflight limits graph creation from an already bounded buffer. Budgets must also cover UTF-8
conversion, parsing hooks, canonical re-encoding and retained lookup state; post-parse hooks
alone cannot bound initial allocation.

SQL byte preflight must precede fetching large payloads. Stream versions/results in approved
order with bounded owner lookups; do not retain every payload or an unbounded ancestry graph.
Budget exhaustion means an honest resource/unsupported refusal, never truncation, disposal
or an invented observation. Measure peak allocation on synthetic representative payloads
before claiming a supported capacity; do not claim an OS/Python memory sandbox.

## Reuse meaning without consuming active WIP

Committed `history.py` provides `encode_payload`, `decode_payload` and exact text hashing;
`Store._validate_mirrors` documents existing application/migration invariants. The latter uses
`fetchall`/unbounded JSON reads and is not an incoming-file validator to transplant.
`processing_metadata` is a report projection that drops invalid observations, not an acceptance
validator. Its lazy language validators also do not establish general historical correctness.

Coordinate a pure validation kernel and its source/policy pin with the chat currently editing
history. Put new preflight limits before the pinned semantic logic, execute captured approved
bytes in the existing child protocol, and retain exact source/policy hashes. Do not load current
Store/providers/inference code or turn today's runtime/model registry into historical evidence.
The precise policy, supported legacy cases, budgets and kernel extraction need independent
review before code; no implementation authorization is inferred from this proposal alone.

## Do not convert local references into ownership authority

Legacy observations establish an observed baseline, not earlier history. Do not invent missing
artifact/model/runtime evidence, apply today's action enums to every historical action, or use
wall-clock timestamps as a replacement for sequence/predecessor facts.
In particular, `EDIT_ACTIONS` is not the complete historical action vocabulary.

The schema-12 catalog deliberately omits several native/runtime ownership foreign keys so
retained journals and unresolved obligations can survive deletion. Demanding every such owner
resolve to today's live project/job/recording would wrongly reject archives retaining those
obligations. Missing live rows cannot automatically invalidate archival evidence, authorize
Retry/Stop/cleanup or count as
quarantine. Remote owner authority and restoration quarantine remain separate contracts.

Return only checked-facet/policy/hash bindings, counts and curated refusals. Keep
`restore_ready: false` and broad semantic categories until all promised sub-scopes are covered.
Capture completeness, role inventory, credentials/data policy, activation quarantine, runtime
security acceptance and institutional decisions remain outside this first consistency phase.

The original notes and follow-on source scoping are read-only. The separately delivered pure
prerequisite has synthetic measurements/tests, not database/history acceptance. No private
data, network, actual database/package inspection or running-app change is part of this
proposal work.

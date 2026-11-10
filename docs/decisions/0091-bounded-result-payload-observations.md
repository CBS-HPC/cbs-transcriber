# 0091 — Bounded supplied result-payload observations

Accepted 29 October 2026 for internal T91b3 after synthetic boundary measurements and
independent design/numerical review. This is a pure prerequisite to later
history consistency, not a database phase, public incoming-file feature or Restore authority.

## Keep the first boundary small

`portable_payload.inspect_result_payload(raw, *, expected_sha256, guard=None)` accepts only
native immutable bytes and an exact native lowercase SHA-256 reference. Validate argument
types/reference/size before invoking guards. Callers cannot supply a parsed graph, limits,
resume cache or an assertion that the payload belongs to a database/result/version.

The accepted envelope is exactly the committed `history.encode_payload`/`decode_payload`
snapshot-v1 contract: `format`, `segments` as a list and `speakers` as an object, with exact
canonical UTF-8 bytes and matching hash. These functions are unchanged between `1a710ca` and
`b21bfd5`; broader history metadata is evolving and must not be imported or extracted here.
The small versioned policy uses the same standard-library serialization settings directly:
`ensure_ascii=False`, `sort_keys=True`, separators `(',', ':')`, `allow_nan=False`.

Preserve opaque inner JSON, empty results, Unicode composition and distinct integer/real,
boolean and signed-zero representations. Do not strip fields, normalize text, infer passage
IDs, validate timing/speaker meaning or use today's edit-action vocabulary. Noncanonical or
unsupported input is refused without rewriting it. Source/context and producer evidence lie
outside these payload bytes.

## Bound allocation before parsing and comparison

The fixed policy `cbs-portable-result-payload-v1` permits at most:

| Observation | Ceiling |
| --- | --- |
| Supplied raw UTF-8 bytes | 8 MiB |
| Lexical value/key tokens, including containers | 500,000 |
| Structural nesting depth | 24 |
| Interior raw quoted-string byte span, including escapes | 2 MiB |
| Numeric/atom token bytes | 128 |

One escape-aware lexical pass precedes `json.loads`. It need not implement a second JSON
grammar: strict UTF-8 decoding, the standard decoder with duplicate/nonfinite hooks, and
UTF-8 canonical comparison reject malformed syntax and unsupported Unicode. The lexical
bounds cap graph creation, including duplicate-pair
hook lists and wide containers; post-parse hooks alone cannot supply that bound. They are
resource support ceilings, not evidence that a larger retained payload is corrupt/disposable.

Do not materialize the whole re-encoded canonical payload. Short noncanonical float tokens
can expand before rejection. Use `JSONEncoder` with the exact writer settings and
`iterencode(..., _one_shot=False)`, compare bounded UTF-8 pieces to the original bytes, and
refuse immediately at a differing prefix or final length. One escaped string chunk, parsed
graph, UTF-8 conversion and object sorting still allocate; measure those cases explicitly.

The initial ceilings deliberately refuse the measured 24,000-word duplicated-map fixture
above 500,000 tokens even though it is below 8 MiB. All ceilings apply together; raw byte size
alone does not imply support for an otherwise valid canonical payload. They do not change
application writing/reading, old snapshots or the
existing portable readers. A broader policy needs new evidence and its own version.

## Refusals and metadata

Guard calls stay outside parser, hook and encoder exception-curation scopes. Retain the
original caller exception object, including BaseException subclasses, and check throughout
the lexical/hash/streaming work. Standard parsing/encoding phases are cooperative bounded
work, not preemptible OS deadlines. Curate parser/Unicode failures without chaining exceptions
that retain raw `doc`/`object` data. Memory/recursion/budget refusals state unsupported resource
limits, never corruption or permission to delete anything.

Return only a `result_payload_envelope_only` observation with `matched_supplied_payload`
reference: policy/format, matched hash,
byte and syntactic entry/token/depth counts, explicit unchecked scopes and `restore_ready:
false`. No decoded graph, strings, IDs, paths or original producer/runtime claims escape.
Counts describe envelope entries; they are not counts of semantically valid passages/voices.

There is no Store/SQLite/filesystem/runtime/network access, shared history edit, new worker or
protocol version in this slice. No aggregate history budget, sequence/head/mirror check,
source lineage, package ownership/capture, credential exclusion, quarantine or restore
acceptance follows. A supplied hash reference does not authenticate its custody.

Single synthetic `tracemalloc` samples describe additional traced allocations in the tested
Python 3.13 build, excluding input creation and tracing overhead. They are not RSS, universal
peak bounds, an OS/Python memory sandbox or private-data security acceptance.

## Verification gate

Before code, 16 selected synthetic measurements and 11 exact/over boundary controls passed
independent review. The frozen study is recorded in the verification evidence; it is not
verification of an implementation. Verify native argument/refusals before parsing, all exact
resource boundaries, guarded
failure identity, privacy, malformed/duplicate/nonfinite/Unicode inputs and canonical
integer/REAL/boolean/negative-zero/Unicode/escape/exponent differential cases against the
committed codec. Run in an owned-only image with committed source, avoiding the discussion
chat's active history/provenance edits. Include an isolated wheel import with no SQLite,
FastAPI or inference modules. Existing reader/codec suites need no repetition absent changes.

The delivered pure implementation, final source/test hashes and all 103 permanent cases are
recorded in [the verification report](../verification-2026-10-29-portable-payload.md).

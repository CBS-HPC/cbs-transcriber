# 0164 — Reuse host canonical JSON facts with fresh byte bindings

Accepted 10 November 2026. Extends ADR 0159 without schema, API, mirror formatting or
synchronous project-snapshot changes.

The rc5 long-transcript CPU profile found repeated prior-head canonical encoding and a cold
decode/re-encode of the successor just produced by the writer. Keep exact history bytes and
authority checks while reusing proven format facts.

The internal serialized encoder parses the actual stored segment/speaker JSON strings into
ordinary JSON values, calls the unchanged canonical encoder, then may seed the existing
512-entry digest-only format memo. Generic Python-object encoding remains unseeded: numeric
dictionary keys can change their sort order after JSON decoding, so a successful generic
encode alone does not prove the reader's canonical round-trip property.

A separate 512-entry LRU retains only triples of raw segment SHA-256, raw speaker SHA-256 and
canonical payload SHA-256, with boolean values. Every lookup hashes both current mirrors and
the actual current head bytes anew, and requires its stored hash to match. On a miss, retain
the old JSON parse/canonical-encode/exact-comparison path. Unsupported raw hashing falls back;
it cannot change the legacy parse outcome, including discarded duplicate JSON values.

These are byte-format/equality facts, independent of row identity, ownership, authorization or
transaction success. Keep fresh owned-head queries, CAS, Working roles, lineage, inverse
checks, strict error behavior and authoritative writes. No parsed objects, payload bodies or
IDs are cached. Locks cover only bounded LRU operations, never hashing, JSON or database I/O.
Eviction changes cost only. The portable checker uses its own host-independent history shim;
its source pins are unchanged.

Tests cover generic numeric keys, malformed/nonfinite/surrogate input, exact Unicode/float
bytes, stale hashes and contradictory mirrors, ownership/Working refusals, warm reads,
eviction and cold compatibility. Performance claims require the assembled-build benchmark;
existing heads may need the canonical fallback on their first Save after restart.

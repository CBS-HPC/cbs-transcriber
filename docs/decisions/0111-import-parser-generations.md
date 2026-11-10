# 0111 — Bind imported transcript receipts to parser generations

Accepted 30 October 2026. Extends exact raw-import receipts (ADR 0073) without changing
database schema or rewriting historical evidence.

Legacy imports sometimes interpret numeric speaker zero as UNKNOWN and tolerate malformed
or ambiguous CSV. Correcting those inputs globally would change historical normalized payloads
when portable restore reparses their preserved raw bytes and checks creation hashes.

Keep receipt schema 1 bound to its exact legacy parsing behavior. Fresh transcript imports
explicitly use receipt schema 2, retaining numeric speaker zero and refusing malformed quoting,
ambiguous/empty headers and surplus or missing CSV cells. Existing parser callers default to
generation 1 so older producer and recovery behavior does not silently change.

Physical receipt verification and portable validation choose exactly the claimed validated
generation; no fallback to another interpretation is allowed. Cache identity includes the
generation. Reports may expose the validated generation but never infer it from time or data.
Keep exact raw bytes, origin, ownership and creation hash verification for both generations.

Optional unrepresentable confidence hints are discarded without changing text or timing. A
midpoint index avoids repeated full-word scans, preserves input order and half-open boundaries,
and keeps portable preflight budgets. Pinned validator and parser source hashes are updated
together. Synthetic legacy golden hashes, differential cases and mixed-generation portable
capture/restore validate compatibility; real historical libraries remain separate evidence.

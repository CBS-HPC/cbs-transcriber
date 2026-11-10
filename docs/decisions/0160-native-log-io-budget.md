# 0160 — Separate native log I/O budget from mandatory guard work

Accepted 10 November 2026. Extends ADR 0153; early-output publication rules in ADR 0155 stay
unchanged.

The real twelve-member native batch had valid supported application metadata, ten completed
JSON outputs and a parseable log, but no local log observations. Its safety guard opens 49
SQLite connections per call. A copied, read-only database reproduction with valid instant
mocked responses spent 3.444 seconds in three guard calls, exhausted the three-second reader
deadline and returned no log. A no-op guard read the same bytes in 0.008 seconds. This is a
local guard-budget failure, not evidence of failed remote transcription.

Keep every mandatory guard before requests and transport blocks. Measure its local execution
time and exclude that time from the log I/O deadline. Keep the three-second I/O allowance,
short remaining-time transport bounds, byte limits, exact owned path/range checks, origin
validation, separate unauthenticated transfer client and redirect refusal. Guard errors still
propagate; optional transport/read failures still defer observations safely. OS/DNS and local
guard execution mean this remains a logical I/O budget, not a hard total wall-clock deadline.

Do not cache approval, source or batch authority. Do not infer completion from log events or
weaken stable-output validation, producer idempotency, the retained monitoring owner or
terminal-only cleanup. Fixing log availability lets the existing early-import path run.

Deterministic tests charge guard time separately from actual slow metadata, session and tail
reads, and retain exact guard-failure propagation. Native log and incremental-result gates and
independent review are required. Real loaded-build acceptance remains separate; never mutate
or restart an active batch merely to activate this fix.

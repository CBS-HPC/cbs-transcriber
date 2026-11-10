# 0163 — Keep native transfer guards independent of transcript payloads

Accepted 10 November 2026. Extends ADRs 0149, 0160 and 0162.

The real failed-member recovery reported three connection-error download attempts. Its output
had previously been read and parsed successfully; eleven other results were saved. The journal
does not identify the transport exception subtype, so local overhead alone is not established
as the cause of the connection failure.

The streamed download guard called Store.recording for every member after every body block.
That read also hydrated transcript/speaker data, replay metadata and exact Working payloads.
A deterministic 2,913,134-byte replay with 45 body blocks made 47 guard calls and 564 complete
recording projections. This cost grew with existing transcript size rather than download size.

Use a fresh id/project_id/status/filename projection for each member in the guard. Keep the
same member loop, current job/batch/frozen identity checks, original-input validation when
needed, project approval and Stop checks. Do not cache authority or throttle checks. Missing
rows and changed project/readiness metadata still fail closed.

This deliberately removes incidental sibling transcript and Working-integrity hydration from
the transfer guard. It does not change authoritative capture, result insertion, history,
Working-version reads, idempotency or cleanup validation at their existing boundaries.

Regressions exercise real streamed bodies in a synthetic twelve-member batch without full
recording hydration and reject a member moved to another project during a transfer. No live
retry or provider acceptance is implied by these tests.

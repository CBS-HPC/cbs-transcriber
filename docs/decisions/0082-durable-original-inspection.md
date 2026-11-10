# 0082 — Durable original inspection, before producer admission

Accepted 28 October 2026 after independent design review. This implements the first inert
T87b slice from the [queue proposal](../proposals/original-readiness-queue-2026-10-28.md).
It does not remove the ADR 0075 activation hold: HTTP acceptance/materialization and
independent playback readiness require subsequent complete slices.

Schema 13 adds one nullable `recordings.original_input` observation. Existing rows remain
NULL; migration obtains the library lease before backup or writes, backs up schema 12,
and does not open media, change recording duration, rewrite jobs or invent earlier evidence.
The internal cache is excluded from recording/API/project-snapshot projections. Existing
report/export allowlists do not expand. No import, public handler or ordinary worker creates
or executes inspection jobs in this slice.

`original_inspection.py` owns bounded cache parsing and short transactional operations:
read, ensure, claim, cancel, retry, recover and manual run. Reuse the existing jobs table
with reserved kind `source-inspect`; generic insertion, mutation, claim, process, Retry and
Cancel cannot bypass this boundary. Generic running-job recovery leaves inspectors alone.
Inspection creates no native/runtime membership, provider request or allocation.

Cache states are pending/running/ready/error, bound to one generation, inspector job and
monotonic epoch. A cancellation settles as error code cancelled plus a cancelled job.
Every lifecycle publication updates cache and job atomically under the same CAS. Concurrent
ensure reuses the active inspector. Retry preserves job/generation and source anchor while
advancing the epoch; ready or corrupt evidence cannot be silently reset. A stale worker
cannot publish over a newer attempt. Running Cancel persists intent and retains running/busy
ownership until its actual I/O drains; queued cancellation may settle immediately.
Any failure in a live decoder's ownership/cancellation guard first kills and reaps that
owned process and drains its readers, then propagates the original failure. Leaving a
Popen context to wait for natural completion does not satisfy this contract.

Claim, run and restart recovery require the caller's existing lease on the same Store.
Do not re-enter `library_lock` from a worker: its RLock is held by the serving lifespan.
The caller retains the lease through worker shutdown/drain. Slow hashing/decoding occurs
outside queue, snapshot and SQLite locks. Explicit restart recovery applies only to proved
local inspectors, advances their epoch and never replays a producing/provider job.

The first complete stable byte hash/size is anchored before decoding. Preserve it through
decode failure, cancellation, retry and recovery; an externally changed original cannot be
adopted at the same generation. Incomplete initial hashing remains honestly unobserved.
Hashing is cancellable between bounded reads, checks size before I/O and bounds growth reads
against the opened size plus a sentinel byte. `audio_inputs.freeze` retains its existing
default contract and gains only the optional internal identity observer/cancellation seams.
Ready publication requires a strict full original-v1 receipt matching recording and anchor.

Bound cache bytes and structure before use, reject duplicate/unknown/malformed fields, and
retain only curated lifecycle errors. Missing or contradictory proof refuses this internal
operation rather than relabelling earlier jobs or weakening producer retries.

Next work must durably accept every requested producer/feature choice, run source/preflight
work in the background, and materialize its same-ID executable job atomically. All compatible
native members selected together retain one source-validation barrier/allocation; API and
CBS Runtime may admit ready members independently. Playback separation follows those guards.
This schema/helper delivery alone is not a latency fix or remote-preparation implementation.

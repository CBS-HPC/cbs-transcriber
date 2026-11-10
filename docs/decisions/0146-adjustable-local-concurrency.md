# 0146 — Adjustable parallel local tasks

Accepted 5 November 2026. Amends the fixed worker counts of ADR 0009 (two recording and two
media workers) and ADR 0085 (two source inspectors and two admission workers). No schema change:
the choice is one JSON row in the existing settings table (id 9).

## Decision

Settings → Local Processing → **Parallel local tasks** sets a limit for four queue lanes:
playback preparation (`media`), original audio inspection (`source-inspect`), provider API
transcription (`transcribe-api`) and request preparation checks (`input-admission`). Each value is
an integer from 1 to its lane maximum (8, 8, 8 and 4), or null for automatic. Automatic follows
this computer: `media` uses the logical processors minus two (2–8), leaving two for the server
and editing UI; `inspection` uses half of them (2–4); `api` defaults to six and `admission`
stays at two. The user raised the media and API defaults on 6 November 2026 because local
processing, not transcription, limited throughput (benchmark: 45 s at four, 35 s at six,
30 s at eight media workers). A PUT replaces only the fields it sends;
invalid values are refused before anything is saved. An unreadable saved row falls back to
automatic, because only throughput depends on it.

Each lane starts its maximum number of threads. The limit gates **new claims only**, checked under
the existing shared claim lock together with the number of that lane's workers that currently own
work. Raising a limit therefore takes effect at the next claim without a restart; lowering it never
cancels, interrupts or pauses work that already started. The saved row and the in-memory limits
are updated under the claim lock, so concurrent saves cannot leave them disagreeing.

Native UCloud Transcriber jobs move to their own fixed `transcribe-native` lane with two workers,
claimed by provider type. More API workers can therefore never mean more concurrent native
allocations, and native allocations no longer hold API files up. Unchanged: one local WhisperX
enhancement lane, one UCloud enhancement lane, one Runtime preparation lane, four CBS Runtime
coordinators with their own durable allocation limit (ADR 0065) and the two shared Runtime
preparation/transfer gates. A standalone `transcribe` worker still claims both kinds.

Idle siblings coalesce polling. After one worker of a lane finds nothing to claim, the others skip
the database until that poll's interval (0.5 s, or 0.2 s for inspection/admission) has passed or a
new notice arrives; a worker never skips because of its own poll. `notify()` advances a notice
counter, so new work is still claimed immediately. Admission's claim scan skips intents that are
still waiting for their own pending or running inspection before fully validating them: every
cohort containing such an intent yields no token, so the result is unchanged.

## Rationale

The synthetic benchmark ([evaluation](../evaluations/local-concurrency-2026-11-05.md)) on a
four-core/eight-thread laptop shows FFmpeg-bound lanes scale to about the physical core count:
with the earlier code, upload-and-transcribe of 16 five-minute files took about half as long with
four media, inspection and API workers as with two, while six or eight added little to that mixed
pipeline and raised peak memory (about 30–35 MB per extra task). With the initial 4/4/2/2 automatic
limits, playback for all files was ready 20–47% sooner. The separate playback-only measurements
continued improving at six and eight workers; the user chose the higher bounded media default
on 6 November. Inspection remains capped at four automatically. The current combined defaults
have not been qualified on a quiet machine or during interactive editing, and no machine with
more than four physical cores was measured.

Provider requests are network-bound and scale further with mocked latency (four API workers
finished all transcripts about 35% sooner than two), but each file can send 1–4 parts at once and
a rate-limited part (HTTP 429) fails for explicit retry (the provider client does not retry).
More API files increase pressure on the user's own provider quota. The initial default was two;
the user explicitly chose six on 6 November, and Settings can lower it. The 35% observation
compares four with two, not the current default of six. Admission is fast metadata work at any
worker count.

Measuring also exposed a real contention defect. With 16 intents waiting for inspection, each
admission poll fully re-validated all of them inside a write transaction under the claim lock.
Two idle admission workers held that lock for about 39% of wall time (99% with eight), delaying
every other lane's claims and every write; single write-lock waits reached 4.1 s in a pipeline.
Simply adding threads would have multiplied this, so coalesced polling and the
pending-inspection prefilter ship with the larger defaults: afterwards the same idle case held
the lock 5% of the time and pipeline write-lock waits stayed below 0.2 s.

## Guarantees and limits

Claims stay atomic SQLite transactions under one claim lock; a stress test processes 150 mixed
jobs with eight media and six API workers exactly once and within each limit, with native work at
most two. Cancellation still targets only the owning worker; shutdown drains every accepted task
while the library lease is held and kills/reaps running FFmpeg conversions. Recovery after a crash
is unchanged. FFmpeg processes are not individually thread-limited: video (libx264) uses several
threads itself, so a few video files can saturate a machine at lower limits. Memory grows with
parallel decoders (roughly 30–35 MB per audio task in the benchmark); the Settings note says so.
Live resizing applies to these four lanes only. Activity grouping and progress are unchanged.

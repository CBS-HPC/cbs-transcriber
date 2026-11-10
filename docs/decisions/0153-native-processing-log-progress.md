# 0153 — Observe native processing logs without granting job authority

Accepted 9 November 2026. The user identified the batch-owned out/transcribe.log as a source
of useful progress, including which input started and model-loading observations. They also
want to check whether an overnight run progressed. Implemented with 42 synthetic log cases and
independent polling/lifecycle/guard checks; real-provider acceptance remains separate.

Read only the exact output log under the recorded native batch's owned run folder, with
bounded bytes, network time and polling frequency. Missing, malformed, oversized, rotated or
unavailable logs are optional observations; failure falls back to ordinary UCloud monitoring.
Use the existing transfer-origin and credential-scope protections. Do not submit, stop, clean
or retry an allocation through this reader.

At most once per 15 seconds, read metadata/control JSON up to 16 KiB and a log tail up to
64 KiB. The synchronous streaming helper has a logical three-second budget, per-transport-block
guards and short transport timeouts; blocking transport/OS/DNS operations may overshoot that
logical budget. It has no background worker or claimed hard wall-clock deadline. A separate
unauthenticated transfer client never receives control Authorization; redirects stay disabled.

Map only exact frozen input names/UUIDs to members and parse an allowlist of known messages.
Expose curated progress, never arbitrary raw lines, provider paths, secrets or transcript text.
Threaded logs may interleave: a line without a file identity cannot be assigned to the most
recent file merely because it is adjacent. Processing ordinals are not member identities.

Exact Starting/Processed observations remain per member; model-loading lines without an input
identity remain batch-only. One bounded log_progress journal retains at most 64 member notes,
one batch note, event digests, validated optional log timestamps (timezone unspecified), and
separate UTC application observation times. Repeated/older rotated events do not refresh them.

Keep log observations distinct from fresh UCloud job state and verified output/local-save
receipts. Starting another file is not proof the previous one finished. Retain useful curated
observations through existing durable batch/member messages so explicit Retry after relaunch
can inspect the same allocation. General automatic startup recovery remains outside MVP.

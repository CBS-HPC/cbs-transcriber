# 0049 — Separate local and UCloud enhancement lanes

Accepted 23 October 2026.

ADR 0016 gave enhancement one worker. With UCloud enhancement (ADR 0046) and automatic routing
(ADR 0047), that worker can spend an hour or more polling a remote allocation while a short
local word-timing job or a passage repair waits behind it, although neither competes for the
same resources.

## Decision

The queue runs two enhancement workers that claim disjoint jobs:

- the **local lane** claims enhancement jobs whose frozen execution is not `ucloud`, including
  passage repairs and their cached local model worker (ADR 0026);
- the **UCloud lane** claims jobs frozen with execution `ucloud` (API-only runs and legacy SSH
  recovery). Its local work is limited to audio extraction, transfer and merging.

The lane follows from the frozen job parameters, so retries and restarts return to the same
lane, and `auto` is always resolved before a job is queued. Each lane remains sequential:
two local WhisperX runs never compete for CPU/GPU memory, and UCloud runs still allocate one
B200 job at a time from this app. Jobs keep their own checkpoint folders, results append by
producing job ID, and cancellation finds the owning worker as before.

## Consequences

A local job and a UCloud job for the same recording may run at the same time. Both freeze their
source version, write only to their own folders and append separate results; neither changes the
reviewed result. Provider-wide capacity limits remain later work.

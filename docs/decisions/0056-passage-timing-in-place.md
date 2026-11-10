# 0056 — Passage word timing added in place, several passages at once

Accepted 23 October 2026. Amends ADR 0052 and the passage repair part of ADR 0049.
Amended by ADR 0058 (one tag for every untimed passage; no static tag).

The researcher tested "Add word timing" on single passages of a 1,313-passage interview. Three
problems showed up:

- Starting one passage turned every tag in the transcript to "Processing…". The tags read any
  local enhancement job of the recording as their own.
- No other passage could be started while one ran: the local lane accepted one enhancement per
  recording.
- Each repair appended a new result to review with "Review repair", so corrections and timing
  forked.

Passages without their own audio interval also showed a disabled "Add word timing" button with
no visible reason. The banner above the passages was hard to read.

## Decision

- **In place, like realignment.** "Add word timing" on a passage, and "Repair all N of M
  missing word timings", use the ADR 0052 path:
  - a local align-only job freezes the chosen passages (only those) at the current revision;
  - the job stores per-passage words;
  - the editor adds them to the same result as one timing-only `timing_realign` version, once
    no draft, dialog or save is open.

  Passages whose text or bounds changed meanwhile are skipped, never overwritten. Text never
  changes. History can undo it.
- **One job per request; many requests.** Each tag click queues its own small job
  (`POST /api/recordings/{id}/passage-timing`), so other passages can start while one is
  aligning. They wait in the local lane. A passage whose timing is already queued, running or
  ready is not queued twice (409). "Repair all" is one job and one version.
- **Each tag shows its own passage.** A tag shows its own state: Queued…, Aligning…, Adding…
  or Try again. All other tags stay available.
- **No dead buttons.** Passages without their own start and end, or longer than 60 seconds or
  120 words, show a static "No word timing" tag whose tooltip says to use Add word timing /
  speakers… for the whole result. The comparison pane is read only: it shows static tags and a
  count, with no actions. The rule that a passage is never timed from neighbouring passages or
  text length is unchanged.
- **One tag instead of the banner.**
  - When every missing passage can be timed: "Repair all N of M missing word timings" (N
    missing of M passages).
  - Otherwise: "Repair K of N missing word timings", with the rest explained in its tooltip.
- **An open draft is kept.** Starting passage timing no longer clears an open edit; the timing
  waits for it.

## Consequences

Passage timing no longer creates results or the "Review repair" step. The
`/enhancements` passage repair API (`segment_id`/`segment_ids` producing a combined result)
remains for API clients and existing jobs. Repairs stay local (ADR 0047). A whole-recording
local enhancement still waits while passage timing jobs are queued. Routing a large "Repair
all" to UCloud would need UCloud passage alignment and is a separate decision. This is step 1
of the version-control redesign proposal (machine timing as versions of the working
transcript) for passage timing only.

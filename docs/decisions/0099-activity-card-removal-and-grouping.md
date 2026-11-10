# 0099 — Activity card removal and grouped request/upload cards

Accepted 29 October 2026. Researchers can remove one finished card from Activity with its ×
(Remove from Activity), and files started together appear as one card. Both are view state:
no job, journal, result, original or remote file is deleted, cancelled or rewritten.

Removals extend the existing per-library Activity settings row (V105) with
`hidden: {card job ID: updated}`. Clear finished preserves it. A removal records the update
time each job had, so any later change shows the card again; the browser compares that exact
time. Writes re-read the row inside one SQLite writer transaction, forget entries whose job or
CBS Runtime owner no longer exists and keep only the newest 1000 removals.

`POST /api/activity/hide` refuses queued/running work and anything that still owes a decision:
a job's own pending UCloud cleanup, uncertain submission or unstopped live remote job (the same
conditions that keep it in the /api/jobs listing), unresolved native or Runtime allocations,
unresolved native input selections and unverifiable preparation evidence. Obligations therefore
stay visible after both Clear finished and removal. A shared native allocation is recorded for
every member with the allocation's update time, because any member may represent its card.

Source-preparation intents with one `request_id` and media jobs with one upload batch render
as one grouped card. The browser generates a random 32-hex upload batch per multi-file
upload; the server validates it before copying bytes and stores only `params.upload_batch`
on the media job. Single-file uploads are unchanged. A grouped card is removed only as a
whole, after every member is removable. Grouping never merges server authority: each intent
keeps its own frozen epoch/phase token for per-file Retry/Cancel, the group's single Prepare
uses the first eligible member's token until one confirmation covers every sibling, and upload
Cancel calls the existing per-job endpoint for each active file.

Activity polls render only if no newer poll has rendered, so a late response cannot restore a
removed card or older state. Card notes no longer use the global right float, which had placed
the provider line in a narrow column and let the badge/actions overlap the message.

Verification uses synthetic libraries, mocked provider rows and local FFmpeg playback
preparation only; no UCloud job is contacted.

## 30 October — active Stop and original inspection

An active card's top-right × duplicates its existing exact Stop/Cancel control. Finished ×
still means Remove; unresolved cleanup and local-deletion recovery never acquire Stop or
Hide merely from that placement. Repeated controls coalesce until the pending request settles.

Original inspections use the verified recording/project relation to one unambiguous media
upload batch. Retry/Hide use current job receipts; stale, ambiguous, restored and legacy rows
are not grouped by time or name. This preserves the import group during its first stage.

## 30 October — durable workflow groups and bounded listing

Ordinary work carries project/request identity from `input_intent` through the durable
`input_acceptance` receipt. An automatic enhancement joins only through its exact producing
transcription job/result on the same recording. Native shared/singleton allocations and CBS
Runtime owners take precedence, including late joins. Failed admissions stay explicit when
successful siblings move into an allocation; never count those failures as submitted members.
Legacy ordinary work without a durable request ID stays separate.

Count distinct recordings as Files, producer choices as Model requests and jobs as Stages.
Select recent groups, then expand complete membership before the recent-row cutoff. Exclude
restored history before ranking. A group has at most 1,000 displayed stages: overflow is
explicit, says counts are shown-only, refuses group Stop/Hide/Clear and retains individual
controls. Hidden-state checks use the same membership and exact current update receipts.
Unresolved native owners survive beyond the former 200-row limit, including deleted members.

Raw rows, membership and intent projections share a read snapshot. This prevents a same-ID
handoff from losing its frozen phase token. Native status inputs must also be captured before
closing that snapshot; fresh retained cleanup owners remain independently visible after Delete.
The grouped read uses a bounded window expansion instead of one history read per selected
group. It does not add a schema or index migration.

Evidence, draft-regression measurements and remaining live limits are in the
[integrated Activity audit](../audits/transcription-batch-2026-10-30.md).

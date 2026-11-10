# 0092 — Cancel owned work before recording deletion

Accepted 29 October 2026. A recording or project Delete cancels its queued/running work;
the user need not find and stop each Activity entry first. Cancellation is distinct from
remote output disposal and deletion of local research material.

Use one short-lived in-memory operation per explicit deletion request. Capture bounded
target metadata, result/version identities, exact job scope, reserved control epochs and
shared allocation membership. The initial DELETE requests cancellation through the owner
of each job. A shared UCloud allocation containing other recordings requires a structured
409 impact preview and explicit confirmation before any cancellation. Never implicitly
expand a previously confirmed scope when more work joins it.

Return 202 while workers still own files or reserved admission groups are settling. The
operation token authorizes finish-only checks: it must not cancel jobs again. Edits, new
jobs, Retry, changed reserved generations or changed allocation membership invalidate the
operation. The browser keeps its card/dialog and reports progress until actual 200 success;
it stops continuations on navigation, closing the dialog or page reload. There is no durable
delete queue that could remove recordings after the user's view has gone away.

Cancel using the source-inspection and input-admission control authorities for reserved jobs;
ordinary jobs use Queue.cancel. Freeze and check shared membership in its stop transaction.
Signal the actual worker, preserve completed results/follow-ups, and drain accepted requests
before deleting files. Remote control requests run outside the queue lock. Merge the final
termination acknowledgement into the current journal, never replace stale parameters.

Final removal revalidates under queue → snapshot → SQLite writer exclusion, including every
active worker ID and batch owner, unsettled native input groups and unresolved remote
obligations. Verify all managed paths and parents before removing any tree. Stop does not
authorize Trash of unsaved/uncertain remote output. If recovery, cleanup or reconciliation
remains, keep local recordings/history and give the reason inside the open dialog.

No new schema or background service is needed. Expired/unknown tokens fail closed; a new
explicit Delete can capture a new scope. A successful operation is cached only after the
database transaction commits, permitting a lost HTTP reply to be acknowledged safely.

Verification uses synthetic libraries and mocked controls: queued/running deletion, worker
drain, retries/new work, reserved generations, shared allocations and late admission,
remote failures/obligations, ownership/path guards and delayed browser replies. No live
recording is deleted and no UCloud job is allocated by these checks.

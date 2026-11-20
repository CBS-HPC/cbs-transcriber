# 0190 — Project deletion in Activity

Accepted on 20 November 2026 for post-MVP-v2 work, implemented on `claude/deletion-activity`
from Codex's proposal of the same day (private handoff, 30 scenario oracles). It changes job
semantics and adds a small persisted note, so it extends ADR 0092 (finish-only deletion
requests), ADR 0101 (Delete-consented remote release), ADR 0102 (recoverable local staging)
and ADR 0106 (capture admission) without replacing their guarantees.

## User decisions (20 November)

Testing MVP v2 the user deleted a 12-recording project. The open dialog drove the deletion by
re-sending Delete every second; closing it abandoned the request, and the open project stayed
visible until a refresh. The user decided:

1. **Hand-off.** Pressing Delete closes the dialog at once. An Activity item "Deleting
   project …" takes over with stages *stopping jobs*, *removing from the library* and
   *removing files*. The server owns the deletion, independent of any dialog or tab.
2. **Inactive project.** Only that project becomes inactive: greyed, tagged "Deleting…", not
   openable or editable, with no new work on its recordings. A researcher inside it lands on
   the project list. The rest of the app stays usable.
3. **Keep project.** Activity offers Keep project until the point of no return. Jobs already
   stopped stay stopped.
4. **App closed early.** If the app closes before the point of no return the project is kept
   and Activity notes the unfinished deletion. Nothing resumes automatically.
5. **After the point of no return** deletion is permanent; backups are the way back. No
   "Recently deleted" area and no automatic pre-delete backup.

## Decision

### Point of no return

The point of no return is the **SQLite commit that removes the project row and its
recordings**. Before it, Keep, a failure or an app shutdown abandons the logical Delete:
the writer transaction rolls back and staged files are restored from fresh database facts
(ADR 0102). After it, nothing recreates project rows; local cleanup follows its ADR 0102
journal and UCloud folders follow their ADR 0101 release receipts.

### Atomic release receipts

The legacy continuation records each batch owner's release decision (`confirm_release`) or
deleted-member receipt, and may move the UCloud folder to Trash, *before* staging and the
Delete commit. Keep would then be untruthful, and a crash between the two left a kept project
whose remote owner was already released and fenced. For a task:

- Exact owner control slots (`queue.control_owner`) are reserved first, and the runtime
  actions lock is taken before staging and the SQLite writer (its existing callers take it
  before SQLite, never after).
- A read-only release plan reproduces `_release_owner`'s decisions (restored owners skipped,
  exact consented folder/server/project/file-count identity or refusal, trash only when both
  preview and current impact say trash).
- New transaction-aware writers `native_batch.confirm_release_in`/`record_deleted_members_in`
  and `runtime_store.confirm_release_in`/`record_deleted_members_in` write the same receipts,
  with the same checks and the same meaning, **inside the Delete writer transaction**. The
  legacy functions are now thin wrappers (runtime) or unchanged (native).
- After the commit, the existing `_finish_runtime`/`_finish_native` do the stop/terminal
  check/Trash once, while the reservations are still held. A failure keeps the durable
  decision for Activity Retry and the automatic release lane; it never undoes local deletion.
  During shutdown this remote step is skipped and left to those same paths.

The legacy HTTP `DELETE` continuation keeps its earlier ordering unchanged for older clients
and for recording deletion (see *Recordings* below).

### Server-owned task

`project_deletion_tasks.ProjectDeletionTasks` is installed by the app factory, tied to the
serving Store and its lease. One executor thread runs accepted tasks in order (at most eight
running or waiting). It orchestrates the existing primitives only:

1. **Admission** (`POST /api/projects/{id}/deletion-tasks`, 202) takes the operation from the
   unchanged read-only preview, the exact fingerprint and the explicit shared confirmation
   (strict body). It validates the capture again, requires the same consents as the legacy
   route, refuses single-job UCloud blockers **before** any Stop, marks the operation as
   task-owned (it no longer expires and no `DELETE` continuation may drive it), writes the
   receipt and installs the project fence before replying. An exact replay returns the same
   task; a changed body is refused without writes. Admission is serialized, so a replay
   racing its own first request and an app shutdown both see a settled admission.
2. **Stopping jobs** dispatches only the captured Stop controls (`_cancel`), checking Keep and
   shutdown before the first control and between generic dispatches. It then waits outside
   all locks for the complete existing drain evidence (`_pending`: control slots, worker
   active ids/batches, busy recordings, admission obligations), revalidating each time.
3. **Removing from the library** takes the local file action, owner reservations, the runtime
   actions lock and the existing staging promotion (ADR 0106), then queue → snapshot →
   SQLite writer, revalidates, stages exact paths, writes release receipts, deletes vocabulary
   and rows, and finally asks the task's decision. Staging refusals (an export or Hugging Face
   task running, another file operation) make the task wait and retry, not fail.
4. **Removing files** settles the ADR 0102 journal outside the locks, then runs the remote
   release step described above.

`GET /api/project-deletion-tasks/{id}` observes; polling never advances, recaptures or
recancels anything.

### Keep project and the decision

`POST /api/project-deletion-tasks/{id}/keep` (bound to the task's project id) only sets the
task's Keep flag under its decision lock and wakes the executor. The last step inside the
Delete writer takes the same lock: if Keep was requested it raises and the transaction rolls
back; otherwise it marks the task as committing and Keep is refused from then on (409,
`deletion_committed`). Exactly one wins. A COMMIT that raises is classified from fresh
database facts after the connection closes, never from the task.

Staging promotion excludes HTTP mutations and the staging journal makes the existing global
file-recovery block refuse them, so a plain POST could not reach the task in time. The exact
Keep path (`/api/project-deletion-tasks/<32 hex>/keep`) is exempt from those two admission
gates only; host, origin and request-header checks still apply. It mutates no project,
Store or file state; the executor owns rollback and the recorded outcome.

Keep never undoes an accepted Stop, retries a job or resubmits remote work. The Keep reply is
202 while the executor settles and 200 once kept.

### Inactive project

While a task runs, its project is in an in-memory fence:

- `/api/projects` returns `deleting: {task_id, phase}`; the sidebar shows it greyed with
  "Deleting…" and without a link; home shows the first other project; its project or
  recording address returns to the project list with a notice; a page inside it (any tab)
  leaves when its ordinary Activity poll sees the change.
- Mutations addressed to the project, its recordings, collections or jobs (including input
  intents and inspections) are refused with 409 `project_deleting` by a middleware outside
  the capture gate, so they never wait for staging. Uploads and new transcriptions check the
  body's project/recordings in their routes. Storage moves of that project and library-wide
  switching wait for the task. Reads and exports of other projects are unaffected.
- Work accepted before the fence keeps its frozen authority. If a late write or a producer
  result changes the captured scope, the existing revalidation refuses the stale deletion and
  the project is kept with its new result; nothing rebases or widens consent.

### Notes across a restart

Each task writes a small receipt `project-deletions/<task>.json` in the library folder
(format `cbs-project-deletion-v1`, at most 16 KiB): project id and name, request digest,
state, phase, revision, tri-state committed, safe message and cleanup flags. No path, token,
transcript text or executable selection is stored.

- Startup (after ADR 0102 file recovery, before backfill and workers) reads at most 128
  receipts. A receipt still running is settled from fresh facts: project row present →
  *interrupted* (kept); absent → *deleted*; unreadable database → *needs attention*. No
  Stop, Delete or remote request is ever started from a receipt.
- Graceful shutdown closes admission, signals every task before the point of no return,
  and joins the executor under the serving lease before other managers close; those
  projects become *interrupted* and kept.
- Malformed, oversized or foreign receipts appear as "Unreadable deletion note" cards that
  can be dismissed and grant nothing. Backups and portable captures copy the database, not
  this folder, so a restored library carries no deletion state. Library deletion (ADR 0144)
  lists the folder as an app entry.
- At most 64 notes are kept: the oldest deleted/kept notes are pruned first; otherwise a new
  deletion asks to dismiss earlier notes.

### Activity card

Kind `project-delete`, id `project-deletion:<task>`, with `project_deletion` holding the
task id, project id, state, phase, revision, `can_keep`, committed, `project_inactive`,
cleanup flags and `can_dismiss`. Activity states: running; completed (deleted); cancelled
(kept); interrupted (app closed early, or file recovery pending); failed (refused before the
point of no return, project kept). Interrupted and failed cards need attention. Finished
cards use **Dismiss**, never the Activity hide list; Clear finished hides only finished ones.
Local cleanup and remote release obligations keep their own independent cards.

## Deviations from the proposal

- **Persistence.** The proposal recommended an additive SQLite table under the next schema
  after root review. This implementation keeps the receipt as a file folder beside
  `local-deletions`, because outcomes are decided from database facts anyway, it needs no
  schema 20 (which concurrent branches may claim), migration, catalog pins or portable
  restore handling, and copies are inert by construction. A later schema can absorb it.
- **Admission blockers.** Single-job UCloud blockers known at preview refuse at admission,
  before any Stop; the legacy continuation cancels first and refuses afterwards. Blockers that
  only appear after Stop still refuse at the last phase and keep the project.
- **Release profile.** No feature key was added: this replaces an existing entry point's
  behaviour rather than adding unfinished work, and MVP v0.1.0 is frozen separately.
- **Retained notes** are files with Dismiss, not Activity hide-list entries.
- **Fence scope.** The fence refuses by resolved resource ids at admission; it is not
  re-checked inside every writer. The deletion's own revalidation is the backstop, so a write
  admitted before the fence can make the deletion fail safely (kept), never land unfenced
  after the commit.

## Recordings

Recording deletion keeps the legacy dialog-driven continuation and its release-before-commit
ordering. Applying the task model would need recording-level inactivity in every route and
editor, multi-recording Keep semantics and a second Activity card type; that widens this
change considerably, so it remains a later decision.

## Consequences

- Closing the dialog, navigating or reloading no longer abandons a confirmed project
  deletion, and a Keep or early close leaves no remote release decision behind.
- The backup proposal's reciprocal fence has a seam: `ProjectDeletionTasks.inhibitors`
  (callables refusing new tasks, e.g. during a capture) and `require_idle()` (already used by
  library switching); backup capture admission is not wired yet.
- Human acceptance (H24) still needs a real many-recording project, Keep during each stage,
  closing the app early and real UCloud owners; synthetic tests do not establish them.

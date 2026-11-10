# 0045 — Shared native batches and Trash-based cleanup

Accepted 22 October 2026. Extends 0012, 0021 and 0028; supersedes 0028's permanent-DELETE cleanup.

## Shared allocation

Selecting several recordings for the same native UCloud configuration started one allocation per
recording. Transcriber Batch accepts an input directory and writes one output per input, so one
explicit request now groups recordings with identical frozen settings, provider ID and project
into one allocation. Single recordings and every other provider keep independent jobs.

Schema 9 adds `native_batch_runs(id, project_id, state, params, members, …)`. A SQLite backup
of schema 8 precedes the transactional migration; existing rows are unchanged. The batch row owns
the remote journal (folder ownership, uploads, submission intent, remote ID, raw hashes, cleanup),
independent of recording cascades so an unresolved allocation keeps its journal. Member jobs keep
their own result/follow-up idempotency and recording identity.

One worker claims all queued members and the batch in a single transaction. Inputs upload as
`<job-id><suffix>` into `cbs-batch-<batch-id>/input`; one specification named
`CBS Transcriber batch <batch-id>` is submitted once. Each output is validated and committed only
to its frozen owning recording. A sibling's failed import never reverts a saved result. Retry,
reconciliation, Stop and cleanup act on the whole batch: an uncertain submission never repeats,
and exact-name reconnection uses the batch name. Activity labels shared members and their count.
Live acceptance of the real Batch output naming remains pending.

## Cleanup

UCloud's current storage provider registers no handler for `DELETE /api/files`, and a missing path
returns HTTP 400 rather than 404, so the earlier procedure could never confirm removal. Cleanup
now moves only the exact journaled owned folder with `POST /api/files/trash` and confirms its
absence by listing the parent folder, with bounded backoff. A refused move that finds the folder
already absent counts as confirmed, which keeps retries idempotent. The receipt is `trashed`;
legacy `deleted` receipts remain valid.

This is explicit, not silent: Activity states the files moved to UCloud Trash and that emptying
Trash in UCloud frees the space. The app never empties Trash, since that would remove the user's
other trashed files. Failures keep `pending` with a short, credential-free reason and the existing
cleanup-only retry. All other ADR 0028 preconditions still apply.

Sources: [core files API](https://github.com/SDU-eScience/UCloud/blob/c216a062878e88c6b07a416bd754a1e899d85b5d/core2/pkg/orchestrator/files.go),
[provider file controller](https://github.com/SDU-eScience/UCloud/blob/c216a062878e88c6b07a416bd754a1e899d85b5d/provider-integration/im2/pkg/controller/files.go),
[K8s trash and missing-path handling](https://github.com/SDU-eScience/UCloud/blob/c216a062878e88c6b07a416bd754a1e899d85b5d/provider-integration/im2/pkg/integrations/k8s/filesystem/00_module.go),
[Transcriber Batch](https://docs.cloud.sdu.dk/Apps/transcriber.html#transcriber-batch).

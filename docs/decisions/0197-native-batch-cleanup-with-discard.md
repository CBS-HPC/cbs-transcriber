# 0197 - Confirmed cleanup of a failed native batch

24 November 2026. Ordinary native cleanup preserves every unsaved result. When
Retry failed cannot retrieve the outputs, the researcher may use **Retry remote
cleanup** to abandon one failed, interrupted or cancelled native batch.

The existing native cleanup route supports a preview with an empty JSON body.
It shows the exact remote job and folder plus saved and unsaved counts. It reads
only the local journal, members and producer results. The frontend asks before
sending the fingerprint. Confirmation binds the owner, settings, membership,
member states, producer results and the observed journal version. The server
reserves the whole allocation, then validates and records consent with cancelled
local processing states in one SQLite writer transaction. Saved results,
versions, Working pins, original recordings and existing local artifacts are kept.

The existing release journal supports this independent decision. The remote step
verifies the exact job ID and unique native job name, observes a final state,
then uses the recorded owned folder and existing Trash confirmation. A running,
suspended, uncertain or unverifiable remote job cannot authorize Trash. This
operation starts and stops no UCloud jobs. A pending Trash failure stays in
Activity and can resume without a new decision. The confirmed receipt prevents
restarting that old allocation. The researcher may make a fresh manual request.
The resolved card stays available in Recent activity and can be cleared as usual.

A bodyless cleanup request keeps the ordinary save-first behavior for existing
clients. An explicit discard does not grant permanent Trash purge or deletion of
any other folders. The MVP interface exposes this only for native batches with a
known remote identity, a sealed selection and exact folder ownership.

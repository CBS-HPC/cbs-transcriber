# Operator checkpoints for a later authorized idle activation

This procedure prepares evidence; it does not authorize or perform an activation.
Do not run it against a real library/server until that exact operational scope is
approved. `scripts/activation_checkpoint.py` has no merge, migration, network,
credential, stop, start, retry, cleanup or restore command. It imports only the
standard library and never constructs `Store`.

The older ignored October 30 scripts used a fixed missing-original count (first
zero, then one) and constructed `Store` during backup. Those are historical evidence,
not reusable activation commands: a constructor may migrate before taking the copy.

## What the helper observes

- SQLite's backup API reads the source through `mode=ro`/`query_only`, including
  committed WAL pages. Only the new copy is changed to DELETE journal mode, closed,
  reopened and checked for integrity, foreign keys, schema and typed row hashes of
  every user table/column. SQLite WAL/SHM bookkeeping is not an application write;
  this is a logically read-only source connection, not a filesystem immutability claim.
- Every currently recorded original is discovered from the copied database, using
  the current project location (or the legacy library fallback). Only explicitly
  supplied media roots may be read. Each regular file gets a fresh SHA-256; no cached
  size/mtime hash is reused. Missing originals are recorded **by current recording ID**,
  never assumed to number zero, one or 39. A stable preexisting hole remains a hole;
  a different missing file with the same missing count fails comparison.
- Existing `local-deletions` entries are inventoried by relative name and hash without
  parsing their targets or granting disposal/recovery authority. Their bytes, staged
  wrappers, originals, runtime/result artifacts and other files are **not copied**.
  Keep the old library and all managed project folders intact. A database snapshot
  plus inventories is not a complete library backup or a tested restore package.
- `offline` requires the existing initialized `server.lock` byte to be available and
  holds the same OS lock primitive as the app throughout capture. It neither creates
  nor writes that file. Other phase names are labels only. No phase proves exclusive
  filesystem access or stops unrelated writers; quiescence remains `not_checked`.
- Credentials/keyrings, `.env`, current model caches and cloud APIs are never read.
  The SQLite copy itself contains private research data and any legacy values already
  stored there: treat the entire checkpoint as private, not as a sanitized export.

Destinations must be new absolute folders outside the library/media roots. Capture
refuses active queued/running jobs from the **complete database**, independently of
Activity pagination. Failed copies remain partial and have no `checkpoint.json`;
do not reuse or overwrite them. The backup loop has a 120-second budget, not a hard
OS/filesystem I/O deadline. Links and reparse points are refused, including Cloud
placeholders; actual OneDrive hydration and Mac/filesystem acceptance are separate.
Receipt publication uses a flushed pending file and create-if-absent hard link; choose
a checkpoint volume supporting hard links. Unsupported storage refuses publication
and retains the partial copy. This does not prove power-loss or storage durability.
The raw job count also conservatively blocks queued/running restored archival rows;
the helper does not interpret quarantine flags. Do not rewrite those rows to bypass
the refusal; a restored-library exception needs separate scoped review.

## Freeze the concrete operation before touching the running app

Record the reviewed candidate commit, current running commit/source/interpreter,
exact managed serving session/PID/port, library, project parents, checkpoint location,
test evidence and intended schema changes. Check both source trees for local changes.
Do not synchronize the environment, read another workspace's `.env`, reset a checkout,
guess a process by port alone, or assume installed source equals serving source.

Save edits/notes, and verify all local admission/media/processing/control lanes and
backup/move/restore operations are idle. Retained remote owners/uncertain submissions
need explicit interpretation: this helper preserves their database bytes, does not
reconcile them, and its local idle count does not prove remote work has ended.
Nonempty local recovery metadata needs review under the accepted recovery plan.
This procedure has no automatic exception for changed jobs/settings or new profiles.

## Concrete checkpoint commands

The placeholders deliberately do not name a real library. Use the reviewed helper
and existing interpreter; no `uv sync`, package installation or persistent environment
change is needed. Choose a short unsynced private checkpoint parent, with separate
new folders for every capture.

```powershell
$CheckpointPython = '<absolute existing Python executable>'
$CheckpointScript = '<absolute reviewed checkout>/scripts/activation_checkpoint.py'
$LibraryRoot = '<absolute approved existing library>'
$OriginalRoots = @('<absolute approved current project-media parent>')
$CheckpointParent = '<absolute existing private checkpoint parent>'
$CheckpointStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffffffZ')
$OnlineCheckpoint = Join-Path $CheckpointParent "online-$CheckpointStamp"
$OfflineCheckpoint = Join-Path $CheckpointParent "offline-$CheckpointStamp"
$PostCheckpoint = Join-Path $CheckpointParent "post-$CheckpointStamp"
$OriginalArguments = @()
foreach ($OriginalRoot in $OriginalRoots) {
    $OriginalArguments += @('--original-root', $OriginalRoot)
}

# Read-only source backup while the existing app is still open and locally idle.
& $CheckpointPython $CheckpointScript capture --library $LibraryRoot `
  @OriginalArguments --stage online --output $OnlineCheckpoint
if ($LASTEXITCODE -ne 0) { throw 'Retain the partial checkpoint; defer activation.' }
```

Inspect the receipt and independently save its SHA-256. A hash without an independently
trusted receipt is not authenticity proof. Check current identities of **all** missing
originals, every local recovery entry and every retained remote obligation. Recording
the facts does not approve missing data or recover/delete it.

Only after the concrete idle transition is authorized: gracefully stop the exact
verified serving session, wait for its worker/control drain and process exit, and keep
the launcher stopped. Do not proceed after an uncertain shutdown. Take the authoritative
offline copy; the helper must acquire the existing app lease and holds it for this command.

```powershell
& $CheckpointPython $CheckpointScript capture --library $LibraryRoot `
  @OriginalArguments --stage offline --output $OfflineCheckpoint
if ($LASTEXITCODE -ne 0) { throw 'No authoritative offline checkpoint; keep the app stopped.' }
& $CheckpointPython $CheckpointScript compare `
  --before $OnlineCheckpoint --after $OfflineCheckpoint `
  --output "$CheckpointParent/online-to-offline-$CheckpointStamp.json"
if ($LASTEXITCODE -ne 0) { throw 'Inspect all observed changes before proceeding.' }
```

The helper releases the lease after capture. Keep every other launcher/writer stopped
through the later source transition. Its receipt never grants ongoing exclusion.
Recheck the full local idle/operation state immediately before any authorized transition.

## Later source transition and startup are separate operator actions

After reviewing the concrete backup/preservation result, the previously approved source
transition may use `git -C <verified running checkout> merge --ff-only <exact reviewed commit>`.
Verify the resulting commit and existing interpreter again. Do not switch or reset unrelated
worktrees. No main merge is performed by this helper or by its tests.

Use the existing reviewed managed-session launch command with the exact same library and
project-parent arguments, `--no-browser`, and the existing port. Ensure the child's module
path resolves to the verified checkout's `src`; set `PYTHONPATH` only in that child's
environment if required. Do not change shared environment settings or reuse an old command
whose source/library identity is unverified. This document intentionally does not create a
background launcher, infer a PID or execute a startup command.

Before reopening/reloading the UI, verify the exact listener/source, local health/storage,
and served static-byte hashes/`no-store` headers using the already approved read-only local
checks. Do not enqueue, upload, retry, Stop or dispose of remote output as an activation test.

```powershell
& $CheckpointPython $CheckpointScript capture --library $LibraryRoot `
  @OriginalArguments --stage post --output $PostCheckpoint
if ($LASTEXITCODE -ne 0) { throw 'Post-startup checkpoint refused; preserve all evidence.' }
& $CheckpointPython $CheckpointScript compare `
  --before $OfflineCheckpoint --after $PostCheckpoint `
  --output "$CheckpointParent/offline-to-post-$CheckpointStamp.json"
```

Comparison is deliberately strict across all tables/schema, original identities/bytes and
local recovery metadata. Schema additions, profile backfill or routine status observations
are reported as differences, never silently allowed. Review any expected changes against
the exact immutable offline snapshot with separate scoped evidence. This helper does not
implement a migration allowlist or establish that unchanged hashes mean the new app is correct.

Never restore over newer work or start an older schema against an upgraded database. A rollback
needs its own idle preservation/recovery plan, retaining all newer jobs, results and files.
Keep both online/offline copies, the old source/library/project folders, manifests, receipts,
logs, source/interpreter metadata and any post-startup observations. No backup or UCloud Trash
is automatically removed. Complete portable restore, real devices/cloud and institutional
acceptance remain outside this checkpoint protocol.

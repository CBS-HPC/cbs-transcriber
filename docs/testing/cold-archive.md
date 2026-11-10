# Cold file archive for a later authorized Windows activation

This is an operator procedure, not an activation or backup schedule. Do not run it on a real
library until that exact scope is authorized. Synthetic checks do not establish real-library
size, storage, recovery, Cloud Files, power-loss or institutional acceptance.

`scripts/cold_archive.py` uses only Python's standard library and the reviewed sibling
`activation_checkpoint.py`. It never imports Store/the app, reads the OS keyring or another
workspace's environment, starts processing, calls the network, restores, merges or restarts.
Its `capture` command **does read and copy all ordinary file bodies in the supplied roots**,
including hidden files and raw legacy artifacts. It is a private archive, not a sanitized
export or a credential-exclusion proof. Supply only approved, owned roots, and keep it out of
Git/cloud synchronization. No secrets need to be displayed or supplied to the helper.

## What is copied and verified

The existing serving process must be gracefully stopped and all other launchers/writers
kept stopped. The helper nonblockingly acquires the existing initialized `server.lock` byte
and holds it through the whole operation. It never creates or writes that lock file.
This excludes cooperating app servers; unrelated processes and filesystem writers are not
stopped. Quiescence remains the caller's obligation.

The helper preflights supplied-root metadata (including file/directory streams and file
availability attributes) before reading original bodies. It then creates the existing
checkpoint's read-only SQLite backup-API snapshot,
including committed WAL pages. It checks integrity, foreign keys, schema and typed table
hashes, and freshly inventories each original by current recording ID. Raw queued/running
jobs refuse, including restored archival rows; do not rewrite them to bypass the gate.
Nonempty `local-deletions` also refuses: staged payloads can live outside ordinary project
roots and need their own accepted recovery plan.

The caller must supply exactly the external `projects.storage_dir` roots recorded in that
copied database. A missing/extra/duplicate root refuses. NULL/empty/library storage uses the
library root automatically. Nested roots are copied once through the containing supplied
root; all project bindings remain in the snapshot. A project root containing the central
library is refused. Existing projects may live outside the default new-project parent, so
that parent is never substituted for the database inventory.

The archive copies every regular file, hidden child and empty directory under the library
and those roots, including project snapshots, original media, raw producer artifacts, local
runtime/preservation files and earlier backups. The library's `server.lock` alone is excluded;
a stored lock file would not grant a reusable server lease. Files outside those roots,
OS-keyring credentials and external model/runtime caches are not captured.

Before copying, every source file is freshly hashed with stable path/handle identity checks.
Each checkpoint original's exact path/availability/size/hash must also match its source
baseline and copied entry. A change or disappearance between the SQLite checkpoint and
file baseline refuses, rather than publishing stale missing-original claims. Verification
rechecks this link using archived metadata only.
Before the cold file baseline, another read-only SQLite transaction must match every
initial checkpoint schema/table/typed-row fingerprint. Exact DB/WAL presence, identity,
mtime and byte hashes bracket that observation, then must match the cold baseline too.
An intervening DB commit cannot silently leave the checkpoint and copied raw database at
different logical states. SHM receives its first strict file anchor after this observation,
allowing the read-only connection's bookkeeping to finish before copying starts.
Copies use exclusive creation, bounded streaming and flush/fsync. Each copied root is read
back and compared by exact relative names, types, lengths, SHA256 and file mtime. Every source
root is then freshly inventoried/hashed again, including file identities, followed by another
copied-root verification. Only after those checks does `ARCHIVE.json` publish create-if-absent
using the checkpoint helper's flushed pending-file/hard-link protocol. Its SHA256 is returned;
retain it independently. The archived checkpoint separately provides a self-contained SQLite
database plus typed logical fingerprints; the ordinary root copy retains the raw DB/WAL/SHM
files together as observed. Do not mix those database sets or open the raw archive as an app.

Directories/regular files with symlinks, junctions or any reparse point are refused, including
Cloud placeholders. Windows EFS-encrypted, offline and recall-on-open/data-access nodes are
refused before their body is read. Ambiguous `0x40000` stat flags also remain refused.
NTFS extended attributes (EAs) are checked by byte count even when no stat flag indicates
them, and refused because this copier does not preserve/verify them. NTFS alternate data
streams are detected and refused,
including `Zone.Identifier`; ordinary Python file copying cannot preserve them. Failure to
enumerate streams also refuses. No hydration, attribute clearing or secret-file filtering is
attempted to force a copy through. Use a separately reviewed storage-specific backup plan
when these refusals apply.

### The Windows `0x40000` alias

Microsoft assigns the same bit to internal `FILE_ATTRIBUTE_EA` and enumeration-only
`FILE_ATTRIBUTE_RECALL_ON_OPEN`; interpreting it requires the API context. A stat flag
alone does not establish remote recall or absence of EAs. Conversely, real NTFS EAs can
exist while both stat and enumeration flags are ordinary `0x20`. See Microsoft's
[attribute constants](https://learn.microsoft.com/en-us/windows/win32/fileio/file-attribute-constants).

The helper therefore checks a fresh exact-path `FindFirstFileW` result for availability/
reparse flags before any explicit metadata handle open. It then requests only
`FILE_READ_ATTRIBUTES`, `OPEN_EXISTING`, `OPEN_REPARSE_POINT`, `OPEN_NO_RECALL` and directory
backup semantics, and queries `FileEaInformation`: a four-byte EA-size result, with no
file-data read rights or EA names/values. Unavailable, short or pending metadata results
refuse; a positive EA size reports `ExtendedAttributesUnsupported` without names, values
or paths. This is metadata qualification, not hydration or media-integrity acceptance.
See [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
and [QueryInformationFile](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/nf-wdm-zwqueryinformationfile).

Python's ordinary stream copy plus `copystat` can retain identical data bytes while losing
NTFS EAs. Do not clear attribute bits or accept an enumeration-only positive result to
force an EA-bearing file through this procedure. Microsoft documents
[CopyFileEx](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-copyfileexw)
as preserving EAs, but that API alone does not provide this procedure's exact EA verification.
An EA-preserving backup path needs separate implementation/review; it is not enabled here.

New captures record `metadata_policy=windows-ea-size-zero-v1`; qualified verification
requires that exact producer marker as well as the trusted manifest hash and current archive
checks. Earlier cold-v1 captures lacking it do not establish source EA absence merely by
passing later byte checks. Do not add the marker to old receipts or invent lost metadata:
retain them as earlier evidence and take a new qualified capture after resolving the cause.
The marker is producer evidence, not authentication or a historical backfill. This qualified
capture/verify route requires Windows; other platforms need their own metadata policy.

## Prepare the approved values, then capture once

First finish the independent clean-build, exact process/source/library identity and idle
checks in [activation checkpoints](activation-checkpoints.md). Save all drafts, stop the
exact managed app, wait for accepted request/worker/control drain and process exit, and keep
all launchers stopped. The helper's lease does not independently prove that drain or absence
of remote work. Retained remote owners/uncertain submissions must already have an understood
preservation plan; this tool never interprets or disposes of them.

Bind `ProjectRoots` from the fresh approved SQLite/project inventory, not guessed folder
names. All paths must be absolute ordinary local paths with no linked/reparse ancestors.
The archive's parent must already exist, be short/private/unsynced, and be outside every
source root; the final archive folder must not exist. Avoid long user paths until separately
qualified. Enough free space is required for all existing bytes, earlier backups and a
second SQLite snapshot. The OS may still exhaust space later, leaving an incomplete copy.

The following is a command template only; no real library is named:

```powershell
$ArchivePython = '<existing approved Python executable>'
$ArchiveScript = '<reviewed checkout>/scripts/cold_archive.py'
$LibraryRoot = '<exact approved stopped library>'
$ProjectRoots = @('<exact current external project root>', '<other root if present>')
$ArchiveFolder = '<existing private short parent>/<new unique archive folder>'
$ProjectArguments = @()
foreach ($ProjectRoot in $ProjectRoots) {
    $ProjectArguments += @('--project-root', $ProjectRoot)
}
& $ArchivePython $ArchiveScript capture --library $LibraryRoot `
  @ProjectArguments --output $ArchiveFolder
if ($LASTEXITCODE -ne 0) { throw 'Retain the partial archive; activation remains held.' }
```

Use an empty `ProjectRoots` array when no external project root exists. No `uv sync`, package
installation or shared environment change is needed. Keep both sibling scripts from the
same reviewed revision. There is no application/database path inferred from ambient defaults.

Inspect the final metadata privately, retain the returned manifest SHA256 outside the archive
and record missing-original IDs. The helper preserves every available owned byte and records
preexisting missing originals; it does not recreate absent recordings. A verified archive
with missing originals is **not fully recoverable for those recordings**. A changed missing
identity never passes by preserving only its count.

## Verify independently without opening the source

```powershell
$TrustedManifestSha256 = '<independently retained 64 lowercase hex characters>'
& $ArchivePython $ArchiveScript verify --archive $ArchiveFolder `
  --expected-manifest-sha256 $TrustedManifestSha256
if ($LASTEXITCODE -ne 0) { throw 'Archive verification failed; do not activate from this claim.' }
```

`verify` requires a bounded manifest matching that trusted hash, exact copied-root identities,
every declared file/directory and no unexpected entries. It rereads all copied file bodies
and verifies the separate SQLite checkpoint's bytes, integrity/FKs/schema/typed hashes.
Absolute source paths inside the receipt are metadata and are **never followed**. No Store,
queue or executable historical job is opened. The source library may be unavailable.
Hash/reference custody is still the caller's assertion, not authentication or encryption.

Success proves this scoped archive's observed bytes/names and checkpoint consistency. The
receipt and verifier deliberately retain `restore_ready=false` and `activation_authority=false`.
File-mtime preservation is checked, but Windows ACLs, ownership, inherited security, creation
times, directory times, hard-link topology, sparse/compression layout, external streams and
runtime/credential continuity are not reproduced. Copies are private ordinary files; approved
encryption/access control on their destination remains an operator responsibility.

## Interrupted, changed or failed copies

No operation deletes or moves source data. Output directories/files are created exclusively.
An interruption, refused file, I/O/space/permission failure or detected source/copy change
before final publication retains the partial output with **no published `ARCHIVE.json`**.
It is not a successful archive.
Do not retry into it, overwrite it or treat its checkpoint alone as a completed cold copy.
Use a new output folder after resolving the cause; retain the partial one for inspection.
The tool has no resume, cleanup, mirror, prune or recursive-delete command.

A lost reply/crash after final manifest publication can leave `ARCHIVE.json` even when no
successful command return was observed. Never infer failure or completeness from the return
alone: retain the copy, verify against an independently held manifest hash, and investigate
reference custody when the hash was not retained. Do not rerun capture into that directory.

The source/copy checks assume a cooperative quiescent filesystem. They are not an atomic
snapshot or a defense against a hostile writer restoring identical bytes/metadata between
observations; path/handle checks do not eliminate every reparse/path race. An unrelated
process can still write after final observation. Storage flushes/hard-link publication do
not establish crash/power-loss durability, and filesystem I/O has no hard wall-clock deadline.
Read-only SQLite opening may create/remove transient WAL bookkeeping; directory times/size
are not compared, while directory device/inode and exact final child names are. Every retained
regular file, including WAL/SHM, still requires the exact before/after bytes and file mtime.
If those sidecar bytes change, capture refuses rather than waiving their preservation.
Keep the originals and old project/library folders intact. Real-media scale/path and external
disk acceptance must be checked before relying on this procedure for actual activation.

## Recovery is a separately authorized operation

This tool provides a verified raw file archive and independent self-contained DB snapshot,
not an in-place restore command. Before declaring a real copy recoverable, independently
verify it and rehearse the exact required recovery on contained copies, preserving raw versus
self-contained database sets. Do not construct Store/the app on an unquarantined raw clone:
frozen jobs and project paths may still refer to real local/remote owners. Do not rewrite
baseline paths or reopen an older schema on newer data to make a rehearsal pass.

The supported portable restore remains a different path: it creates a new disconnected
library with historical jobs quarantined, excludes several regenerable artifacts and needs
explicit playback preparation. Neither path authorizes overwriting newer reviewed work,
reinstating old remote allocation control, merging main, restarting or pushing. Preserve any
post-startup changes before considering a rollback, under a separate exact recovery plan.

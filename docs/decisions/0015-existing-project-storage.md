# 0015 — Existing-project storage changes

Accepted: 20 October 2026. Extends creation-only storage choice in 0002/0008.

The settings form disabled Choose folder for existing projects and ignored its storage field.
Enable the chooser and editable path; an unchanged current path means no move. A changed value
is a new parent folder. Save creates a dedicated subfolder with the existing project's ID prefix.
Project name and classification remain separate settings operations.

Under the shared queue and snapshot locks, reject any queued/running job in that project.
Copy only its ID-owned recording and export directories, including originals, raw responses,
imported transcripts and prepared media. Switch SQLite's storage_dir after successful copying,
then regenerate the project snapshot and remove only the known old copies. Legacy projects in
the library root move only their own recording/export IDs, never the central database or another
project. File imports share the snapshot lock; transcript imports/deletions already use the
queue lock. New worker claims wait during relocation; already-running work in other projects
can continue. This is a local request doing filesystem copying, with a visible moving message;
there is no transfer-time estimate or background move/resume service.

Reject relative paths, existing destination folders, destinations inside source data, symlinks
and junctions. Resolve and verify ownership before recursive cleanup. Failed copies leave the
original location authoritative; failed post-commit finalization retains both locations and
returns a warning. Cleanup errors preserve the active new copy and report retained old files.
No schema migration or transcript rewrite is needed. SQLite, credentials and library settings
stay in the central local library. External original source files are unaffected.

A process/power failure can leave an incomplete unused destination, or old copies after a
committed switch. The database still selects the authoritative location. Do not automatically
overwrite or delete an existing target on retry; choose a new parent or inspect that directory
manually. Moving large libraries needs temporary disk capacity for both copies. Arbitrary
unmanaged files in the old project folder are retained there.

Validation: synthetic originals/raw responses/edited results survive relocation and Store
reopening; copy failure, an exception after commit, active jobs, nested/occupied destinations
and legacy project isolation are tested. Chromium exercises the existing-project chooser,
saving a new location and reopening settings. The Windows dialog is mocked in automation;
its actual foreground behavior and cross-volume filesystems still need manual validation.

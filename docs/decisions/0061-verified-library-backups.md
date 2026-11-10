# 0061 — Verified local library backups (step 1 towards UCloud storage)

Accepted 24 October 2026. Follows docs/ucloud-storage-investigation.md, option (b).

The researcher wants the library and projects kept on UCloud as secure storage. The
investigation found that a live SQLite library must not sit in a synced or mounted UCloud
folder. Verified snapshots stored on UCloud are the safe route. The first step, a verified
local backup, is useful on its own and is the unit any later upload copies.

## Decision

- **Back up library** writes a new folder `library-<UTC time>` under `<library>/backups/`, or
  under another absolute folder the researcher chooses. The folder holds:
  - `library.sqlite3`: a consistent snapshot of the running library, made with SQLite's
    online backup API. The app keeps running.
  - `manifest.json` (`cbs-library-backup-v1`): the schema, row counts per table, the result of
    the integrity and foreign-key checks, the snapshot size, and every original recording
    (recording, project, file name, size, modification time, SHA-256). Missing originals are
    listed separately. The manifest also lists what is deliberately excluded.
- **Verified or not kept.** The snapshot is opened read-only and must pass `integrity_check`
  with no foreign-key violations; otherwise the folder is removed and the reason is shown.
- **Originals are referenced, not copied.** They never change after import. Their hashes let a
  restore, or a later upload, prove each file. Hashes are reused from the previous backup
  when size and modification time are unchanged, so repeated backups stay fast.
- **Never in a backup:**
  - keys and tokens, which live in the OS credential store and never in the library;
  - the server lock;
  - media prepared for playback, which is recreated from the originals.
- `GET /api/library/backups` lists the library's backups read-only. `POST` creates one.

## Next steps (not decided)

- Upload a backup folder and the originals it names to a chosen UCloud project drive. This
  needs its own explicit approval, per library or per project, like Protected approvals.
- Restore into a new folder, opened through the library chooser (ADR 0051), with every
  original re-verified against its hash.
- The questions for CBS IT and the DPO in the investigation still apply before real data is
  stored on UCloud.

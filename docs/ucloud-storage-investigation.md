# UCloud as secure storage for the library and projects (investigation)

23 October 2026. This is based on public documentation and the repository. Nothing was called
on UCloud. Unverified points are marked as such.

## Short answer

UCloud is a good secure place for **copies** of the library and projects. It should not hold the
**live** library. The recommendation is a local working library plus explicit, verified snapshot
backups to a UCloud project drive.

## What UCloud offers

- **Storage.** Folders live in project drives; space is granted in GB. The current product is
  "storage" (the Weka filesystem). The FAQ still names `u1-cephfs` for sensitive data, so which
  product to use must be confirmed.
- **Labels.** Data classification labels (Private, Confidential, Sensitive) only warn users;
  they do not restrict access.
- **Trash.** Files stay in Trash until it is emptied. Deleted drives cannot be recovered.
- **No backups.** SDU makes no backups: the customer is solely responsible.
- **Security.**
  - eScience services are ISO 27001 certified.
  - Data is kept in Odense and Sønderborg.
  - Transfers use HTTPS.
  - Staff access is logged and kept for 180 days.
  - How data is encrypted at rest is not specified (unverified).
  - SDU has data processing agreements with "most Danish universities"; that CBS has one is not
    confirmed.
- **Desktop access.**
  - Sync uses Syncthing, run as a job, and does not sync Sensitive files.
  - Rsync and MinIO are also apps that run as jobs.
  - No WebDAV or SFTP mount is documented.

## Approaches

| Approach | Verdict |
| --- | --- |
| (a) Live library in a synced or mounted UCloud folder | Reject. WAL-mode SQLite must not run on network filesystems or synced copies (sqlite.org); the one-server lease only works on one computer; sync deletions reach every copy; Sensitive files would not sync at all. |
| (b) Local library plus explicit snapshots to UCloud | **Recommended.** SQLite's backup API gives a consistent snapshot of a running library. Originals never change, so each is hashed and uploaded once. Restore goes into a new folder and opens through the library chooser (ADR 0051). |
| (c) Per-project archive to UCloud and import back | Good second step; fits per-project approval. Needs a full project package format and an importer (today's ZIP holds readable exports only). |
| (d) Run the whole app on UCloud | Not suited to one person's daily use. The app has no login and binds to loopback only; API keys would have to live inside UCloud; it uses compute hours. |

## Rules to keep

- One server per library.
- Originals never change; verify their hashes on restore.
- Storage on UCloud is a new data flow: it needs its own explicit approval, per project or for
  the whole library.
- Never upload keys or tokens; a test should check that snapshots contain none.

## Proposed first step

1. A local **Back up library** command:
   - writes a consistent snapshot with the backup API;
   - runs `integrity_check`;
   - writes a manifest of projects and original recordings with their SHA-256 hashes;
   - leaves out `server.lock` and keys;
   - includes a test restore.
   This changes persistence, so it needs a numbered ADR.
2. Then an in-app upload to a chosen UCloud drive folder, behind its own Protected-style
   approval.

## Status and step 2 plan (24 October 2026)

Step 1 is built (ADR 0061): **Library → Back up library now** writes a verified snapshot and a
manifest of hashed originals. Step 2 is planned but not built. It sends research data to a new
place, so it waits for the researcher's decision and the CBS IT/DPO answers below.

- **Approval.** One explicit setting per library, off by default: "Store backups on UCloud",
  naming the UCloud connection and drive folder. Libraries with Protected projects also need
  that connection to be approved for Protected. Each upload is started by the researcher.
- **What is uploaded.**
  - The backup folder (`library.sqlite3`, `manifest.json`).
  - Every original named in the manifest. It goes under its SHA-256 name in a shared
    `originals/` folder, so each file is uploaded once across backups.
  - Every upload is confirmed by size, and by the hash the manifest names.
- **Layout on UCloud.**
  - `<drive>/CBS-Transcriber-backups/<library id>/library-<time>/` for the database and
    manifest;
  - `<drive>/CBS-Transcriber-backups/<library id>/originals/<sha256>` for originals;
  - nothing else.
- **Restore.** Download into a new local folder, re-verify every hash, then open the copy
  through the library chooser. The live library is never replaced in place.
- **Deletion.** The app never deletes or empties UCloud Trash; old backups are removed by
  the researcher in UCloud.

## Questions for CBS IT and the DPO

1. Does CBS have a data processing agreement with SDU eScience? Is UCloud approved for interview
   recordings, including special-category data? Which storage product should be used?
2. How is data encrypted at rest? Is encryption before upload required, and who holds the key?
3. Retention: Trash, deletion timelines and project expiry.
4. Who can read project drives (PI, admins), and is that access audited?
5. Upload token scope and lifetime.
6. SDU makes no backups: is a second copy required besides UCloud?

## Sources

- https://docs.cloud.sdu.dk/intro/security.html
- https://escience.sdu.dk/index.php/organization/iso-27001/
- https://docs.cloud.sdu.dk/guide/resources-products.html
- https://docs.cloud.sdu.dk/help/faq.html
- https://docs.cloud.sdu.dk/guide/files-opt.html
- https://legal.cloud.sdu.dk (§11, §13)
- https://legal.cloud.sdu.dk/terms/data-processing-terms
- https://docs.cloud.sdu.dk/guide/synch.html
- https://www.sqlite.org/wal.html
- https://www.sqlite.org/useovernet.html
- https://www.sqlite.org/howtocorrupt.html
- https://www.sqlite.org/backup.html
- https://docs.syncthing.net/users/faq.html

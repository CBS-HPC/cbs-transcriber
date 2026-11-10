# T91 proposal: portable UCloud backups and restoration into a new library

27 October 2026. **Proposal only. T91 remains pending after T90.** This document changes no
accepted behavior, schema, retention policy or deployment. No private library was opened,
credential file read, UCloud request sent, file uploaded or restore performed for this review.
Institutional decisions below remain unresolved; this is not CBS IT/DPO approval.

## 1. Outcome and smallest complete first stage

Keep the working library local. Offer a manual **Prepare portable backup → Upload to chosen
UCloud folder → Verify → Restore into a new empty local library** flow. A restore preserves
research history and usable originals, and cannot control the source library's remote jobs.
Use the released UCloud client and direct storage transfers; no GPU/CPU allocation, SSH,
remote script, live database mount, synchronization service or scheduled retention is needed.

The first upload should be self-contained: one immutable backup directory containing every
object required by its manifest. Deduplicate identical bytes within that backup only. The
earlier investigation's shared `originals/<sha256>` store would require cross-backup reference
tracking, concurrent-publication rules and garbage collection before safe removal. Defer that
optimization; repeated upload/storage cost is explicit in the preview. This is a proposed
refinement of an unimplemented option, not a change to ADR 0061.

The first release supports a whole-library preservation backup. A project-only selection needs
a separate relational subset/export contract; it cannot be implemented by filtering the media
list while sending the whole SQLite database. Existing readable ZIP/research exports are not
substitutes for a library backup.

## 2. What exists, and what a portable backup must add

`backup.py` currently produces `library.sqlite3` using SQLite's backup API and a
`cbs-library-backup-v1` manifest containing schema, table counts, integrity status, database
size, and original-media size/mtime/SHA-256. Originals are **referenced, not copied**. Missing
originals are reported. The manifest does not hash the database. Unchanged original hashes
are reused on matching size and mtime; this is an optimization, not independent byte
verification. The tests cover a consistent copy, known credential exclusion, hash reuse and
failed-copy cleanup. There is no uploader, portable restore or remote retention implementation.
See [implementation](../../src/cbs_transcriber/backup.py), [tests](../../tests/test_backup.py),
[ADR 0061](../decisions/0061-verified-library-backups.md) and the
[earlier investigation](../ucloud-storage-investigation.md).

A new versioned portable format must contain the following, with a manifest entry for every
included file. Keep v1 readable and accurately described; do not silently reinterpret it as a
complete portable backup.

| Contents | Preservation rule |
| --- | --- |
| Consistent database | Preserve exact logical rows: reviewed/trashed results, immutable versions and hashes, independent notes/history, collections, topic evidence/reviews, hints, frozen jobs, batch journals and available processing evidence. Include schema/app format versions and a SHA-256 for the final database object. |
| Every original recording | Copy the actual bytes, keyed by recording/project ID and a generated relative destination; retain original display filenames as metadata. Rehash the copied bytes instead of trusting the v1 mtime cache. |
| Imported transcript sources | Include managed `sources/` files as literal bytes. Present code does not bind each generated source filename to a particular result: retain available files and mark that attribution gap; do not invent a binding. |
| Raw provider responses | Include native `ucloud-<job-id>.json`, API `response-*.json`, and their exact owned run context. Reviewed text in SQLite does not replace raw responses used for evidence and hint rebuilding. |
| Local enhancement/repair evidence | Include requests, stage/window JSON, terminal outcomes, explicit-attempt archives and preservation evidence referenced by frozen jobs/results. Retain existing T43 artifacts through its eventual artifact index/allowlist; do not claim that missing legacy evidence was captured. |
| Recovery metadata | Include role/path/object mappings, explicit legacy evidence gaps, required app/schema versions, exclusions, unresolved source-side remote obligations and the restore contract. No credential or transfer-session secret belongs here. |

Files needed only to recreate browser playback (`audio.mp3`, generated video, thumbnails),
decoded/chunk audio derivable from originals and exact retained bounds, regenerated project
JSON/NVivo/readable exports, model caches/weights, environments, executables, prior backup trees
and Git/source checkout are excluded. A file referenced as non-reproducible processing evidence
is included even if its directory also contains reproducible cache files. An explicit role
inventory decides this; an extension-only filter or unrestricted directory walk does not.

Exclude OS keyring contents, environment/config secret files, SSH private keys, credential
exports, control Authorization, presigned/session URLs or tokens, `server.lock`, SQLite
`-wal`/`-shm`, temporary/partial files and the machine's library registry. The archive may still
contain interview text, private notes/reasons, provider endpoints, filenames and historical
paths in preserved database fields. It is confidential research data, never a public export.
Do not redact arbitrary transcript text to make a false promise that all text resembling a
secret has been removed. Test known app credential fields and synthetic canaries separately.

A physical SQLite snapshot can retain deleted bytes in unused pages. For the cloud database,
produce a compacted copy from the verified local snapshot, then compare schema and exact
logical table fingerprints and rerun integrity/foreign-key checks. Do not upload the
uncompacted snapshot as an additional object. Keep the original local snapshot/receipt for
preservation; compaction must not rewrite immutable result payloads or their hashes. Known
structured credential slots must be absent; unknown schema/legacy credential layouts require
review instead of guessed sanitization. Free-text research content remains literal.

Missing originals or missing files promised by a captured evidence receipt block a complete
portable backup. Legacy evidence that was never captured is recorded as a provenance gap,
not fabricated or described as a newly missing original. A partial salvage format can be a
later feature; it must not receive the first stage's complete/verified label.

## 3. Capture, integrity and immutable publication

Preparation uses the serving library's lease and a writer gate, not a second `Store` opening
the live database. For this first stage, wait until local jobs, batch coordinators, transfers
and snapshot writers have settled. Block new jobs, imports, edits, deletion and storage moves
while preparing the local package; keep read-only browsing and backup cancellation available.
Do not hold a SQLite transaction or queue lock across network transfer. After the local
package is sealed, normal work can resume during its upload.

Take the database snapshot, enumerate only ID-owned managed files against that snapshot,
copy/hash them into local staging, validate before/after file identity/size and close/flush
all handles. Reject symlinks/junctions and escaped paths. A file change or missing required
file fails preparation; no partly captured set becomes ready. Unresolved source-side remote
jobs may be represented as such only when no local coordinator is writing. Outputs still
only on UCloud are outside this local snapshot and must not be described as preserved.

Proposed layout under the exact selected drive and generated library/backup UUIDs:

```text
<chosen-parent>/CBS-Transcriber-backups/<library-uuid>/<backup-uuid>/
    objects/<sha256>       # database and included files, each immutable
    manifest.json         # bounded versioned role/path/size/hash inventory
    COMMITTED.json        # uploaded last; names backup UUID and exact manifest size/hash
```

The manifest contains generated relative destinations, logical owner IDs, content roles,
object hashes/sizes, capture time, format/schema/app versions, table fingerprints, scope,
exclusions and provenance gaps. Do not add absolute source paths or project titles to the
outer remote folder/commit marker. Preserved database content may contain historical paths;
that is part of the reviewed data classification.

Journal the frozen destination, backup UUID, manifest bytes/hash, object inventory and owned
folder receipts locally before sending bytes. Freeze the destination's server, UCloud project,
drive ID and parent path. A changed profile cannot redirect an interrupted upload. Recheck
current permission before each private transfer; revocation blocks further upload but permits
read-only verification/recovery of already transferred objects.

Create a fresh exact folder with conflict rejection. An existing unknown folder is not proof
of ownership. A lost folder-creation reply is reconciled at that exact UUID path using bounded
parent listing and existing ownership receipts; an ambiguous folder remains visible for
manual reconciliation. Neither HTTP 400 nor a failed listing proves absence.

Upload one object at a time initially. Stream from the sealed local copy, compare remote size,
then **download the exact object back and stream its SHA-256** before recording verification.
Current file metadata/upload acknowledgements prove neither a remote SHA-256 nor a complete
portable backup. Do the same for the manifest. Publish `COMMITTED.json` only after all objects
are verified; read it back and validate its exact binding. A folder without a valid commit
marker is incomplete and is never offered as a restorable verified backup. A lost marker
reply is resolved by reading that same marker, never by assuming upload failure.

The checksum manifest detects corruption relative to a trusted manifest; it does not
authenticate an entire replacement by someone with drive write access. Upload produces a
small independent receipt containing backup UUID and manifest/commit hashes, to be retained
outside that backup failure domain. First-stage verified restore requires this trusted receipt
or a later institutionally approved signed equivalent. Do not claim independent authenticity
when only a mutable remote manifest is available. Long-term receipt/key custody is an IT/DPO
decision, not something to hide in the same remote directory.

## 4. Released UCloud capabilities and transfer recovery

The installed dependency is `ucloud-workflow` **0.1.9**, revision **v0.1.9**, commit
`a521fe7546e2735d0e8d9872c2486e02ad6b6fc8`. Its local release tag resolves to that commit.
Only installed source and those release documents were inspected; this is not a live provider
capability test.

| Confirmed local capability | Consequence for T91 |
| --- | --- |
| `UCloudClient` has project/drive/file browse, file retrieval, conflict-rejecting folder creation and generic `request`. Setup helpers provide writable-drive discovery and contained storage-path validation. | Reuse explicit settings/client construction and read-only discovery. Backup storage must not require an ASR application, template, model or compute product. |
| The release has no first-class general HTTP upload/download, persistent transfer-resume, provider hash, atomic directory publication or file-Trash helper. Its `transfer.py` workflow uses SSH/SCP and compute jobs. | Reuse/extend the app's existing direct file-transfer seam rather than invoking a job workflow. Do not infer stronger provider guarantees from a generic HTTP method. |
| The app implements `/api/files/upload` with WEBSOCKET_V2/CHUNKED, a separate transport client, bounded chunks and remote size checks; downloads obtain a session then stream from its endpoint. | Factor storage-neutral streaming helpers. Keep control credentials off transfer hosts, redirects disabled and diagnostics curated. Backup downloads must stream to disk rather than use transcript-sized in-memory helpers. |
| Existing `trash_owned_folder` calls `/api/files/trash` and confirms parent absence. | A later authorized removal can reuse this exact-owned-folder receipt pattern. No permanent file DELETE or Trash purge is assumed. |

Sources: release [client](https://github.com/CBS-HPC/ucloud-api/blob/a521fe7546e2735d0e8d9872c2486e02ad6b6fc8/src/ucloud_workflow/client.py),
[API/setup reference](https://github.com/CBS-HPC/ucloud-api/blob/a521fe7546e2735d0e8d9872c2486e02ad6b6fc8/docs/reference/api.md),
[token guide](https://github.com/CBS-HPC/ucloud-api/blob/a521fe7546e2735d0e8d9872c2486e02ad6b6fc8/docs/guides/token-management.md),
[delivery implementation](https://github.com/CBS-HPC/ucloud-api/blob/a521fe7546e2735d0e8d9872c2486e02ad6b6fc8/src/ucloud_workflow/delivery.py),
and app [transfer/Trash implementation](../../src/cbs_transcriber/ucloud_native.py).
The generic delivery helper recursively packages supplied trees; it is not a safe backup
inventory or restore validator by itself.

Construct `CloudSettings(server=..., project=..., token=...)` explicitly. Never call
`Settings.from_env()`, read another workspace's `.env`, auto-create/revoke tokens, change ACLs,
create a drive/public link, or inherit model-upload approval as backup approval. Tokens remain
in the existing server-scoped credential store and memory. Session secrets are transient;
omit them from durable journals, manifests, logs, browser storage and restored data. Permission
names and token lifetime come from supported metadata/approved operator policy, not guesses.
Validate returned HTTPS/WSS transfer endpoints against the frozen server/provider's approved
endpoint policy, reject embedded credentials and unapproved origin changes, and never forward
control Authorization or follow redirects to make a transfer work.

Durable operation states should be small and explicit: preparing, ready, uploading,
verifying, complete, interrupted and cancelled. Store per-object receipts and a monotonically
claimed local owner/epoch; serialize mutation of one backup folder. Cancellation stops this
local transfer and retains both staged local bytes and any partial remote tree. It does not
delete originals or other backups. Switching libraries/deleting the source package is blocked
while its transfer owns those files.

Retry reconnects to the same UUID and exact manifest. Reuse an existing remote object only
after full size/hash verification. Provider transport offsets/acknowledgements are session
details, not durable checksum receipts: the first stage may restart the affected object from
byte zero in the same owned folder while skipping verified objects. Explicitly distinguish
this from byte-range resume. Do not replay an uncertain create/commit blindly, overwrite an
unknown object, or substitute another snapshot. Quota/token/network errors retain resumable
state; bounded retries preserve the original curated failure without printing session URLs.

## 5. Restore into a new, empty, local library

1. Select the committed backup, its trusted receipt and a new absolute empty unsynced local
   destination. Refuse the current library, a nonempty destination, nested source/staging
   paths and symlink/junction escapes. Preview required disk space, projects, recordings,
   schema compatibility, sensitive scope and known evidence gaps.
2. Validate bounded JSON with unique keys, supported format/schema, strict IDs/hash/size
   fields, object/count/total-byte limits and complete role mappings. Reject duplicate,
   case-colliding, reserved-device, absolute, traversal and link paths. Do not extract an
   arbitrary archive or follow paths supplied by a remote manifest.
3. Download into a uniquely owned staging directory beside the destination. Each object uses
   a partial file, a streaming byte limit and a hash check before atomic promotion. Interrupted
   restore resumes only the same trusted manifest; incomplete files restart without modifying
   the source library. Validate original/evidence mappings and every required object.
4. Inspect the database read-only with extension loading disabled. Require a supported known
   schema object inventory, no unexpected triggers/views, successful integrity/foreign-key
   checks, table fingerprints, exact version-payload hashes and active-result mirrors. Never
   execute a remote SQL dump or open an unknown schema in `Store` just to see whether it works.
5. Preserve the downloaded database object unchanged as restoration evidence. Build a separate
   working copy. Relocate only operational project storage paths to generated ID-owned folders
   under the new root, place recordings/sources/evidence accordingly, and record the path map
   in a restore receipt. Preserve project/recording/result/version IDs, Unicode payloads,
   source hashes, Trash visibility, review history and frozen producer parameters exactly.
6. Give the restored instance a fresh library/backup-namespace UUID and record its source
   backup UUID/hash. **Imported jobs and native/runtime allocation owners are historical,
   never execution authority.** A small persistent restoration-ownership table/receipt must
   mark all imported IDs before workers or job endpoints can operate. Historical queued jobs
   are excluded from claims; Retry, Stop, reconcile and cleanup cannot target source remote
   work, even if this computer already has credentials for that endpoint. Retain journal bytes
   as evidence. New inference requires a new producing job and freshly checked connection/
   approval. This applies equally to singleton API, legacy SSH, native and dynamic batches.
   Activity and deletion/storage/library-switch guards must distinguish archived source-side
   obligations from this restored instance's owned work. Do not manufacture source cleanup
   receipts or leave the restored library permanently blocked by an allocation it cannot own.
7. Start current provider connections disabled/unapproved in the new operational configuration;
   keep original profile/frozen-job bytes in preservation evidence. Never restore keyring,
   tokens, SSH credentials, inherited local interpreter paths or backup-upload consent. The
   restore receipt lists these deliberate operational differences without rewriting research
   history. Derived playback can be regenerated locally from originals as new media jobs;
   restoration itself performs no inference, downloads weights or private upload.
8. Revalidate the working copy and its exact permitted differences, flush/close handles and
   atomically promote staging only while the final destination remains empty. Register it in
   the library chooser only after success; opening it is a separate user action. A failed
   restore retains a clearly labelled recoverable staging operation or removes only its own
   resolved staging tree, never the destination's prior contents or source library.

An older supported database may be migrated only on that working copy with the normal backup
and preservation checks. A newer/unknown schema is refused. File hashes must verify before
migration, and both source and migrated hashes/versions are retained. Restoring a backup is
not permission to resume old jobs or to erase evidence of a remote obligation on the original
machine. Source-side reconciliation remains with that original owner.

## 6. Approval, retention and acceptance gates

There are two separate decisions: the researcher's deliberate transfer to an exact destination,
and institutional authorization for that data class and storage arrangement. Backup approval
is off by default and independent of ASR/Protected provider approval. The preview must cover
all projects and historical/private material in the database, including Trash and review
reasons; a Standard-only media filter does not make a mixed library safe to upload.

| Pending CBS IT/DPO/records decision | Proposed safe behavior until settled |
| --- | --- |
| CBS contractual/data-processing basis, allowed classes including special-category data, storage provider/product and geographic requirements | No production/private-data activation. Synthetic or licensed public round trips can validate the implementation separately after authorization. Existing investigation claims are dated evidence, not renewed legal approval. |
| Encryption in transit/at rest; whether client-side authenticated encryption is required | HTTPS with isolated credentials is mandatory for transport. Choose one approved stored-payload encryption mode before production. If client encryption is required, use an established authenticated container/tool and institutional key custody; never custom cryptography, password-ZIP or plaintext fallback. |
| Key/receipt custody, staff departure, PI continuity and disaster recovery | Define who holds decryption keys and independent manifest receipts, how recovery is tested and how access is revoked. Keep keys out of the backup they unlock. |
| Read/write/administrative drive access and audit evidence | Verify the selected identity/permissions; do not treat a classification label as an access control or silently broaden ACLs. |
| Retention length, consent withdrawal, legal holds, project/drive expiry and second-copy requirements | No automatic pruning, expiry-triggered deletion, cross-backup garbage collection or claim that UCloud is an independent institutional backup service. |
| Trash retention and final erasure, who may approve it, and provider guarantees | First stage offers no destructive retention default and never empties Trash. A future explicit removal shows the exact backup and independent-copy/hold implications, journals intent, moves only that owned whole folder and confirms parent absence. It must say “moved to Trash”, not “permanently erased”. |
| Storage cost/quota and support ownership | Show expected bytes and duplication cost; preserve incomplete transfers for explicit recovery/disposition. Define the support route and restore-drill owner. |

Implementation requires a numbered ADR and accepted SPEC/TODO changes when it is actually
authorized; this proposal does not supply them. The smallest useful validation sequence is:

1. Synthetic full portable capture with literal edits, Trash, notes, collections, topic evidence,
   exact source lineage, raw stages and legacy gaps; assert explicit exclusions and credential
   canaries, immutable originals and no live-library changes. Snapshot/file races must fail
   closed, and matching mtime must not hide changed bytes.
2. Mocked upload/publication: partial files, lost replies, wrong hash/size, exhausted quota,
   revoked approval, endpoint change, redirects, pagination and folder conflicts. No valid
   commit marker appears before full download-back verification.
3. Restore: tampered/missing/oversized files, path attacks, unknown schema/SQL objects,
   interruption and final-destination races. Prove exact research preservation and the
   allowlisted path/operational overlay. Prove imported source jobs can never submit, Stop,
   retry or Trash a remote allocation; new jobs remain usable after explicit reconnection.
4. Windows/macOS local restore acceptance, original-media playback, readable/history exports
   and a deliberately interrupted transfer/restart. Only then a separately authorized public
   UCloud round trip with measured bytes/time and owned test-folder disposition. Private data,
   encryption/key recovery and institutional retention require separate approved acceptance.

Current scope remains a design document. No upload, restore, retention automation, encryption
deployment or institutional approval is claimed.

# 0096 — Complete portable captures, isolated restoration and manual cloud retention

29 October 2026. T91c onward. Extends ADR 0061 and the deliberately narrow ADRs
0081/0083/0088/0090/0091. Public installation follows independent semantic/ownership review
and the real schema14 → schema15 integration; synthetic leaf tests are not that activation.

## Capture and data fidelity

The original v1 database/inventory backup remains readable and unchanged. A v2 capture copies
the whole current research library: a consistent SQLite backup, every live recording's
original, and available managed import/processing/preservation files. It does not reconstruct
deleted recordings or fabricate evidence an earlier producer never captured.

The serving Store's existing lease stays held. An HTTP admission gate waits for all mutating
requests and excludes new mutations; its waits run off the event loop. Only the capture POST
is exempt because that handler takes the exclusive gate itself. A separate queue admission
pause, set under the shared claim lock, refuses already active workers and prevents every
ordinary/runtime/source/admission claim during copying. Nonarchival queued/running work is
refused. Queue/SQLite locks are released after establishing the snapshot; large copies hold
the admission gates instead. Read-only browsing continues. Final byte/stat and managed-file
inventory rechecks detect observed drift under the caller-owned, quiescent-storage contract.

A separate compacted snapshot must have exactly the same typed table claims as the source
snapshot. Uncompacted deleted pages are never published. Every object is copied, hashed,
flushed, closed and published create-if-absent. Original media bodies are never hardlinked.
Deduplicated objects and expanded file mappings have separate byte counts. The manifest and
commit bind exact canonical bytes; a complete directory appears only after checks succeed.
Failed/partial operations stay clearly separate from committed packages and never remove
source media.

The role resolver derives paths from database-owned IDs and fixed managed names. Unknown
producer formats fail rather than silently disappearing. Credentials/configuration files,
logs, reproducible code/cache/playback files, prior package trees, machine registry and
backup-transfer settings/journals are excluded. The database and retained source files remain
private: literal research text is not scanned or censored as though every token-like string
were a credential. Saved credentials continue to live in the OS credential store.

Present import/producer proofs must validate, including their exact immutable origin and
promised file hashes. `not_captured` is an explicit absence sentinel. Producer evidence stays
bound to the original result creation after edits; timing evidence stays bound to its actual
timing successor/application job after later edits. Available old files are still copied;
unrecorded legacy evidence and uncaptured timing units remain declared gaps.

Retained Runtime member/attempt journals may outlive their recording/job rows. A present proof still
requires strict internal member/attempt/hash binding. Only when both recording and job are
absent and an exact retained member proves those IDs may the package declare
`absent_owner_artifacts_not_retained`; live owners, including owners with every result in
Trash, still require all promised files. Absence does not prove why, when or by whom an owner was removed; deletion intent/chronology
remain unrecorded. No gap recreates data or grants remote authority.

## Incoming bytes and checked history

The schema12 prerequisite readers keep their original contracts. New explicit catalogs cover
13, 14 and 15; schema numbers alone never expand the allowlist. SQLite opens only in the
captured-source, isolated child, with its verified process-wide SQLite heap cap before opening.
The serving process never sets that cap or opens an incoming package database.

The new policy checks exact schema/table claims, integrity/FKs, canonical payload hashes,
strict result sequence/predecessor/source ownership, latest heads, active mirrors, notes,
Working-version events, and bounded annotation/codebook immutable hashes and owner chains.
Present import bytes must parse to their original creation payload under the pinned parser.
Producer/timing/file receipts bind to their actual immutable origin and frozen object mapping.
Preservation controls have an explicit source/manifest/commit/operation binding, including a
restored library with no owners at all. Old nested source databases are preserved as bytes;
they are not recursively opened as restoration candidates.

The result means **exact captured history with these scoped consistency checks**. It does not
prove every historical wording/merge/language transformation, qualitative coding meaning,
reference custody/authentication or an institutional/private-data security baseline. Those
unchecked facets remain explicit in receipts and the interface; no generic validity flag
substitutes for a missing check.

Initial supported budgets include 8 GiB database bytes, 1 million rows/table and 2 million
total; 64 MiB SQLite heap in the child; 8 MiB per semantic JSON value and 256 MiB aggregate;
depth 24, 500,000 lexical tokens and 2 MiB strings; 20,000 mappings and 200,000 overlay owner
IDs. Import parsing additionally allows 8 MiB raw bytes, 10,000 CSV rows, 64 fields,
200,000 characters per CSV field and 25,000 normalized word occurrences. Resource refusal is
distinct from corruption and never truncates or repairs research data. These are supported
policy bounds, not an OS/Python-memory sandbox or real macOS acceptance.

## New-root restoration and schema15 quarantine

Restore requires a nonexistent final library below an existing local unsynced parent, outside
the open library and source package. Generated full IDs determine every destination. Relative
path/case/NFC collisions, links/reparse aliases and actual Windows expanded path limits are
checked before materialization. Native Win32 staging directories/files stay within 247/259
UTF-16 units; a longer choice gets a clear shorter-folder refusal, not a registry change.

A sibling operation journal binds the trusted commit reference, operation ID and exact target.
Complete copied files resume only after their hashes match; incomplete copies restart in the
owned staging area. Source `source.sqlite3`, manifest, commit and a restoration receipt remain
under `preservation/<restore-id>/`. All research rows stay exact. The contained working-copy
overlay changes only generated project roots, playback status, current disconnected/disabled
provider and machine preferences, `recordings.original_input=NULL`, and the additive approved
schema14/schema15 metadata. The raw old cache, frozen jobs and producer/history proofs remain
in the immutable source database. New source inspection uses a fresh job/generation; a NULL
working cache ignores only archived inspector rows, retaining corruption checks for live ones.

Schema15 follows the accepted schema14 migration and adds only `restored_owners`: kind
`job|native_batch|runtime_batch`, owner ID, restore ID, source backup ID, primary key(kind, ID),
no cascading foreign keys. Every imported job, native owner, runtime owner and retained member
job ID is covered. Original job/owner rows are not cancelled, rewritten or cleared.

Shared transaction-aware fences cover claims/recovery, producers/CAS writers, Retry/Stop,
remote reconciliation/cleanup/discard and late shared-owner admission before credentials or
network. Archival owners do not consume this copy's slots or block new work, storage moves,
library switching or deletion. Activity projects bounded read-only history before decoding
unsupported operational journals. Safe JSON projections prevent malformed retained bytes
from starving fresh work; raw rows remain unchanged. The same endpoint credentials on this
machine never grant source-allocation authority. Explicit new work receives fresh IDs and its
own current approval checks; playback preparation is a separate local action.

An exclusively reserved final target has `restore-pending` until every output hash and receipt
matches. Store/chooser refuse that marker. Interrupted top-level moves resume within the same
binding. Registration adds a recent library without switching the open one; a lost/refused
registration reply retains the complete target and can recover. No finalization path deletes
a committed library or source package.

## UCloud storage and retention

Storage uses the existing UCloud connection and released direct file-transfer seam, never a
compute allocation, SSH command, live mount or synchronization service. Whole-library backup
consent is separate from transcription/Protected approvals. Destination server/project/drive/
parent and approved HTTPS transfer origins are frozen in credential-free operation journals.
Current approval is checked before private transfers/chunks. Read-only recovery remains
possible after upload approval is revoked; a changed destination cannot redirect a transfer.

Each package owns one fresh conflict-rejecting folder. An uncertain creation is not silently
adopted: an explicit fresh preview may reconcile only that exact empty folder or successfully
observed parent absence. Each object and manifest is uploaded, then streamed back and hashed.
The commit is last. An uncertain/lost commit reply is settled by reading that same marker,
never by assuming failure or overwriting a different committed marker. Transfer clients carry
no control Authorization and follow no redirects. Downloads request identity encoding and
hash bounded raw body bytes, avoiding decompression amplification. Restore download requires
an independently supplied trusted reference, not a mutable remote checksum as its trust anchor.

Retention is configurable **manual Trash only**. Defaults keep three recent copies and require
30 days' age; these are technical preview defaults, not an institutional retention period.
Holds always exclude a copy. A fingerprint-bound preview verifies exact owned remote inventory
and an available hash-checked independent local package. Apply journals consent/intent before
moving only the previewed owned whole folders to UCloud Trash and confirming parent absence.
Policy and per-transfer operation locks prevent concurrent upload/retention journal races.
Audit and explicit fresh retry preserve uncertain outcomes. No scheduled deletion, local
package deletion, permanent file DELETE, Trash purge or claim of permanent erasure is offered.

CBS IT/DPO storage approval, encryption/key and trusted-receipt custody, legal holds/withdrawal
processes and real Windows/macOS/UCloud private rollout remain separate external acceptance.
Synthetic/mocked tests establish technical behavior only; no real remote transfer, recording,
backup, token or paid job was used to implement this change.

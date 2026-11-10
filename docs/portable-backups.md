# Complete library backups

Open **Library → Backups → Create complete backup** after finishing or stopping processing.
The new package contains the database, original recordings and available managed research
evidence. Older database-only inventories remain under **Earlier database-only inventories**;
they do not contain the media bodies required for a portable restore.

Save the small backup receipt outside your backup storage. Its trusted commit SHA-256 is
required when restoring. The package's own mutable checksum is not an independent trust anchor.
Uncaptured older evidence is listed honestly; the app never manufactures missing provenance.

For UCloud, select an existing connection and project-drive backup folder, approve its exact
HTTPS transfer origins, and explicitly approve **whole-library backups**, including Protected
projects, Trash and private research notes. This consent is separate from transcription
approval. Upload/resume uses the operation reference shown in the dialog. Every object is
downloaded back and hashed before the final commit marker is published. If folder creation's
reply is uncertain, **Review uncertain folder creation** can reconcile only the exact empty
folder or a successfully observed absence. It never adopts someone else's populated folder.

CBS IT/DPO approval, approved storage/encryption, receipt custody and institutional retention
decisions remain separate. Saving these settings does not establish those approvals. No GPU
job, SSH setup, model download or background synchronization is needed for backup storage.

# Restore into a new library

In **Restore a complete backup**, enter the local package folder or an exact UCloud backup
folder, the independently retained commit SHA-256, and a **new** library path below an existing
local unsynced parent. Existing libraries cannot be overwritten or merged. Prefer a short
parent path on Windows because the generated staging/project/recording paths must also fit.

Keep the operation reference when retrying an interrupted restore. The same package/target
binding resumes checked copies and interrupted finalization. A registration error retains the
complete restored library; retry registration/open that folder rather than deleting the copy.

The restored library is added to the chooser while your current library stays open. Exact
captured results, edits, notes, histories, Trash and source bytes are retained with scoped
consistency checks. This does not verify every historical editing or model transformation.
Imported processing jobs and remote obligations are read-only history and cannot control the
source allocations. Providers are disconnected. After explicitly opening the new library,
use **Prepare playback in the open library** and reconnect/configure providers for new runs.

# Manual retention

Set recent copies to keep, minimum age and held backup IDs, then **Preview retention**.
Nothing is scheduled. Only the exact previewed owned backups may be moved to UCloud Trash,
and only when an available local package still passes its hash checks. A changed policy,
hold, source package or remote inventory invalidates the preview.

The app records confirmation, intent and observed outcomes. An uncertain action requires a
fresh explicit retry preview. Local packages are retained. Moving to UCloud Trash is not
permanent erasure; the app never empties Trash. Technical defaults are not a CBS retention
mandate or approval to restore data subject to a later withdrawal/hold obligation.

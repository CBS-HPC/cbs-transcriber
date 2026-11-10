# 0139 — Preserve UCloud backup upload admission

Accepted 4 November 2026. Extends ADR 0109 and ADR 0134.

The Library uses a unique configured UCloud server/project identity and a project-drive
location, without choosing a transcription model. Different configured identities require
resolution in Settings. A user explicitly confirms the whole-library scope for each upload;
no institution-wide approval is inferred. A dedicated backup folder is suggested only on a
writable configured drive. Existing pinned profiles and approved origins remain frozen.

Before reading package bodies, the simple flow journals the exact creator receipt, destination
and operation ID as `validating`. The general upload path also admits its exact checked commit
marker before complete transport and database verification. An OS admission lease is held
through verification and upload. Target changes and initial admission share a short settings
lock, ordered after per-operation locks; no package hashing or remote calls occur under it.
Unfinished journals prevent incompatible retargeting, but consent may always be revoked for the
same destination. A later retry uses the same frozen operation. A `validating` admission may
be abandoned explicitly only while its admission lease is held; package files are retained.
Uncertain remote folder creation always needs reconciliation and never authorizes another POST.
Duplicate completed/trashed operations are refused for the same server, project and remote folder,
even when the profile ID or approved origins changed. Frozen recovery still uses the exact profile,
origins, bytes and destination originally recorded.

Stop acts on a locally displayed target revision and remains available without a remote location
listing. It revokes further requests; an already in-flight request may finish. Folder preparation
rechecks the same approval before each metadata and creation request. A changed or uncertain
destination invalidates cloud restore checks without replacing the researcher's draft; an
approval-only Stop retains the independent restore identity (ADR 0140). The browser fences
late replies, destination changes and separate restore drafts. Real UCloud transfer, Windows and
macOS crash behavior, power-loss durability and institutional acceptance remain unverified.

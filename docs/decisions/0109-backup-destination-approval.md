# 0109 — Bind backup approval and retention to the observed destination

Accepted 30 October 2026. Extends the complete-backup cloud and retention controls in
ADR 0096; no database schema or frozen transfer-journal rewrite.

Whole-library backup approval covers Protected projects, notes and Trash. A mutable provider
profile could change server or project while an older approval remained true. Retention also
reloaded a destination after checking a preview, and client creation could read a different
provider snapshot from the one validated. Synthetic tests reproduced both target races.

Persist the approved credential-free resolved identity with the backup settings: provider,
server, project, drive, parent folder and approved transfer origins. Effective approval exists
only when explicitly saved for the exact current identity. An older unbound approval remains
stored but is ineffective until the user explicitly saves approval again. Reapproval of the
same original destination restores the same public fingerprint for frozen-journal recovery;
it does not authorize a changed destination. Never put credentials into the binding.

Construct the cloud client from the same provider observation that resolves and checks its
target. Retention Apply binds the destination to the freshly checked preview, journals the
credential-free target, checks owned receipt identity and rechecks settings before each Trash
operation. A changed destination or approval needs a fresh preview. Preserve completed
partial outcomes and allow an explicit new retention preview after upload approval was revoked;
retention consent does not authorize a backup upload. Legacy journals remain recoverable.

Backup settings and retention preview UI must fence delayed replies against current form and
preview generations. Unsaved or pending settings cannot silently authorize Preview or Apply;
a late Apply result cannot clear a newer preview. Keep actionable earlier outcome evidence.

Focused tests use isolated synthetic stores, mock cloud clients and browser interleavings;
real UCloud transfers, institutional policy and platform acceptance remain separate.

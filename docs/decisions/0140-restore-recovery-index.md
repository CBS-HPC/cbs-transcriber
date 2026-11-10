# 0140 — Discoverable restore recovery bindings

Accepted 4 November 2026. Extends ADR 0107 without changing the library database schema
or the independent trusted-backup receipt.

Before a restore downloads or writes a target, publish a bounded, immutable per-operation
binding in the open library. It contains only the restore ID, source, trusted commit hash,
new-library destination and, for UCloud, the reviewed connection and destination identity (excluding upload approval); no credentials.
Flush the temporary file before an exclusive same-volume link, refuse collisions with different
bindings, and retain exact entries on failures and uncertain replies. A crash between link and
unlink can leave an exact, named temporary hard link; the next index access repairs only that
matching owned link. Failure to publish blocks the operation. Successful records remain visible
because even a successful reply can be lost. Filesystem and power-loss durability still depend
on the host volume; this does not promise directory sync on every platform or defend against non-cooperating processes racing filesystem changes.

Recovery presents prior destinations without exposing reference hashes in the normal workflow.
Choosing one restores its exact frozen choices, but requires a fresh byte check and current
matching UCloud destination before resume. A running restore or newer draft cannot be replaced
by a late history response. Old manual reference entry remains available for earlier operations.
The existing target-parent journal remains authoritative about target ownership and progress;
this index is discovery evidence, never permission to overwrite or trust unverified bytes.
Synthetic checks cover collision refusal, discovery after interruption, surviving the link/unlink
window and UI rechecking. Real machine crashes and filesystem durability need separate acceptance.

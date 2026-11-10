# 0158 — Prepare ZIP downloads independently of Export Settings

Accepted 9 November 2026. ZIP preparation with large original recordings can outlive a dialog.
Previously closing Export Settings discarded the response while the server continued working;
another click repeated preparation. Give the derived download its own Activity lifecycle.

Use a bounded server-lifetime ExportTasks manager rather than adding project exports to the
recording-owned ASR job table or changing the database schema. Project and recording ZIP POSTs
return 202 after selection validation and the exact SQLite read snapshot are established,
before format rendering/media copying. Subsequent edits cannot retarget that snapshot.
Retain legacy synchronous export APIs and the single-transcript direct download behavior.

Activity exposes preparation, cancel, ready Download ZIP and Discard ZIP. Closing the dialog
or navigating does not cancel the task; re-opening cannot implicitly repeat active preparation.
Clear finished does not erase these downloads. One export prepares at once; retain at most
three ready ZIPs until explicit discard or server shutdown. Failed/cancelled metadata is bounded.
These are temporary downloads, not complete backups or restart-durable jobs.

Write only private, exclusively created temporary files. Reuse bounded owned-original copy
and identity guards, with cancellation checks during copies and ZIP insertion. Ready downloads
reserve independently positioned descriptors, stream bounded chunks and never require a full
ZIP Blob in browser memory. Recheck path/descriptor identity before serving. Refuse Discard
while a reader or worker finalization holds ownership. Always release the reservation even if
the client disconnects before response headers, during streaming or through cancellation.

The worker borrows the serving library lease; no-worker synthetic apps take a distinct worker
lease rather than another thread's transient lease. Shutdown cancels and joins workers before
queue/lease release; ready readers delay temporary cleanup until their descriptors close.
An export admission inhibitor fences capture, file staging, project moves and library switching
without holding the manager mutex during I/O. Ordinary transcript/metadata edits remain usable.
Accepted library switching seals later starts until shutdown.

Every API retains same-origin and current-library-instance guards, including native browser
download links. Curate failures; preserve only an exact fixed known no-results diagnostic,
never arbitrary exception text or local paths. Late scoped errors cannot alter a newer dialog.
Serve every unversioned static JavaScript/CSS asset with no-store after application updates.

Verification: 22 final manager/mounted-API cases, a broader 99-pass export gate with two
platform setup skips, 16 focused/migrated browser cases and independent source review. Tests
cover frozen content during live edits, cancel/failure/discard, response disconnect cleanup,
operation fencing, source deletion after readiness and instance boundaries. Real large-media
disk/throughput and other-device acceptance remain separate.

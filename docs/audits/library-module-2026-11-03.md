# Library module audit — 3 November 2026

Scope: originally a read-only review of library selection, switching, complete backup, cloud
transfer, restore and their settings on main at `f2646b1`; implementation notes below reflect
main through `02315b3`. No real cloud transfer,
library deletion or backup retry was performed. Synthetic and real acceptance are
distinct; this is not an exhaustive filesystem or platform safety certification.

## Findings

1. **Backup recovery and feedback.** A complete backup is one long local HTTP
   request, with no Activity entry, progress or cancellation. A lost connection
   returns a generic fetch error even when the outcome is uncertain. Capture
   receipts exist, but the Library UI does not list them. Design a recoverable
   flow that lists receipts and verifies published packages before showing them
   as complete; retain incomplete packages until explicitly reconciled. Explain
   that editing is temporarily blocked by capture rather than removing the gate.
   **Implementation:** the Library now lists unchecked creator receipts and can
   check the selected published package against its receipt before reusing it.
   A connection failure reports an unknown outcome; incomplete files remain. This
   is a stored-byte check, not a restore test. Activity, percentage progress,
   cancellation and partial-copy reconciliation remain open.
   The Restore flow now combines earlier receipts and externally held receipts;
   local or UCloud bytes are checked against that independent reference before
   restore is enabled. UCloud checks stream the contents and do not lock a remote
   snapshot; restore checks again. Real transfers and restore acceptance remain open.
2. **Library-switch verification.** The chooser reloads after 20 seconds when
   any server responds, even if it still reports a different library. Require
   confirmation of the requested library identity; report a timeout otherwise.
   **Implementation:** the chooser now waits for the accepted library path and reports a
   timeout or uncertain outcome instead of reloading for another server. Synthetic tests
   cover the target, timeout, lost response and draft guards. Other open tabs and direct
   media/download requests are not fenced from a later switch; cross-tab isolation remains open.
3. **Cloud chooser describes models, not storage identity.** Backup uses the
   selected saved UCloud profile's server and project, plus its server-scoped
   credential. The model, language, GPU and transcription working folder do not
   select a backup destination. The separately entered parent is the destination.
   Several model-named profiles can represent the same server/project, while
   approvals and transfer journals still bind the exact provider ID. Simplify
   their labels and selection without silently changing those bindings. Handle
   incomplete, missing and deleted profiles and avoid implicit selection.
   **Implementation:** the chooser identifies the server and project, uses an explicit
   selection, and fences cloud actions to the reviewed settings revision and target. Synthetic
   tests cover stale tabs, changed identity and approvals, and frozen transfer retries. Real
   UCloud transfer and writable-folder acceptance remain open.
4. **Backup destination and verification.** The default local backup folder can
   be on the same disk as the library. The UI should make its destination and
   verification status clear, offer a suitable destination choice and distinguish
   a same-disk copy from an independent one. Saving a cloud target validates
   configuration, not whether its remote folder is writable. Real UCloud and
   clean-machine restore acceptance remain open.
   **Implementation:** Library shows the actual default backup parent and allows choosing
   another local folder before creation. It explains the same-device risk and can
   identify the same local Windows volume when verified; other cases remain unknown. It does
   not claim that a chosen path proves independent storage. The completion message
   distinguishes creation checks from a test restore. Physical independence and real
   backup/restore acceptance remain the user's responsibility.
5. **Library layout.** Make the current library, switching and hidden entries
   the primary flow. Consider one guided Backup and restore section, linked from
   Settings, with verification and recovery actions. Keep retention, transfer
   origin and legacy-inventory details available for advanced recovery; do not
   remove approvals, checks, evidence or explicit restore boundaries.
   **Implementation:** older database-only copies appear under Restore only when
   present, labeled as lacking recordings; new database-only creation is absent
   from the Library UI. Existing files and APIs remain available.
6. **Old libraries.** Reversible removal from the list is implemented. Permanent
   deletion remains unimplemented: ownership, external project paths, unsettled
   local/cloud work, staged recovery and platform-specific locking need proof.

## Reported backup failure

The reported “Failed to fetch” is a browser transport error from the local
backup request, not a UCloud error. The app server was found offline and the
library's SQLite quick check passed; the server was restarted. A read-only local
receipt query returned no recorded complete captures. An older partial package
was found, but its timestamp does not establish that it belongs to the reported
attempt. No traceback or Windows event established why the server stopped. Do
not assume the backup completed or delete the partial based on this observation.

Evidence: `static/library.js`, `static/portable.js`, `portable_routes.py`,
`portable_capture.py`, `portable_cloud.py`, `libraries.py` and `providers.py`.

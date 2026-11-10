# 0107 — Publish a marked restore destination atomically

Accepted 30 October 2026. Extends the complete-backup restore design without changing
its database schema, package format, source ownership or independently trusted receipt.

Creating the destination directory before writing its incomplete-restore marker leaves a
crash window: a marker write failure can expose an unmarked empty directory that a normal
library open treats as a fresh library. The staged backup and source data remain intact, but
the destination no longer communicates that restoration is incomplete.

Build the reservation in the exact operation-owned staging directory first. It contains only
the durable operation-specific marker; validate its bounded contents and sync it before using
an exclusive same-volume rename to publish it at the destination. Sync the source and parent
directories after publication. Never replace or adopt a racing destination. A partial marker
may be repaired only inside the same unpublished, quiescent operation-owned reservation.

When the target exists and an unpublished reservation still exists, refuse adoption even if
the target carries the same marker. This includes dangling reservation links and preserves
both objects for investigation. Once publication has consumed the reservation, existing
exact-marker and completed-target resume rules continue to handle interruption and a lost
registration reply. Never infer ownership from an unmarked empty folder.

Synthetic tests cover interrupted marker creation, rename failures, destination collisions,
links, marker identity and successful resume. Real filesystem crash durability and network
storage behavior remain separate acceptance work.

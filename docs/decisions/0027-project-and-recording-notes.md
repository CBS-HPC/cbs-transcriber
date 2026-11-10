# 0027 — Descriptive project and recording metadata

Accepted: 21 October 2026. The user requested Description / notes for research metadata.

Schema 5 adds plain-text notes and independent notes_revision columns to projects and recordings,
initially empty/zero. Preserve every prior column/value. Back up an existing schema-4 SQLite
library under its lease before migration. No recordings, results, jobs or credentials change.

Separate notes PATCH endpoints accept up to 20,000 Unicode characters and require the notes
revision. Stale writes fail with a conflict; transcript revisions remain independent. SQLite
is authoritative and project snapshots include notes. Render textarea values, never HTML.
Settings and notes have independent Save actions. Late saves cannot put one item's metadata
into another view or redirect its next save. Preserve conflict drafts and guard navigation.

Notes stay outside provider/enhancement input and ordinary transcript exports. They can contain
private metadata and belong in managed project storage, never Git. Current notes/revisions are
not a complete historical research ledger; that remains a separate roadmap item.

# 0060 — Collections inside projects (schema 11)

Accepted 24 October 2026. Implements docs/collections-proposal.md.

Researchers group recordings within a study: interview waves, sites, focus groups, pilots. They
want to do this without splitting the study into several projects, which would duplicate
classification, storage and provider approvals. The researcher asked for the proposal to go
ahead. Its recommended options were taken; any of them can be revisited.

## Decision

- **One collection per recording, one level.** A recording belongs to at most one collection,
  like a folder; recordings without one are "Unsorted". There are no sub-collections. Each
  collection has a name (unique within the project, case-insensitive) and plain notes.
- **Schema 11.**
  - A `collections` table (`id`, `project_id`, `name`, `notes`, `position`, `created`) and a
    nullable `recordings.collection_id` (a foreign key, `ON DELETE SET NULL`).
  - The migration backs up the schema-10 library first. It is additive and idempotent: only
    missing parts are created. Every earlier row is unchanged.
- **Metadata only.** Creating, renaming or deleting collections and moving recordings never move
  files on disk. Deleting a collection makes its recordings Unsorted and deletes nothing else.
  Moves are checked within one project: another project's recordings or collections are
  refused.
- **Project page.**
  - A chip row filters the cards: All, each collection, Unsorted, and ＋ New collection.
  - Selected recordings can be moved to a collection.
  - Uploads made while a collection is shown go into it.
  - While a collection is shown, the sidebar's Project Settings becomes Collection Settings
    (rename, notes, delete).
- **Exports follow the visible collection.**
  - Project ZIPs take an optional collection (or "unsorted") and, when the project has
    collections, put each recording under a folder named after its collection, or "Unsorted".
    Folder names are numbered and sanitised; the manifest names each entry's collection.
  - The research report receives the visible recordings as its explicit selection.
- **project.json.** The project snapshot lists the collections, and each recording carries its
  `collection_id`.

## Consequences

Classification, storage folder and provider approvals stay per project. Collection changes are
not transcript versions. They are not yet kept as an event history either: project.json holds
the current state only. A collection history can be added if researchers need to audit regroupings.

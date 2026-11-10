# Collections inside projects (proposal)

Status: proposal, 23 October 2026. Not implemented; needs the user's choices below.

## Need

A project can hold many recordings that belong to different parts of a study, such as
interview waves, sites, focus groups or pilot recordings. Researchers want to group them inside
the project without splitting the study into several projects. Classification, storage folder
and provider approvals would stay per project.

## Proposed design

- **Data.**
  - A new table `collections(id, project_id, name, position, notes, created)`.
  - Recordings get an optional `collection_id`. When a collection is deleted it becomes
    `NULL`, so the recording itself is never deleted.
  - This needs schema 11, with a schema-10 backup like earlier migrations.
- **Files.** Collections are metadata only. Recordings stay in the project's folder, so creating,
  renaming or moving between collections never moves audio on disk.
- **Project page.**
  - A chip row above the recordings: **All**, one chip per collection, **Unsorted** and
    **＋ New collection**. Choosing a chip filters the cards.
  - Selection and **Transcribe selected** work within the filter.
  - Each card gets **Move to collection…**, which also works as a bulk action for selected
    cards. Uploads made while a collection is shown go into that collection.
- **Exports and reports.** Export Settings and the Research report can be limited to one
  collection. A project ZIP keeps one folder per collection.
- **History.** Creating, renaming and deleting collections, and moving recordings between them,
  are recorded as project events, never as transcript versions.
- **Sidebar.** It still lists projects only. A project's collections appear on its page, which
  keeps the sidebar short.

## Choices for the user

1. May a recording belong to **one** collection (folders, recommended for simplicity) or to
   **several** (tags)?
2. Is one level enough, or are sub-collections needed?
3. Should collections carry their own notes, or a description only?
4. Should **Transcribe selected** / exports default to the visible collection (recommended)?

## Effort

About one to two days, including the schema migration with backup, the API, the UI, tests and
documentation.

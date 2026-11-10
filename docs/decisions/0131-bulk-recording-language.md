# 0131 — Atomic language changes for selected recordings

Bulk language selection freezes the selected recording IDs and their observed language
revisions. A project-scoped request changes all of those recordings in one SQLite writer
transaction, or changes none if any recording is missing, belongs to another project, or has
changed. The same operation can clear overrides to inherit the project language. Unchanged
values retain their revision; existing processing jobs keep their frozen inputs.

The project snapshot is refreshed after commit under the existing snapshot lock. Snapshot
failure is reported separately from the committed database change. Selection and navigation
are guarded while a request is pending; an uncertain response requires the user to reload
before retrying. The operation is bounded to 1,000 distinct recordings.

Select all acts on visible, filtered recordings and preserves other selections. Deselect all
clears the whole selection. No provider, recording media or remote operation is changed.

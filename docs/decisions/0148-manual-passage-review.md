# 0148 — Independent manual passage review progress

Accepted 9 November 2026 for MVP. Implemented with synthetic backend, migration, portable
restore and browser verification; real-user acceptance remains separate.

Users need Not checked, Checked and Needs review marks plus an overall checked count. These
are human workflow metadata, separate from model uncertainty, qualitative topic coding and
the immutable Working transcript. Marking never changes wording, timings, content versions,
recording revision or Working roles, and does not require a Working-choice prompt.

Schema19 adds passage_reviews keyed by result ID and passage ID, with canonical saved-passage
SHA-256, status, independent row revision and update time. Back up before migration under the
library lease. Existing results begin unmarked. Retain rows when a result is in Trash and
remove them only with its physical result deletion.

A matching saved Checked mark is Checked. If that passage changes, expose Needs review;
unrelated passages keep their marks. New result/passage IDs are Not checked. Exact Undo or
history restoration may match the old saved hash again. Explicit Needs review stays so until
the user changes it; keep Not checked rows with their revisions to avoid an ABA reset.

Read only the scoped active live saved result for MVP. A mark write checks result identity,
recording revision, exact observed passage hash and row revision in one writer transaction.
Return authoritative status/counts; stale writes fail without changing content or roles.
Unsaved wording cannot silently be marked Checked. Failed/late replies preserve current
drafts and view identity. Manual progress is available in full and MVP profiles.

Next to review belongs beside manual progress. It visits visible Not checked or Needs review
passages in display order, skips Checked and wraps, independently of model uncertainty and its
visibility switch. Loading/unavailable marks, unsupported historical views and all-checked
selections disable it. Navigation may seek to an existing passage start but never changes marks,
closes an editor or saves/discards a draft. Status/filter/view changes refresh eligibility.

Repeated marks may reuse a bounded memo of completed passage fingerprints (eight contexts,
20,000 passage entries). Rehash the actual current head and both mirrors before reuse; keep
live ownership, revision and review-row CAS checks fresh. Cache no wording, speaker names,
statuses or authority. Content changes require the full proof again. Include review metadata
in destructive-operation preview fingerprints so changed progress invalidates old consent.

Complete SQLite backups and portable restore carry these rows. Extend strict portable schema
admission and quarantined working-copy migration to schema19, preserve older catalogs and
keep restored remote owners inert. Review flags do not imply transcript/model quality or
institutional approval and are not added to ordinary transcript/NVivo text exports.

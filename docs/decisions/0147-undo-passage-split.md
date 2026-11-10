# 0147 — Undo a proven passage split

Accepted 9 November 2026. Implemented with synthetic history, Working and browser verification.

Reuse ADR 0135's bounded history proof and manual Working fence. Extend its eligibility
response with split first-half IDs without changing existing merge IDs. Offer Undo split on
the first half only when both current halves are exact, unique, adjacent historical outputs.

Validate the split version and immediate predecessor, payload hashes and result ownership.
Recompute the entire split from its recorded cursor/audio position. The split function creates
a random second ID: substitute the recorded ID only after proving the before/after shape and
uniqueness; all other dictionaries and speaker maps must match exactly.

Undo restores the exact original passage dictionary, including Unicode, originals, timing,
word maps and unknown fields. Preserve unrelated current passages and current speaker names.
Append passage_split_undo through the existing atomic edit/history/Working writer. Recheck
eligibility on Apply; changed halves, stale revisions and missing/corrupt proof refuse.

Do not consume proof permanently. A later explicit history restoration of the same exact
pair may become eligible again. History remains immutable; direct undo never restores a
whole transcript or guesses timing. Merge and split discovery share bounded scan budgets.

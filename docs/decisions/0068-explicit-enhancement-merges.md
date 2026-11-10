# 0068 — Explicit enhancement merges into a reviewed result

Accepted 27 October 2026 as a first T43 implementation stage. Existing results, selection,
Trash and historical restoration keep their meanings; no schema migration or automatic
promotion of a model result is introduced.

An enhancement freezes a source version, while its source result can receive later review
edits. The enhancement may renumber passages or split them, and its original_* fields refer
to a different restoration baseline. Copying whole passages or matching IDs alone is unsafe.

Offer a preview and explicit **Apply safe updates** from Versions → Results for a derived
enhancement of the currently reviewed source result. Establish correspondence from the exact
incoming creation version to its frozen base: equal passage count, order and literal text,
with unique bounded IDs. Reject changed or ambiguous creation segmentation. Match later source
and incoming edits using that established correspondence, never fuzzy wording or timestamps.

Merge timing as a coupled group and speaker assignments independently. The current result's
wording, original_* fields, edits, passage order and unknown metadata remain authoritative.
Changed/deleted/split/merged passages are retained and reported as conflicts or skipped. New
wording is not adopted in this stage. Validate every adopted word interval and character span;
partial maps remain partial. A researcher-named voice is never reassigned to a new machine
speaker cluster. Existing speaker names win; do not infer identities between clusters.

Preview reads one SQLite snapshot. A fingerprint binds the recording revision, base, incoming
creation, both heads and their exact hashes, the merge policy and proposed payload. Apply
recomputes the plan under BEGIN IMMEDIATE, rechecks live ownership and all identities, then
atomically updates the active projection/result and appends one result_merge version. Reject
stale previews and no-op applications. The client sends no merged passages.

Keep predecessor as the previous reviewed head and preserve source_version_id's existing
producer ancestry semantics. Add bounded, server-built merge context naming the base,
incoming head and mapping creation version/hashes. Research reports verify those references
inside their bounded snapshot and render aliases, without copying reasons, text or private
metadata. The incoming result and all earlier versions remain unchanged and recoverable.

This does not implement the broader working-transcript/alternative migration, automatic
machine merges, arbitrary model wording adoption or interactive conflict resolution. Those
remain separate T43 stages. Tests use temporary libraries and mocked/synthetic inputs only.

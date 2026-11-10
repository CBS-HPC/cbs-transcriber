# 0151 — Select recordings and formats for named export folders

Accepted 9 November 2026. Implemented with synthetic archive, Windows-path and browser verification.

Export Settings provides compact checkbox lists for project recordings and output formats,
with Select all/Deselect all. An empty list disables export and never falls back to all data.
The recording view remains scoped to its reviewed result; a project defaults to Working
versions and retains reviewed/all-result choices and the shown collection scope.

Use structured POST archive requests for explicit recording IDs and format lists so large
projects do not depend on long GET query strings. Keep existing single-format GET links.
Validate format, recording, project and collection ownership and bounds before exporting.
Freeze dialog scope/selection for a download and ignore replies from abandoned views.

Render every chosen result/format combination from one read-only SQLite snapshot. Exact
Working version validation stays unchanged. If one format needs missing timing, report that
skip while retaining other valid outputs. Keep machine-readable and readable export reports.
Private provider configuration never belongs in these archives. Originals remain excluded by
default; the explicit option below is separate from transcript format selection.

Use the researcher's recording name for each recording folder. Sanitize path separators,
unsafe/control characters, Windows reserved names and trailing dots/spaces; resolve duplicate
and case-insensitive names deterministically. Separate same-extension formats and multiple
results without overwriting files. Numeric-only recording folders are replaced; short suffixes
remain appropriate for collisions. Existing collection grouping is retained.

On 9 November, the user requested Include original recordings, unchecked by default. Include
each selected owned original once in its recording folder, preserving its safe filename and
extension. Allow an original-only ZIP with no transcript formats. Stream bounded chunks from
verified owned regular files; do not follow links or hold shared mutation locks through large
copies. Preserve source identity and report unavailable originals without changing the source.

The recording checklist keeps in-scope recordings without a transcript visible with No transcript
or No Working transcript. Disable and uncheck them for transcript-only export; including originals
enables them without selecting them automatically. Select all affects only eligible rows. Keep
legacy requests, frozen dialog scope, pending controls and library-instance fencing unchanged.

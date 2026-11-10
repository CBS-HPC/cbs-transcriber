# Clear finished presentation — 27 October 2026

The reported remaining cards were completed UCloud jobs with unverified cleanup. V105 kept
their actions accessible, but rendered every full-size card after Clear finished. The
presentation now folds older finished obligations into one closed, counted summary. Newer
updates and active jobs remain visible. Expansion survives polling; Clear and reload collapse
the group. Nothing changes in the API, jobs, result history or remote ownership journals.

The exact synthetic reproduction used three completed/SUCCESS jobs with pending cleanup and
one active job. Before the fix, four visible cards remained after Clear (one expected).
After the fix, only the active card and a compact three-job summary remain. Expanding exposes
all cleanup actions; invoking the mocked action leaves the original journal parameters intact.

Four focused Chromium cases passed in 40.49 seconds: the existing clear contract, the new
three-obligation reproduction, Runtime cleanup and native batch Activity. Evidence:
`test-results/activity-retained-green.xml` and `activity-cleared-retained.png` (ignored).
Scoped Ruff, Prettier 3.6.2, JavaScript syntax and whitespace checks pass. All libraries and
remote responses were synthetic; no real remote cleanup, allocation or transfer occurred.

The four explicit Runtime-discard dialog browser cases also passed on the combined working
tree (33.77s, `test-results/review-activity-discard-integration.xml`). The integrated UI retains
the discard action dispatcher required by the previously committed dialog. Repository-wide
Ruff lint and formatting passed after formatting the startup evidence's Python code block.

The separate four-file startup incident and stale-backend diagnosis are recorded in
[supervisor startup evidence](verification-2026-10-27-supervisor-startup.md). Restarting the
running backend and migrating its real library were not part of these browser checks.

# 0133 — Automatic and explicitly consented Working versions

## Decision

Supersede ADR 0093's prohibition on implicit assignment for new work. A first
result can become Working when no previous result or role event exists. New
processing requests freeze the exact Working role revision plus the selected source
version/hash; the resulting successor may move Working only if the current source
head is still live, matches that frozen source and the role has not changed. This includes
processing an alternative or a recording with no assigned Working version. A researcher
choosing another version while work runs always wins. Legacy unmarked jobs,
replays, result restoration and migration never create a designation.
Fresh standalone processing and owned follow-ups use an explicit frozen policy marker;
an old unmarked job cannot acquire promotion authority from a coincidental parameter.
Schema 18 also stores a separately authorized job receipt, bound to the recording, policy,
frozen role, exact source version/payload and complete source-snapshot hash. Accepted
admission receipts additionally bind the accepted-request hash. Only trusted enqueue and
follow-up transactions can create these receipts; a legacy job's arbitrary parameters alone
never grant authority. Explicit preparation successors inherit only their predecessor's exact
role and source, with the renewed accepted-request hash. Deleted jobs remove their receipts.

Same-result processing and consented manual edits use the same transactional
role fence. Explicit editing consent must bind the exact source head and role
revision and commit the edited successor and its designation together. Declining
consent does not move Working. Existing explicit preview/apply remains available
to choose an earlier immutable version.

Manual edits of the current Working head automatically advance its designation.
A contradictory explicit request to keep that same head is rejected before
the edit or any history write; it is never silently treated as consent to move.
Timing-only Apply has its own frozen authority and explicit-choice behavior.

For separately requested disjoint timing jobs, a later Apply may advance across
an intervening timing-only chain rather than leave valid later timing outside
Working. Verify at most 16 role events under a 16 MiB budget: each must be a
system timing successor whose predecessor, source, trusted job authority and
atomic local/cloud Apply receipt reconstruct exactly, with the current Working
version equal to the chain tip. Any explicit role choice, manual or unrelated
edit, unverified step or exhausted budget leaves Working unchanged and reports a
warning while preserving the applied timing result.

An explicit decision to keep the current Working version, or to reselect that
same version, records a `working_kept` local-user event and advances the role
revision even though the version reference does not change. This also applies
when the role is unassigned. The self-loop truthfully records the decision and
prevents an earlier job from overriding it; automatic timing chains cannot cross
such a user decision.

Before writing an edit or a role event, validate the two most recent role events
and their immediate predecessor linkage, bounded observation fields, action,
actor and referenced version hashes. This tail check does not validate older
history; full bounded history/report and portable checks remain separate.

## Persistence and provenance

Schema 18 migrates the existing role-event table transactionally without
rewriting events; a schema-17 library is backed up before migration. Automatic
first-result, processing and edit advances have distinct actions and a system
actor. Explicit user decisions remain local-user actions. Portable checking,
restore, reports and default exports must recognize the same event chain and
preserve exact immutable payload hashes. Trashed results do not count as absent.
Both role writes and result/version receipts roll back together.

Role history has bounded reporting limits. Frequent automatic edit advances can
reach those limits; large-history reporting needs separate work without silently
truncating or misrepresenting the event chain.

## Isolated integration verification, 3 November 2026

The Working implementation remains on `codex/working-undo-integration` and is
not active in the serving app. Synthetic checks covered 101 manual-edit and
passage-structure cases on the integrated branch. After rejecting contradictory
Working choices, nine focused edit/Undo cases and four explicit timing-choice
cases passed. Ten targeted browser cases passed across the combined run and a
separate retry; the initial retry candidate had failed before page load with a
Chromium resource error. A synthetic schema-17 migration preserved role history.

Contained schema-18 checks covered automatic role events, an isolated complete
backup/restore with archived jobs, and quarantine of restored Working job
authority. A separate backup receipt test mocks semantic inspection and proves
only schema-18 capture plumbing and transport integrity; it is not a substitute
for contained semantic verification. Canonical pinned source/catalog checks and
CRLF source-size boundary checks passed in-process.

These narrow synthetic checks do not complete corruption, resource-limit,
full-suite, producer-authority, report, real-library migration, real-provider,
or cross-platform acceptance. Independent review remains in progress. They do
not establish actual UCloud backup/restore or researcher acceptance.

After adding bounded role-tail checks and timestamp validation, 23 focused
corruption/role/export cases and the nine-case result-role suite passed. The
contained schema-18 role suite passed 12 cases, including malformed timestamps;
complete synthetic backup/restore and quarantined-authority restore each passed
again. These results do not establish integrity of role events older than the
checked tail or close the wider acceptance items above.

The smallest next integrated producer/role and concurrency regression selection
passed all 36 synthetic cases on the isolated branch. It exercises fresh and legacy routes, Working choice
fences, disjoint timing successors and corrupt recent history. These deterministic
cases do not replace true parallel stress, full-history corruption tests or real
provider/Windows/Mac acceptance. Run serially after checking system resources;
the portable-capture case starts a contained worker but no real cloud request:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$python = 'C:\Users\kgp.lib\OneDrive - CBS - Copenhagen Business School\Desktop\CBS Transcriber\.venv\Scripts\python.exe'
& $python -m pytest -q -x `
  tests/test_working_route_integration.py `
  tests/test_atomic_local_timing.py::test_disjoint_timing_jobs_follow_verified_automatic_successor_chain `
  tests/test_atomic_local_timing.py::test_disjoint_timing_never_overrides_an_intervening_explicit_choice `
  tests/test_atomic_local_timing.py::test_disjoint_timing_does_not_absorb_intervening_manual_edit `
  tests/test_atomic_local_timing.py::test_disjoint_timing_keeps_working_if_chain_cannot_be_proved `
  tests/test_working_manual_policy.py::test_stale_role_choice_rejects_entire_edit `
  tests/test_working_manual_policy.py::test_corrupt_recent_working_history_blocks_manual_write_atomically `
  tests/test_automatic_working.py::test_processing_does_not_override_an_explicit_newer_choice `
  tests/test_automatic_working.py::test_explicit_keep_fences_earlier_processing
```

The broader five-file Working/authority selection also passed all 150 synthetic
cases, serially, in 133.58 seconds on the isolated branch:

```powershell
& $python -m pytest -q tests/test_working_route_integration.py `
  tests/test_atomic_local_timing.py tests/test_automatic_working.py `
  tests/test_working_manual_policy.py tests/test_portable_working_authority.py
```

Neither selection establishes true parallel stress, integrity of older-than-tail
role history, real provider behavior or real-library migration acceptance.

During this run, a legacy follow-up test initially expected `queue.process` to
set a terminal state; the real worker loop owns that transition. Its fixture now
runs and drains that loop while keeping the completion and frozen-legacy
assertions. Another test incorrectly sent a schema-18 capture to the internal
schema-12-only package checker; it now uses Restore's bound transport capture,
contained semantic check and final recheck, retaining the authority-row and
`restore_ready=false` assertions. The selection then passed 36/36.

# Runtime preparation reader guard verification

23 November 2026, Windows 11. The local rc3 candidate includes the reviewed guard
in `6e2054b` (source checkpoint `4ce7c42`). A matching older supervisor may not
participate in the shared lock. Its UCloud job can resume from SUSPENDED, so the
host keeps treating it as a reader when a new request would perform preparation.
This applies to the exact existing server, project, storage and runtime scope.

- Five synthetic cases reproduced forbidden setup calls on earlier source. On the
  fixed source they refuse before setup for singleton and shared batches (with
  valid and absent stamps) and standalone preparation (with an absent stamp).
- The focused guard suite passed all 66 cases with unchanged product and test
  hashes. An earlier already-captured readiness inventory on `e895b7e` had 153
  passes and one Windows link-permission skip.
- The adjacent suite had 90 passes and one timeout before the cancellation test
  entered its worker assertion. The baseline passed that node. A candidate rerun
  with timing observation also passed the unchanged assertions, with the legacy
  reader check taking about 0.01 seconds and the worker close to the fixture's
  five-second wait. The final uninstrumented run on the committed candidate passed.
  All 91 adjacent cases have passing evidence across those runs.
- The test app's read-only Runtime check reported connected and unprepared.
  Actual preparation and the small-batch acceptance are separate approved steps.

The ordinary Runtime worker's remote state handling is a separate review scope.
Shared reader observations cover the selected Store. Other known libraries and
the actual lock still need verification before controlled preparation.

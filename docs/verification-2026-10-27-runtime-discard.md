# T85d — Retained Runtime file disposition

27 October 2026, `feature/runtime-batches-and-review-layout`, following core commit `0159052`.
The disposition stage is verified offline; the running backend/library remain unchanged.

## Scope and review

ADR 0066 / SPEC V118 add a distinct whole-batch preview and explicit confirmation. The service
verifies originals, frozen inputs, exact terminal allocation ownership and every prior attempt.
It archives bounded stage bytes in separate task folders; matching receipts are distinguished
from unverified artifacts. Complete unsaved results require recovery first. A SQLite CAS
records immutable consent and fences recovery/producers before owned-folder Trash.

An independent read-only review found two concrete issues in the initial implementation:
confirmed retries needed to re-inventory newly visible artifacts, and never-dispatched siblings
needed separate no-input validation. Both have regressions. Lost Trash replies use strict
parent absence; drift after consent retains the run for explicit review. Post-consent refusals
store curated diagnostics without manufacturing pre-consent obligations. The final review is GO
for these seams, subject to the verification below.

## Evidence

- Focused new stage: 58 unit/API cases passed together (37 disposition, 17 API and 4 Activity).
  Service/persistence also passed its earlier 74-case group (36 disposition + 38 persistence).
- Interface: 17 API and 4 Chromium cases passed. The final affected browser rerun also passed;
  desktop and 390px screenshots were inspected. Preview cancellation, exact confirmation,
  failed requests, stale previews, literal names, unverified labels and pending cleanup are covered.
- Affected regression: 117 passed, 2 failed. Both existing coordinator cases passed an exact
  two-case rerun. Bounded stress reproduced initial milestone timeouts. Captured frames showed
  a 10ms fixture heartbeat flooding real Windows ancestry checks and disk writes; a controlled
  six-case comparison passed with the production 15s cadence and unchanged milestone timeouts.
  The corrected whole coordinator file passes all 23 cases, including strengthened sibling
  completion checks and the existing virtual-clock long-transfer renewal case. No production
  timing or coordinator behavior was changed to repair this fixture. The earlier final-state
  failure did not recur in these corrected checks. Across the affected broad/focused runs all
  119 distinct affected cases pass; this is not a new complete project-suite run.
- Raw evidence is in ignored `test-results/runtime-discard-final.*`,
  `runtime-discard-interface-final.*`, `runtime-discard-screenshots.*`,
  `runtime-discard-regression.*`, `runtime-retry-*.xml/.txt`, and
  `runtime-coordinator-production-cadence.*`. Fixtures contain synthetic data only.
- Repository-wide Ruff lint and stage-file formatting, Prettier 3.6.2, JavaScript syntax and
  Git whitespace pass. A later repository-wide format check also included an unrelated
  in-progress supervisor document with an unformatted code block; it is outside this stage.

Do not add overlapping run counts. All inference/transfer/control behavior used temporary
libraries and mocked UCloud; no private recordings or transcript bodies were inspected, no
real allocation, transfer, Trash, weight download or paid API call occurred. No real migration,
backend restart, merge or push was performed. Real UCloud and cross-platform acceptance remain
separate. This focused review does not replace the final project audit after implementation.

Changed remote evidence after consent is preserved where verifiable and blocks the recorded
decision pending explicit recovery review. Missing/corrupt prepared inputs also refuse
disposition even when an original still exists. These conservative refusals are intentional;
the UI does not amend or withdraw a recorded decision implicitly.

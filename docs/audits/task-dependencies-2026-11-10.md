# Task overlap and dependencies — 10 November 2026

## Checked scenario

The user imported twelve recordings, then started native UCloud transcription while browser
media preparation was running. The inspected serving build is **MVP rc4 02193a2**, immutable
package c897efa. The relevant processing modules match the integration source. No real job
was started, stopped, retried or modified by this audit.

A read-only local API checkpoint found twelve active transcription recordings. Each had a
completed media job and completed original-inspection job. Transcription was still running
on UCloud. This is a healthy checkpoint, not proof that the whole remote batch has finished.

The overlap is intentional:

- `media.py:63` reads an original and writes separate browser audio/video proxies.
- `input_admission.py:869` waits for the selected originals' inspection outcomes; playback
  readiness is a separate prerequisite for playback, not original-backed transcription.
- `native_batch.py:1136` uploads the managed original and verifies its source identity before
  and after transfer. It does not upload an in-progress browser proxy.
- `jobs.py:1320` and `native_batch.py:1046` explicitly allow original-backed transcription
  while playback is preparing or has failed. Legacy jobs retain their existing readiness gate.
- `jobs.py:952` directs cancellation to the worker owning that job. Media cancellation does
  not cancel a native batch; native Stop remains allocation-wide.

The independent source review found no concrete conflicting-file-write defect in these paths.
Existing tests cover API transcription before playback, the native cohort barrier, one native
owner and independent cancellation. Native fixtures normally finish admission first; a composed
same-recording native/playback scenario is an additional useful regression.

An exploratory twelve-file mocked workflow progressed through inspection, one native owner
and uploads while playback was held, but a fixed test deadline did not reliably complete the
protocol on this shared machine. The exploratory test was removed; it is not a passed regression
or a product deadlock reproduction. A deterministic composed test remains part of the audit.

## Remaining focused audit

The follow-up found a concrete monitoring dependency: the log reader repeatedly charged its
full batch guard against the three-second I/O deadline. With twelve members each guard opens
49 SQLite connections; correct instant mocked responses still timed out entirely on local
checks. This suppresses both progress observations and early retrieval. The tested correction
separates guard time from I/O while retaining every check; see
[ADR 0160](../decisions/0160-native-log-io-budget.md). The active rc4 server remains unchanged;
this source fix does not establish a successful real early import.

| Combination | What to establish |
| --- | --- |
| Media + inspection + native transfers | Aggregate CPU/disk/memory and editor latency; per-lane limits are not a shared resource budget. |
| Delete with playback and inspection readers active | Both readers drain before files disappear; surviving cohort members are admitted once. |
| Storage move immediately after Stop | A cancelled state does not bypass physical worker drain. |
| Background export while native results arrive | Frozen selections/versions remain exact while results and playback finish; no lock cycle. |
| Shutdown/reopen during these overlaps | Worker drain, durable remote ownership and explicit recovery preserve the same allocation. |

This computer currently uses six media, four inspection, two admission and six API slots on
eight logical CPUs. Native transfers have a separate four-operation limit. FFmpeg has no
explicit per-process thread budget. These can compete for resources even though they write
different outputs; higher independent limits must not be interpreted as spare CPU capacity.

Continue measurements under the existing
[inspection/media performance plan](../work-plans/original-inspection-performance.md) and
[local-concurrency evaluation](../evaluations/local-concurrency-2026-11-05.md). Use tiny synthetic
files and mocked providers for correctness; representative real-media throughput is separate
acceptance. No scheduling policy or concurrency default changed in this audit.

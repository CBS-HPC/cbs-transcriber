# 0106 — Drain HTTP mutations before local Delete staging

Accepted 30 October 2026 during the integrated workflow audit. Extends ADR0102 and the
existing complete-backup mutation gate; no database schema change.

A reproducible interleaving lets a classification PATCH pass the outer recovery check,
wait for Delete's queue lock, then write after failed staging leaves `recovery_pending`.
Checking a cached flag only before entering the endpoint does not fence admitted requests.

Use the existing central HTTP mutation gate to reserve an exclusive local staging interval.
The deleting request promotes its own mutation slot before taking queue/snapshot locks,
drains previously admitted mutators and excludes new admission through the transaction and
rollback outcome. A second promotion refuses immediately rather than deadlocking while it
holds another mutation slot. Complete-backup capture and staging remain mutually exclusive.

Publish the recovery outcome before releasing exclusivity. Every mutation that waited at
the gate checks current recovery state after waking, including requests that passed an
earlier middleware check. The explicit local-recovery Retry remains usable. HTTP cancellation
must release its slot/reservation without leaving a stuck gate.

Blocking admission waits use a dedicated limiter of one thread. Sharing FastAPI's default
worker limiter lets later waiting requests consume every token while an older admitted
request still needs a worker to finish; Delete or capture then waits forever for that older
request. An independent two-token red/green reproduction establishes this cycle and the
separate-limiter remedy. Keep endpoint capacity unchanged and retain shielded acquisition
and the matching finalizer.

Release staging exclusivity before recursive committed-file cleanup: ordinary unrelated work
and Stop controls must remain responsive during potentially slow removal. Existing queue
drain/ownership checks still protect workers and exact target jobs; HTTP admission is not
a substitute for those checks or a claim that every internal database operation uses this gate.

Verification must reproduce the pre-admitted writer, competing deletion, gate/capture ordering,
recovery after a wait and responsive Stop/read-only access. No real library deletion is part
of this change's verification.

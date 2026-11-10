# 0065 — Durable shared CBS Runtime allocations

Accepted 27 October 2026 for implementation. Amends ADR 0046, 0049, 0053 and 0063.
The detailed failure analysis remains in `docs/runtime-batches-design.md`.

New API-only CBS Runtime jobs share an allocation when their research project, frozen provider
identity/settings, operation, concrete processing options, Terminal application, runtime/model
revisions, worker/protocol hashes and compute match exactly. Different source files and frozen
transcript versions are member identities. Native Transcriber batches and old API/SSH singleton
snapshots retain their existing paths. Only new jobs carrying a frozen `ucloud_api.batch_plan`
use this protocol; no old job is silently converted.

Schema 12 adds independent allocation, member and attempt journals. Migration takes the library
lease before backup or writes and preserves schema-11 data. Enqueue, follow-up insertion and
membership attachment use the existing SQLite transaction. Result/version/job/member/output
receipts commit together, including idempotent recovery of a producer result in Trash. Valid
completed output may commit after Stop; reviewed source text is never replaced.

The batch owns submission intent, remote ID, capacity, readiness, admission/seal, Stop and
cleanup. Each file retains its own source, stages and result. One coordinator publishes a
bounded revisioned membership ledger and immutable per-attempt input descriptors. Hash-checked
private inputs are published before their ready descriptor; remote acceptance precedes
inference. An accepted task without a terminal receipt is not automatically replayed. Only an
explicit retry of a known failed/interrupted attempt may use a new task ID while admission is
still open; sealed or ended allocations require a new producing job for new inference.

A parent native-batch Stop may cancel a runtime follow-up allocation only when all its members
belong to that parent's follow-ups. Check containment and close admission atomically; a late
foreign member prevents the cascade or receives another owner. Mixed shared enhancements keep
running with an explicit notice. Stopping their Runtime Activity card remains allocation-wide.

Late attachment and sealing use the same transaction/CAS boundary. A one-minute idle grace
allows more compatible files; admission ends two minutes before the frozen wall deadline.
Heartbeat every fifteen seconds, with a five-minute owner-absence drain, bounds unattended idle
allocations without treating app shutdown as user Stop. The remote worker executes one GPU
pipeline at a time per MIG. Independent allocations provide concurrency; retaining several
resident GPU models or parallel stages requires measured memory evidence and is not assumed.

Processing settings expose a local concurrency limit of 1–4 allocations, initially 2. Durable
reservations count uncertain submissions and legacy API singleton allocations too. Lowering
the limit does not terminate existing owners or prevent collection/recovery. Heavy local
preparation/transfers are bounded independently. No external queue service is introduced.

Before concurrent preparation, recheck the runtime stamp under its preparation lock. A persistent
`.in-use` lock protects ready readers; destructive preparation requires an exclusive lock and
ready supervisors hold a shared lock before doctor/inference. Never replace the lock inode.
Read-only runtime mounts open the existing lock read-only. Known nonterminal/uncertain legacy
readers, whose uploaded scripts lack this protocol, prevent destructive preparation of that
runtime. Pinned-model prefetch remains serialized and never rebuilds a ready environment.

Approval governs private transfers, not recovery. Recheck current provider identity and Protected
eligibility before each upload; retain owned status, Stop, collection and verified local
finalization after revocation. No credentials enter scripts, worker arguments or remote files.
Cleanup moves only the batch-owned folder to UCloud Trash after every required local raw/result
receipt is durable and the allocation is terminal. Never delete shared runtime/model folders.

Activity uses one canonical batch card with expandable per-file outcomes and allocation-wide
actions. Owner journals remain visible beyond recent-job limits and recording removal. Library
switch, storage move and deletion check owner obligations directly, not only active member jobs.
Automated verification uses temporary libraries, fake clocks/barriers and mocked UCloud; real
migration, allocation, performance and cleanup acceptance remain separate operations.

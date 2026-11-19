# Strict comparison with UCloud's normalised job specification

19 November 2026. Design note for one of the ADR 0176 "Before enablement" gaps. Nothing here
is implemented, and none of it starts preparation, allocates compute, uploads or contacts UCloud.
All three Stage 2 switches stay `False`.

## What happens now

`runtime_preparation_observation.state(client, remote_id, specification)` is the single
exact-ID observation. Legacy preparation polling and the disabled Stage 2
`verify_and_promote` both use it. It accepts a `/api/jobs/retrieve` reply when
`actual.get(key) != specification.get(key)` is false for `name`, `application`, `product`,
`parameters` and `resources`. That check is too loose and too strict at the same time:

1. **Types are not compared.** Python `==` treats `False == 0` and `1 == 1.0` as equal, so a
   reply with `"readOnly": 0` passes. This is the same defect class as B453.
2. **Some fields are never compared.** `timeAllocation`, `sshEnabled`, `replicas` and every
   other key are ignored, so a job with SSH enabled or a 48-hour allocation passes.
3. **Absent equals absent.** `.get()` returns `None` on both sides, so a reply missing
   `resources` matches a recorded specification that is also missing it.
4. **Unknown normalisation blocks every promotion.** If UCloud echoes the specification with
   added defaults (extra per-resource keys, a resolved application version, parameter
   metadata), the equality fails and a genuinely successful generation can never be promoted.
   This fails safe, but it would make enablement useless.

The legacy name-based `reconcile()` compares the whole specification with `!=`. Generation
owners never reconcile, so that call is out of scope here.

## Proposed rule

- Two versioned, pure functions: `expected_v1(posted)` turns the exact journaled POST body
  into the reply shape UCloud is shown to return, and `observed_v1(reply)` projects the
  reply's `specification`. They match only if their canonical JSON bytes are equal
  (`runtime_store.canonical`, the same rule as `runtime_preparation._same`). The journal keeps
  the exact POSTed bytes; a normalised form is computed and never stored over it.
- Bound fields must be present with exact JSON types:
  - `name` (exact string);
  - `application` `{name, version}`;
  - `product` `{id, category, provider}`;
  - `parameters`, which is exactly one file parameter. Its name comes from the owner's
    frozen record (the separate "frozen script parameter" gap), never the current constant;
  - `resources`, the ordered `[{type: "file", path, readOnly: bool}]` list;
  - `timeAllocation` `{hours: int, minutes: 0, seconds: 0}`;
  - `sshEnabled: false`;
  - `replicas: 1` if UCloud echoes it.
- Server-added keys go on a closed allowlist per object level, each with an exact type and
  shape, taken only from recorded evidence. Anything else refuses; unknown keys are never
  ignored. A missing bound key refuses.
- New generation owners freeze the projection version at submission, so a later version
  never re-interprets an accepted allocation. Absent policy keeps today's comparison for
  legacy owners, byte for byte, until they are separately migrated.
- A mismatch raises `observation.Refused`. It never adopts, retries, reconciles or promotes,
  and the recorded allocation stays visible for Stop and cleanup. The existing 16 KiB
  specification bound and a nesting-depth bound apply before comparison.
- Placement: `specification_matches(expected, actual, *, policy)` in
  `runtime_preparation_observation.py`. `state()` calls it only when it receives a policy,
  and generation owners pass their frozen policy.
- Synthetic tests come from scrubbed copies of the recorded replies. They cover re-typed
  values (`False`/`0`, `1`/`1.0`), missing and extra keys, reordered resources, a wrong
  `readOnly`, SSH enabled, a longer allocation, a wrong parameter name and a foreign mount.
  They also check that differences the evidence shows to be legitimate are accepted.

## Real UCloud evidence still needed

This needs a separately authorized live acceptance, because it means a paid allocation and
project folder creation. Collect it from one minimal Terminal batch job built by the
generation path itself: MIG product, two writable file mounts, the batch-script file
parameter, one hour and SSH off. A trivial `exit 0` script is enough for the shape, but the
product shape is product-specific, so the MIG product must be used. Before anything is
committed as a fixture, scrub project IDs, user names, drive IDs and paths.

1. The exact `POST /api/jobs` body we sent and the raw create reply, including the id's JSON
   type.
2. Raw `GET /api/jobs/retrieve?id=…&includeUpdates=false&includeApplication=true&includeParameters=true`
   replies for the same job while IN_QUEUE, RUNNING and SUCCESS, plus one job ending in FAILURE
   or EXPIRED and one CANCELLED after Stop. For every reply, record the full `specification`:
   - which keys are present, and their JSON types;
   - the order of `resources`, and whether `readOnly` is echoed (including `false`);
   - path normalisation (leading or trailing `/`, drive-ID versus path form);
   - whether `application.version` is echoed or resolved;
   - extra `product` keys;
   - whether `timeAllocation`, `sshEnabled`, `replicas`, `allowDuplicateJob`, `openedFile` and
     `restartOnExit` are present, and in which form;
   - whether the echoed specification changes between states.
3. Whether `includeParameters=false` drops `parameters`, which would confirm that the call
   must keep `true`.
4. The retrieve reply's `id` compared with the create reply's id (same string?), and an
   exact echo of the ASCII job name.

Already covered by other gaps or not needed for enabling generations: the create-folder
reply shapes, the frozen Terminal script-parameter record (the projection consumes it), and
the `browse_jobs` item shape, which only legacy reconciliation uses.

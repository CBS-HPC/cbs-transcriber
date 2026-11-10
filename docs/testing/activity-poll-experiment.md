# Bounded synthetic Activity poll experiment

This offline harness measures the real in-process `GET /api/jobs` route, including middleware,
SQLite reads, Activity expansion/projection and JSON serialization. It does not replace a
browser, remote/provider or live-library acceptance test. Run it after the integration suites
finish; competing suites make latency/memory comparisons misleading.

Use the existing environment and this checkout's source. No dependency installation is needed:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python.exe scripts/benchmark_activity_poll.py --groups 200 --members 10 --samples 5
```

The script accepts only numeric fixture sizes. It creates a new temporary library, supplies a
credential-refusing store and disables workers. Tripwires refuse TCP `connect`/`connect_ex`,
DNS through `getaddrinfo`, datagram `sendto` and subprocess creation; they are not an OS sandbox
or a complete network-isolation policy.
The trusted standard-library socket-pair constructor remains available for Windows asyncio's
internal self-pipe; only its own calling thread may make that constructor's loopback connection.
OS classification comes from `sys.platform`, also supplied to imported `platform.system()`
callers so Windows imports cannot fall back to spawning `cmd /c ver`.
It starts no HTTP server, registers no library, opens no existing library, reads no model cache
or private recordings and makes no inference, download or cloud request. An inherited
`CBS_TRANSCRIBER_MODELS` is refused before application imports can read it. The temporary
library is removed on exit; the JSON report is printed to stdout. Python's `-O`/`-OO` modes
are refused so correctness assertions cannot be silently disabled.
Before opening the library, the harness verifies that the imported app/projection modules
come from this checkout. A missing/wrong `PYTHONPATH` cannot silently measure an installed
checkout while reporting the script checkout's source hashes.

## Fixture and correctness oracle

The default fixture has 200 ordinary upload groups with 10 unique recording/job pairs each.
All jobs are completed media jobs with short synthetic metadata, inserted directly into the
temporary database. No import/admission/media processing or producer evidence is claimed.
Creation times interleave groups so the 200 most recent jobs contain one member of every
group; the route must expand the other 1,800 members. A separate older job in another project
uses the same upload marker as the first group and must remain excluded.

Each poll is checked against a separately constructed exact ID/project/recording/group oracle,
without calling the Activity helper to calculate expectations. Duplicate, missing or foreign
rows fail; membership must be complete, states/progress must match and private parameter/chunk
canaries must be absent. An SQL-dump fingerprint of all user tables must remain unchanged
across the polls. This fingerprint is a same-process fixture check, not a portable row codec,
backup, filesystem immutability guarantee or historical authenticity proof.

Bounds are 1–200 groups, 2–50 members each, more than 200 total jobs and 1–9 timed samples
(at most 10,000 main jobs plus the foreign control). These bounds limit fixture size; they
are not a hard OS/filesystem deadline. The official smoke uses only 210 main jobs.

## Measurements and interpretation

One explicit warmup is discarded. Timed samples report request/response latency and separate
client JSON-decode time, response byte count and returned-row count. Oracle validation,
fixture creation and cold imports/startup are outside latency measurements. Report the actual
sample list and median; there is no machine-dependent speed threshold in the test suite.

A separate poll counts every SQLite statement observed through the connection factory,
including connection PRAGMAs; only leading verbs are retained. Observed leading DML/DDL verbs
fail the check; a `WITH` prefix is not itself proof of a read-only statement. The independent
SQL-dump comparison checks unchanged logical contents, not absence of no-op writes or all
filesystem activity. Another separate poll records peak Python allocations with `tracemalloc`,
including the in-process client, JSON decode and correctness oracle; it is not isolated endpoint memory,
process RSS or SQLite/native memory. Instrumentation overhead is excluded from timed samples.
An existing `tracemalloc` observer is refused rather than reset. Relevant source-file hashes,
Python/OS and the fixture dimensions accompany each report.

Only compare identical fixture/source/environment dimensions. A 2,000-job pass establishes
that bounded ordinary-upload case, not every Activity path or an upper production limit.
Native/shared Runtime owners, retained cleanup, admission/inspection and restored-history
projection, concurrent mutation, real HTTP, browser DOM rendering and polling fairness under
load need separate scoped evidence. No model/GPU throughput or platform acceptance follows.
No result is a claim that the entire application audit is complete.

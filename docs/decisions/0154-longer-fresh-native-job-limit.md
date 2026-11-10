# 0154 — A longer limit for fresh native Transcriber jobs

Accepted 9 November 2026. The user identified the four-hour default as a risk for large or
overnight batches and requested a longer default. Target 24 hours for fresh native connections.
Implemented with 64 setup/specification/preservation regressions and a focused UI probe;
no live allocation is extended. Provider acceptance remains separate.

Keep NativeSetup's legacy omitted-hours interpretation at four hours. Changing that shared
parser would silently alter older queued jobs and retries. Instead, fresh native setup/profile
creation explicitly supplies 24 only when hours was omitted. The new/unset UI also supplies 24.
Explicit saved values, existing profile updates and parked CBS Runtime settings stay unchanged.
The existing validation range is one to 168 hours; the job builder receives the frozen value.

Do not introduce a startup migration that repeatedly changes an intentional four-hour choice.
The user's requested update of their existing native defaults is a separate explicit settings
operation, preserving other durations and every frozen job. The running allocation keeps its
submitted limit. Provider acceptance and workload duration remain separate from mocked requests;
this is a maximum wall-time request, not automatic batch sizing or allocation splitting.

# 0108 — Bound and cancel FFmpeg discovery and version probes

Accepted 30 October 2026. Extends original-input admission and managed media cancellation;
no queue schema, frozen legacy request or prepared receipt format changes.

FFmpeg discovery and version calls used unbounded subprocesses before the managed decoder
started. A stuck probe could keep a worker and shutdown join alive despite cancellation.
Synthetic reproductions covered browser media preparation, original inspection and the
source/version observation path.

Use a shared bounded, cancellable probe for automatic executable checks and version output.
Bound the startup deadline and output, kill and wait for the owned direct child on cancellation,
failure or timeout, and drain nonblocking pipes for a bounded time. Missing or failing automatic
candidates may continue through the existing bundled, environment and system selection order;
timeouts, output overflow and cancellation do not silently choose a different executable.
Explicit overrides retain their established selection behavior. This is not a process-tree
sandbox for arbitrary user-supplied wrappers.

For original-v1 preparation select one concrete executable for decoding, version and hash
observations; validate its identity before receipt publication. Keep cache results tied to the
selected executable, including concurrent observations. Already-completed receipt reuse does
not launch a probe. Preserve queue drain and serving-lease ownership until workers stop.

Concurrent cold observations of the same executable signature share one hash and version
probe. Each waiting inspection checks its own cancellation and durable guard; an owner's failure
or cancellation releases waiters to retry independently. Different signatures do not wait for
one another, and each waiter rechecks the current executable signature before reusing evidence.
This optimization changes neither inspection receipts nor the number of inspection workers.

Tests exercise real child cancellation, timeout, output bounds, inherited pipes, overrides,
fallbacks, concurrent cache observations and tiny synthetic media. Windows results do not
establish clean-machine macOS qualification.

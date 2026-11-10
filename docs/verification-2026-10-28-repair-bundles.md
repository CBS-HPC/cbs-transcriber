# T90b — Bounded alignment bundles

28 October 2026. ADR 0078 / V133. The delivered primitive is internal and inert: it builds,
validates and maps local fixed-language alignment clip bundles. It does not expose an API,
route repair work to UCloud, upload recordings, start jobs or change the running app.

Inputs must prove the exact historical source/projection through `repair_sources`, carry an
original-v1 source and a frozen runtime declaration, and use extraction receipts already
recorded on that job. At most 200 units cover the existing selection bound. Only actual
selected PCM and literal alignment text enter ordinal bundle files. Canonical WAV rewrapping
removes ancillary metadata and gets its own physical hash while retaining the source PCM hash.

The exact inventory and marker commit are immutable. Incomplete generations remain for
explicit disposition; the code never deletes an old generation to retry. Managed paths reject
symbolic and hard links. Marker publication uses atomic create-if-absent hard linking, so
the managed volume must support hard links (normally NTFS/APFS). Other volumes receive a
curated refusal with incomplete artifacts preserved. Windows extended paths support long
job-owned artifact paths without changing filesystem settings.

The mapper accepts only an unmodified in-process build/validate return. It does not accept
an external self-declared manifest as upload or execution authority. Every word range must
belong to exactly one selected passage. Nullable timing endpoints remain nullable, actual
clip coordinates are offset once, and search windows never become invented passage timings.

**125 combined source/bundle tests passed** (69 source cases and 56 bundle cases). Independent
review approved the final primitive after red/green probes closed cross-passage word spans,
oversized physical input, hard-link ownership and marker-overwrite defects. Size validation
happens before audio reads; same-descriptor hashing/PCM checks and a bounded growth-read test
protect the subsequent copy. Ruff and formatting passed.

```text
uv run --no-sync pytest tests/test_repair_sources.py tests/test_repair_bundles.py -q -o addopts='' --tb=short
```

No original whole-recording decode, private input, provider call, model download or external
execution was used. Future T90 slices still need fresh queue admission, exact upload scope,
remote per-unit acceptance/recovery/Stop/cleanup and revision-checked application. Automatic
routing, clip-language integration and passage re-transcription remain pending. Any future
threshold must measure selected audio windows, retaining overlap/transfer work separately;
whole-recording length is not the selected repair workload.

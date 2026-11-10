# Native Transcriber MIG observation — 2 November 2026

A single UCloud Transcriber Batch 1.12 allocation was requested on the funded
`gpu-nvidia-b200-1-mig.1g` product with `whisper/large-v3-turbo`, Danish and JSON.
The inputs were two public FLEURS clips, 5.10 and 18.24 seconds long. The allocation
reported SUCCESS after approximately 41 seconds of running time. No full-GPU fallback
or second allocation was used.

The normal native importer saved two results, their creation versions and raw output
receipts in a disposable local library. A subsequent read-only check matched their
request, result, creation and recording projection hashes, re-parsed the raw output,
and verified 12 and 33 finite, ordered word intervals inside the respective clips.
Each result contained a speaker-labelled passage. This is a functional observation,
not a measurement of transcription or diarization accuracy.

The retained UCloud logs reported `device: cuda` for both files. The available job
metrics did not report GPU utilization or VRAM, so this observation does not measure
either. The provider omitted the SSH-enabled field from its returned metadata; it is
recorded as not returned, not inferred from the original request.

The result supports an explicit user choice for this tested model and machine. It does
not establish that other models, longer recordings, larger batches, simultaneous jobs,
or automatic resource selection are suitable for this MIG size. Local public-only raw
output and log evidence were retained for the qualification run. After validation, the
test's own UCloud working folder was moved to Trash and its absence from the original
parent was confirmed. The platform job directory was not moved and Trash was not purged.

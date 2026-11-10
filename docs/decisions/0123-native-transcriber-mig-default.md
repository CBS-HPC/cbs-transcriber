# 0123 — Native Transcriber MIG default

Supersedes the fixed-native-compute portion of ADR 0039. The user requested that
new UCloud Transcriber runs default to a 1g B200 MIG, with an explicit full B200
choice. One public two-file Danish/JSON Transcriber Batch 1.12 `large-v3-turbo`
run completed on that MIG; logs reported CUDA, and both results and their timing
were validated. This is not evidence that other models, larger recordings or
batches will fit, or a measurement of GPU utilization and VRAM.

Keep an explicit allocation preference on the provider profile, separate from
the normalized connection settings that identify and authorize frozen work.
Existing full-B200 direct profiles predate any allocation choice and transition
once to the new MIG default; later explicit choices are retained. Templates
remain bound to their explicit captured machines rather than silently retargeted.
An admitted native request freezes its allocation and verifies exact supported,
funded product metadata before submission, without fallback. Its output and
provenance must identify the machine actually requested. Group only compatible
jobs. Old jobs and intents without the new policy retain their prior behavior,
and protected approval and existing Runtime identity checks remain in force.

CBS Runtime remains a separate execution path with its own compute policy.
Automatic allocation selection by file or batch size, model-by-model MIG
qualification and quantitative resource measurements remain future work.

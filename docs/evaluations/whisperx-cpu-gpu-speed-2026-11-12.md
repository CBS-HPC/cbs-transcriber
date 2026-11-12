# CPU versus GPU evidence for WhisperX scheduling

Reviewed 12 November 2026. Public primary sources only; no models, recordings or provider
jobs were downloaded/run. These measurements are not acceptance of CBS Runtime, UCloud
Transcriber, a B200 1g MIG or a particular UCloud CPU product.

## Published measurements

The most directly relevant source found later in the search is
[Aarhus University's UCloud Transcriber guide](https://interactivehpc.au.dk/en/about-interactive-hpc/applications/translate-to-english-transcriber),
revised 28 September 2026. It reports the same 57m48s interview taking **7m20s on
gpu-nvidia-b200-1-mig.1g**, versus about **1h40m on an eight-core CPU job** (the named CPU
product is cpu-amd-zen5-8-vcpu). The calculated time ratio is about 13.6. It does not identify
the exact model, precision, batch or full alignment/diarization scope, so this is strong local
native-Transcriber planning evidence rather than a qualified CBS Runtime/WhisperX ratio.

| Model / workload | Hardware and configuration | Reported time | Scope |
| --- | --- | --- | --- |
| faster-whisper large-v2, 13-minute audio | RTX 3070 Ti 8 GB, CUDA 12.4, FP16, beam 5 | 63 seconds; 17 seconds at batch 8 | ASR benchmark. |
| faster-whisper small, same 13-minute audio | i7-12700K, 8 CPU threads, INT8, beam 5 | 102 seconds; 51 seconds at batch 8 | Different model; cannot form a CPU/GPU ratio with the preceding row. |
| Independent WhisperX large-v3, 36-second clip | AWS g5.2xlarge, CPU INT8 versus A10G FP16/batch 32 | CPU about 44 seconds; GPU 2.63 seconds | Small implementer's experiment; GPU averages three warm transcription/alignment runs, excluding loading. CPU threads/batch/stage coverage incompletely specified; no complete diarization comparison. |

The first two rows come from the [canonical faster-whisper benchmark](https://github.com/SYSTRAN/faster-whisper#benchmark),
which names faster-whisper 1.1.0. The third is an
[independent implementer's primary experiment](https://github.com/Ziyang-Liao/WhisperX#gpu-benchmark-g4dn-vs-g5-vs-g6),
not an upstream or UCloud qualification. Its approximate 44/2.63 = 16.7 speed ratio is
directional evidence only; quantization/batching differ and the clip is short.

The [WhisperX paper](https://arxiv.org/pdf/2303.00747) compares inference approaches on an
NVIDIA A40; its reported acceleration relative to original Whisper is not a CPU/GPU ratio
and does not establish full speaker-label pipeline throughput. Do not turn upstream real-time
speed claims into a promise for this app's pinned model, Danish audio or MIG slice.

## Scheduling implication

Compare total completion time: queue wait, startup/model load, ASR, alignment, speakers and
transfer/import. CPU fallback is useful only when that total is lower than waiting for a
MIG and processing there, subject to model compatibility, memory, quality and approved cost.
More CPU cores do not imply proportional single-file speedup; the
[CTranslate2 performance guidance](https://opennmt.net/CTranslate2/performance.html) recommends
INT8 and coordinating worker/thread counts with physical cores for throughput.

Before enabling fallback, benchmark the actual pinned same model and output stages on one
MIG and selected CPU configurations. Separate cold/warm startup, each processing stage,
transfer/import, memory and total time; include short and long Danish/English workloads.
Hold decoding/batching/language/quality settings explicit, record differences and do not
silently substitute a smaller model. No numerical fallback multiplier is accepted yet.

Parallel MIG requests, CPU fallback and machine availability belong in one policy. The
currently checked API exposes per-product queue status, not exact free-instance counts.
UCloud's 2/3/4-mig.1g products count identical 1g devices, not larger NVIDIA 2g/3g/4g profiles.
The checked [upstream scheduling implementation](https://github.com/SDU-eScience/UCloud/blob/2c23d295318cabdb960a8baa69dc175e806254ac/provider-integration/im2/pkg/integrations/k8s/monitoring.go#L1256-L1278)
simulates one and two copies per product: AVAILABLE means both fit; BUSY means only one fits;
FULL means neither does. This provides useful lower-bound signals if the deployed version,
resource mapping, project eligibility and CPU/RAM constraints match. Do not add overlapping
product counts or treat a snapshot as a reservation. Preserve per-allocation Stop, recovery,
lineage and cleanup when a bounded second request still queues.

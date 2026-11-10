# UCloud GPU size for the WhisperX workflow: full B200 or MIG slices (T73)

Investigation started 23 October; implementation updated 27 October 2026 (ADR 0063).
Read-only product/wallet discovery on 26 October confirmed a funded single MIG identity.
No MIG allocation was started for this change; estimates below remain unmeasured.

## Findings

- **What UCloud offers.** DeiC Interactive HPC (SDU/K8s) offers `gpu-nvidia-b200` nodes and
  MIG products named `gpu-nvidia-b200-*-mig`. One MIG is 1/7 of a B200, and a job can request 1–4
  of them. MIG use is charged to the `gpu-nvidia-b200` allocation at 1/7 GPU-hour per hour
  ([products](https://docs.cloud.sdu.dk/guide/resources-products.html)).
- **Memory per slice.** On a B200, a 1/7 slice is NVIDIA's `1g.23gb` profile
  ([MIG profiles](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/latest/supported-mig-profiles.html)).
  Read-only product metadata now reports `B200-mig.1g.23gb`, independently of host RAM.
- **Exact product verified read-only.** Reference `gpu-nvidia-b200-1-mig.1g`, category
  `gpu-nvidia-b200`, provider `ucloud`; one GPU, fraction 1/7, Docker enabled and a matching
  positive COMPUTE wallet. New jobs recheck those facts rather than trusting this document.
- **The workflow fits comfortably.** Our worker runs one stage at a time and frees each model
  before the next, so the peak is the largest single stage:
  - faster-whisper large-v3: about 4.5 GB (fp16);
  - large-v3-turbo: about 2.5 GB (fp16);
  - wav2vec2-300m alignment: an estimated 2–4 GB;
  - pyannote community-1: a few GB (estimate; no figure is published).

  Audio is decoded in 30-second windows, so memory does not grow with recording length.
- **Speed estimate.** Roughly 4–8 minutes of large-v3 transcription per interview hour on one
  slice. This is an estimate from public benchmarks and the slice's memory bandwidth; it is not
  measured.
- **Software.** PyTorch cu128 supports Blackwell from 2.7, and MIG does not change how kernels
  run. B200 MIG needs NVIDIA driver R570.133.20 or newer, which is UCloud's responsibility.
  CTranslate2 4.8.3 compiles some kernels on first start on Blackwell, which may add a start-up
  delay.
- **Several MIGs in one job.** A process can see several MIG devices, but faster-whisper and
  torch use only the first one. Using several slices therefore needs one worker per slice, each
  with `CUDA_VISIBLE_DEVICES=MIG-<uuid>`.
- **Transcriber app.** Its documentation does not mention MIG. It says large-v3 needs about
  10 GB and that it uses one GPU. Whether the app runs on MIG products is unverified.

## Current implementation and acceptance

1. **New CBS Runtime work uses one verified MIG slice.** There is no silent full-GPU fallback.
   Native Transcriber keeps its fixed full-B200 setup. Older frozen runtime jobs keep their
   exact original product; partial or unknown new policy fields are rejected.
2. **Dynamic shared runtime batches and concurrent allocations are T85.** Merely increasing
   worker threads would allocate one GPU job per recording and would not meet the user's
   batching requirement. Durable allocation/member ownership and a late-join protocol come first.
3. **First live acceptance:** one MIG slice, a 90-minute Danish interview, speaker labels on.
   Record peak GPU memory and wall time.

## Implementation details

`runtime_compute.py` resolves the allowlisted identity and verifies the current project wallet.
`enhancement_api.py` freezes `ucloud_api.runtime_product` and `runtime_compute_policy: mig-v1`
independently of the native setup. Its Terminal specification uses the separate product.
Readiness requires at least 8 GiB on the first CUDA device, never summed GPU or host memory,
before any private upload. A failed check stops the owned job. The floor is a conservative
guard for the current sequential worker, not a measured performance guarantee.

Tests mock metadata, allocation and GPU reports, including missing/ambiguous funding,
compatibility errors, legacy snapshots and failed readiness. Native discovery/compute rules
have not been widened. Full-GPU fallback or multiple slices require a separate explicit policy.

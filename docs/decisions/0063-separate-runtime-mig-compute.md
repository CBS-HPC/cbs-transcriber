# 0063 — Separately frozen MIG compute for new CBS Runtime work

Accepted 27 October 2026. Amends ADR 0038, 0039 and 0055.

Native Transcriber retains its fixed B200 contract. CBS Runtime enhancements and WhisperX
transcription instead use one verified funded B200 MIG slice for new work. Read-only product
and wallet metadata on 26 October identified the exact reference
`gpu-nvidia-b200-1-mig.1g / gpu-nvidia-b200 / ucloud`, reporting one
`B200-mig.1g.23gb` device and fraction 1/7. Host RAM does not establish GPU memory.

Before enqueue, resolve that exact product against this connection's provider and project:
require matching COMPUTE category, a positive usable project wallet, Docker support and
AVAILABLE or BUSY queue status. Ambiguity or missing funding/support refuses the new request;
there is no silent full-GPU or different-slice fallback. BUSY allows ordinary UCloud queueing.
Selection is read-only and starts no allocation.

Freeze `ucloud_api.runtime_product` and `runtime_compute_policy: "mig-v1"` independently of
the native connection's product. Terminal specification construction uses that identity.
Readiness requires at least 8 GiB on the first actual CUDA device used by the worker, before
private audio upload. Never sum devices or use host memory. Failed readiness stops the owned
allocation under the existing protocol. This floor supports the current sequential bounded
worker; it is not a performance guarantee or permission to run parallel GPU stages.

Earlier jobs without either new field retain their exact native-product snapshot. Partially
present or unknown policy fields are rejected, rather than falling back to a full GPU.
This introduces no database schema change. Mocked selection, specification and readiness
tests are automated evidence; performance and first live MIG execution remain separate
acceptance work. Dynamic multi-file runtime allocations remain T85.

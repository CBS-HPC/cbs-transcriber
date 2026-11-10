# 0124 — Fixed native Transcriber MIG for new requests

Supersedes the new-request allocation-choice portion of ADR 0123. The user chose
the B200 1g MIG for all new native Transcriber and CBS Runtime requests and removed
the machine selectors. Admission freezes the exact native MIG product without a
full-GPU fallback, regardless of previously saved native model preferences. The
underlying saved connection and its approvals are not rewritten. Background
preflight still verifies the exact funded product before upload and submission.

An existing native UCloud template binds its own product, so new requests using
one are refused with an instruction to reconnect without it; the app does not
silently retarget the template. Already accepted intents, retry payloads and
submitted jobs retain their original frozen allocation, including older full-GPU
and template jobs. CBS Runtime retains its own fixed-MIG execution policy.

One public two-file large-v3-turbo test and later user-reported batches do not
establish that all models or larger recordings fit the 1g slice. Allocation and
quality acceptance remain separate, and the app must report allocation failure
without automatically requesting a full GPU.

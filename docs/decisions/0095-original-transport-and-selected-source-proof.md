# 0095 — Compressed original transport and bounded older repair admission

Accepted 29 October 2026 for T87f/T90f. This extends ADR 0075/0085/0086 without a
schema migration. Existing frozen WAV requests and selected-repair jobs keep their contracts.

Fresh whole CBS Runtime batch plans declare `cbs-original-transport-v1`. Per-member
source identity records its deterministic transport choice; sources do not split compatible
allocations. Only fully inspected `original-v1` sources may use unchanged compressed bytes:
one audio stream, no video, MP3/M4A/audio-only MP4/FLAC/Ogg/AAC, at most 512 MiB and six
decoded hours, and fewer source bytes than normalized PCM. All other sources retain local
original-derived WAV preparation. OpenAI/local processing and bounded clip requests retain
their existing routes. No original upload is inferred from a filename alone.

This moves the second WAV materialization to the existing owned GPU allocation. The local
full-source inspection still hashes and decodes the original before admission; this is not
complete CPU offload or a demonstrated GPU-time/cost improvement. Remote preparation currently
runs in the sequential owned inference child. New runtime preparation remains explicit.

The runtime recipe pins imageio-ffmpeg 0.6.0's Linux x86-64 wheel and its bundled FFmpeg
7.0.2 binary (`e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99`).
The supervisor publishes decoder capability before readiness; the app verifies the exact
capability before uploading a request, source descriptor or original. The descriptor freezes
source bytes/hash/format, explicit `0:a:0`, source snapshot hash, full frame window, recipe,
sample-clock policy, expected PCM hash/frame count and remote decoder pin. It has its own
filename; source bytes never masquerade as `audio.wav`.

Captured validator bytes, input inventories and acceptance receipts remain bound to the
existing task/attempt/epoch. Decode runs in an owned Linux child with a fixed demuxer, no
network protocols, MOV data references disabled, two decoder threads, a 64 MiB single-allocation
limit, 2 GiB address-space limit and bounded CPU/wall time (3600 seconds). The parent streams
only the expected PCM byte count to a pending canonical WAV, checks available output space,
and kills/reaps/drains failed decoders. Linux parent-death handling kills the decoder if the
owning inference child dies. Prepared PCM must exactly equal the frozen local inspection's
frame count and SHA-256; similar duration or successful exit is insufficient. A decoder/build
or codec mismatch refuses inference, retaining pending evidence. There is no silent tolerance,
offset guess, replacement allocation or transport switch after publication.

The immutable preparation receipt binds the actual WAV/hash/PCM/geometry to source,
descriptor, decoder, request and zero-based sample window. Inference and language metadata use
that WAV identity. Terminal outcomes retain a separate preparation-file receipt, including
when inference fails. The app transfers and verifies the small receipt before stage validation
or result commit; it does not download a complete WAV merely to validate metadata. The same
coordinator/attempt CAS preserves the receipt and compatible original-input evidence without
replacing concurrent journals. The producing version retains the remote decoder observation
separately from the local source-inspection decoder. Reports validate captured metadata only;
current files, remote runtime and recognition quality remain explicitly unchecked.

Explicit stage retries retain the same request/source/decoder identity and canonical WAV
identity. Carried stages must match their predecessor's durable output hashes; the owned child
prepares and verifies that same WAV before validating/reusing them. An uncertain existing task
is observed rather than re-uploaded or replayed. Existing allocation-wide Stop, shutdown,
retained-output and cleanup rules remain authoritative.

Large fresh selected timing repairs no longer require an already-full-inspected original.
When no ready cache exists, HTTP accepts only `selected-original-v1` metadata: exact selected
windows/IDs, original format, observed recording duration and any existing byte anchor.
The repair worker hashes the owned original and durably anchors the first complete observation
before its existing bounded first-packet/first-stream timeline probe. It never creates or
backfills the full original-readiness cache. Probe failure, Stop and retry retain that anchor;
changed original bytes cannot be adopted by the same repair. A pre-proof Retry advances a
separate source-attempt epoch, fencing a stale worker even if the job state cycles back to
running. A completed hash is anchored before honoring a Stop arriving at its observer boundary.

This scoped original-v1 receipt keeps `audio_frames`/whole PCM hash unknown and labels its
duration as a container observation. Only a matching selected-repair admission, exact IDs and
windows, fixed-language alignment-only options, and real extracted WAV receipts may consume it.
Each clip is at most 60 seconds; its actual frame count must equal the requested interval.
For these new scoped requests only, `-copyts` retains absolute source PTS through seek,
resampling and sample trim. The inspector observes every selected decoded frame before the
final clip-zero rewrite: its first PTS must equal the requested original start frame, every
subsequent PTS must continue the sample clock, and the total must end at the requested frame.
The receipt and bundle require the exact `selected-pcm-contiguous-v1` window marker. This
does not infer continuity outside the selected windows. A real synthetic timestamp-gap
fixture verifies the absolute nonzero seek, exact samples after the gap and refusal across it;
checking only output PTS zero would not establish those facts.
The observed duration is an upper selection bound, not invented global sample evidence.
Unsupported offsets, damaged clips or contradictory proof fail before allocation. Small/single
requests retain local behavior. The worker uploads the committed selected-clip bundle only,
never the original, and cannot widen its frozen selection or require another ASR result.

No real recordings, UCloud allocation, private upload, paid request or model download is part
of this delivery's verification. Resource, codec/timebase, speech/timing-quality and cost
acceptance on the approved MIG remain separately observable work, not inferred from mocks.

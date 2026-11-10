# Remote original preparation and older selected repairs — 29 October 2026

Implementation and synthetic verification for T87f/T90f, [ADR 0095](decisions/0095-original-transport-and-selected-source-proof.md).
Work took place in an isolated managed checkout; no running application environment was
synchronized and no main checkout/library was edited by this work.

## Delivered behavior

Fresh whole CBS Runtime plans select unchanged compressed originals only when the frozen
full original-v1 proof, supported audio-only format, byte limit and payload comparison permit
it. Other sources retain local original-derived WAV preparation. A pinned decoder capability
is checked before private upload. The owned remote child streams a bounded canonical WAV,
proves exact expected PCM/frame identity and publishes immutable source/decoder/request/input
receipts before inference. The app verifies transferred preparation evidence before validating
stages and creating a result, without downloading a complete WAV. Legacy frozen requests,
OpenAI/local processing and bounded clip requests keep their existing transport contracts.

Large selected timing repairs for older recordings accept metadata only. The worker anchors
complete original bytes before its bounded probe and preserves that anchor through Stop and
failure. Pre-proof retries advance an epoch; stale workers cannot publish after state cycles.
Only selected clips are decoded/uploaded. Scoped receipts keep global frames/PCM unknown and
require an absolute source-PTS/contiguous-sample marker before bundle admission. Existing
remote ownership, uncertain submission, per-unit recovery/Stop/partial Apply and cleanup
remain authoritative.

## Verification

- Focused affected-source run: **74 passed, 2 skipped** in 79.85 s. It covered existing
  original input/receipt behavior, compressed transport, real local result creation through
  the mocked Runtime coordinator, configured/Automatic language metadata without a local WAV,
  source epoch/Stop boundaries, older public admission and selected repair routing.
  Skips were Windows symlink-creation privileges and the Linux-only decoder test.
- Earlier transport/controller compatibility run: **60 passed, 1 skipped**. Earlier broader
  repair checks passed 106 cases; two new fixtures were corrected to use fresh recording
  metadata and consistent Windows extended paths. Counts overlap and must not be added.
- Actual synthetic discontinuity check: 4 s of mono PCM was muxed with a 1 s Matroska
  timestamp gap. Selected windows starting at 0.5 s and 3.25 s exposed original absolute
  PTS **8000** and **52000** at 16 kHz before clip-zero rewriting. The after-gap output
  exactly equalled the original physical samples from 2.25–2.75 s. A 1.5–3.5 s selection
  crossing the gap refused publication. This checks intended absolute windows, not merely
  output PTS zero. The test is retained in `tests/test_older_repair_source.py`.
- A separate tiny Linux execution used Ubuntu's Python 3.10 standard library and the
  extracted hash-verified imageio-ffmpeg wheel in a dedicated no-pip test environment.
  The actual pinned Linux FFmpeg decoded a synthetic 2 s FLAC (**21,012 bytes**) to a
  canonical WAV (**64,044 bytes**, **32,000 frames**), exactly matching the Windows
  original-v1 inspection's PCM SHA-256
  `61a3f4cb0e91f3d16e0cd3aeb43c7840ba9d2fc6e2accd900823ab7dab0fc535`.
  Reusing the completed receipt produced identical evidence. The Linux wheel SHA-256 was
  `c7e46fcec401dd990405049d2e2f475e2b397779df2519b544b8aab515195282`;
  the executed binary SHA-256 was
  `e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99`.
  This exercises the preparation wrapper, resource bootstrap and binary, not the complete
  CPython 3.12/CUDA inference runtime. A platform-gated regression test is retained.
- Ruff checks and formatting passed for all 19 affected Python/test files.
- Independent code-only review of source admission, epoch CAS, scoped evidence, absolute
  timeline proof, bundle admission and clip-only upload returned **GO**, with no must-fix
  within that scope. When integrating the separately developed restored-library quarantine,
  add its owned-job/owner guards to the two new direct writers; this schema-13 branch does
  not claim that schema-15 integration by itself.

## Limits

No real recordings, UCloud allocation, private upload, paid provider request, trained weights
or ASR inference were used for these checks. The only network file fetched was the exact
already-lockfile-pinned decoder wheel for the isolated Linux check. No live runtime was
prepared or downloaded, and the app's local environment was not reinstalled.

The full local original inspection still decodes each source first. Remote preparation
currently runs sequentially in its owned inference child; overlap, throughput, GPU idle time
and cost improvements are unmeasured. One synthetic lossless FLAC round trip does not qualify
every codec, container, architecture or real interview. Exact PCM disagreement fails before
inference; it never authorizes a guessed time offset or silent transport/allocation switch.
Real approved MIG, broad codec/timebase, resource, speech/timing-quality and institutional
acceptance remain separate. Browser derivative preparation remains local presentation work.

## Reproduce the focused checks

Use the repository-local locked environment in a development checkout:

```powershell
uv run pytest tests/test_audio_inputs.py tests/test_remote_audio_input.py tests/test_remote_preparation_batches.py tests/test_older_repair_source.py tests/test_repair_routing.py tests/test_repair_api.py -q -o addopts=''
uv run ruff check .
uv run ruff format --check .
```

The retained Linux-only test additionally requires the pinned Linux x86-64 decoder package;
Windows uses fake decoder streams for transport/refusal boundaries and actual bundled FFmpeg
for selected-window timeline checks. No test requests an allocation or model.

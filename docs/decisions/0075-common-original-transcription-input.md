# 0075 — Common original transcription input

Accepted 27 October 2026 after the user required all transcription backends to start from
the same raw recording. Implementation proceeds in bounded offline stages under T87.

The managed immutable original is the common source of new transcription and alignment
work. Browser playback derivatives are presentation files and must not become the source
of new original-v1 inference. Provider-required compression, resampling, channel selection
and splitting derive directly from the original. This establishes common source identity;
it does not claim identical internal processing by independent services.
Prefer original bytes when the backend's validated format/size contract permits them.
Conversion is a required compatibility/chunking step, not a mandatory lossy intermediate.
Freeze that transport choice so retries cannot silently switch between originals and derivatives.

Fresh request boundaries freeze a strict server-built audio_input policy original-v1 with
original identity/hash/bytes/format, first audio stream 0:a:0, observed duration/timebase and
recipe identity. Long hashing/probing/decoding stays outside queue/SQLite locks. Do not add a
default in the shared job insertion helper: old frozen parents create follow-ups there.
New follow-ups inherit the original source selected with their fresh parent request;
missing policy always means the exact legacy playback path. Retries never acquire a new
policy implicitly. Source identity is per recording/member, outside allocation compatibility.

Validate owned regular paths and physical source bytes. New prepared input reuse requires a
durable receipt binding original identity, recipe, actual prepared hash/bytes/sample frames
and selected window. Existence of plan.json or audio.wav is insufficient. Publish a completed
receipt only after successful strict decoding and completeness/timebase checks. Failed or
cancelled conversion cannot publish partial input as complete. Already validated completed
stages remain recoverable without regenerating audio or starting inference.

Existing recordings.duration is a container observation, not proof of decoded first-stream
duration. Never rewrite it and invalidate frozen ownership/language evidence. Preserve the
new job's effective duration/timebase separately and check it against actual sample frames.
Nonzero/negative PTS, stream defaults, video/audio offsets, legitimate unequal durations and
truncated input need explicit fixtures. Unsupported mappings refuse clearly; do not invent
an offset or hide discrepancies with an unmeasured tolerance.

Stage 1 retains the remote WAV/worker protocol and changes fresh source/receipt handling.
Its first delivery covers whole-recording ASR, whole-result enhancement and their fresh
whole-result follow-ups. Explicit bounded timing/selected-passage enhancement retains its
legacy source until cached source inspection can prove codec/timebase handling without
decoding outside the requested windows. Passage re-transcription stays legacy until T90's
bounded bundles; incidental auto-edit realignment stays legacy so Save cannot block on
whole-file inspection. These explicit scope deferrals are not format-dependent silent fallbacks.
Native Transcriber keeps uploading originals; its application metadata alone does not prove
stream selection or every codec. Claim only the validated contract. Stage 2 separates durable
original-input readiness from playback status, allowing source-ready work to proceed while
browser conversion continues. It must not reserve GPU allocations for unvalidated sources.
Until Stage 2 is complete, describe Stage 1 as direct-original preparation, not independent
playback readiness.
Stage 1's synchronous source inspection at HTTP enqueue is also an activation hold under V8;
moving it into durable background admission must precede release of the preparation change.
Preserve one native allocation for an exact compatible selected group, including late source
inspection completions; do not split that selection into separate ready-cohort allocations.

Uploading compressed/original files to CBS Runtime and decoding remotely is a later transport
optimization, requiring a pinned decoder/protocol, checksums, recovery/timebase tests and
measurements. The earlier MP3 reuse experiment remains evidence for legacy behavior, not the
chosen default. Protected policy, current approval checks and immutable result lineage remain.

Original and preparation evidence belongs in the producing version's existing context and
is retained through edits and merges. Research reports validate ownership and exact receipt
binding inside their bounded read snapshot, without opening files or inspecting today's
decoder. Expose source aliases, hashes, sizes, numeric sample/window observations and pinned
recipe/decoder hashes; omit internal IDs, filenames, paths and free-text decoder output.
Local decoded sample evidence does not prove a remote service's internal processing. Missing
preparation evidence and legacy sources remain explicit gaps. Preflight a separate 32 MiB
context-metadata budget before loading version contexts, alongside payload/source budgets.

# 0007 — Named providers and independent transcript results

Accepted: 20 October 2026. Extends decisions 0002–0004.

A provider configuration names an endpoint, model, language, response capability, and upload
limit. Multiple configurations may use the same endpoint (for example Whisper and speaker
diarization). Credentials continue to be shared per endpoint in the OS credential store;
configuration lists, jobs, and result metadata contain no keys. The former settings endpoint
continues to operate on the default configuration for existing clients and the benchmark script.

Each transcription job produces a saved result with its frozen provider metadata, original
and edited segments, and speaker names. Imports also create results. SQLite retains the
recording's existing transcript fields as the active-result view for API compatibility.
Result edits and active-view updates share one transaction. Switching results increments the
recording revision so another tab cannot apply stale edits to the wrong result.

New results are selected automatically only when no transcript is active. Adding another
provider's output never overwrites the transcript under review. Results can be selected for
editing/export or compared read-only beside the active result, using the same audio player.
There is no automatic alignment or speaker-identity matching between providers.

Migration is transactional and runs under the library lock. Existing recordings become saved
"Existing transcript" results without modifying text, original timings, speaker names, or
revision. Existing settings become the first named configuration. Existing job snapshots and
raw responses remain usable. A database backup is taken before updating the user's library.

One worker remains sufficient. Different providers can queue on the same recording; duplicate
active requests for a configuration are rejected. Each job freezes its configuration and
commits at most one result. Completed chunks survive errors/restarts, and retries stay explicit.
The UI captures the visibly selected provider before waiting for preference persistence;
a quick Transcribe click must not submit work to the previously selected provider (V15).

Review evidence: the former recording-level transcript update (`Store.save_transcript`) and
recording-wide busy check (`app.transcribe`) would respectively overwrite comparison results
and reject multiple provider jobs. V12–V14 and migration/API/browser tests cover those seams.
Gate: GO with transactional migration, independent results, and stale-edit protection.

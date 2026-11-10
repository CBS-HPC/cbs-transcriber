# 0055 — CBS WhisperX runtime as a UCloud Terminal batch job; speakers on UCloud

Accepted 23 October 2026. Amends ADR 0046 and ADR 0053.

The first live UCloud WhisperX runs, on 23 October 2026, ended with `FAILURE` and exit code 1
seconds after starting. The supervisor never wrote `supervisor.json`, and no recording was
uploaded. The run used the Transcriber Batch app with our supervisor as its `initScript`. That
app runs the init script and then its own transcription program, which ends the job when it
has no input file. The researcher also expected UCloud WhisperX to need no local speaker run
beforehand.

## Decision

- **Run as a Terminal batch job.** CBS runtime jobs (enhancement, WhisperX transcription and
  passage re-transcription) are submitted to UCloud's Terminal app.
  - The app's documented "Batch processing" parameter `batchScript` (a Bash .sh file) runs the
    supervisor and ends the job when it exits.
  - The flavour is chosen read-only from the Terminal group: `terminal-ubuntu`, then
    `terminal-debian`, then `terminal-almalinux`. It must declare `batchScript` as an
    `input_file`.
  - The chosen name and version are journaled in the run (`script_application`), so a retry
    reuses exactly that app.
  - The specification sets only `batchScript`. It mounts the run folder writable and the runtime
    folder read-only (writable only for an explicit preparation). It uses the connection's GPU
    product, and SSH is off.
- **The supervisor finds its folders.** It looks for its run and runtime folders under `/work`
  if the expected mount path is missing.
- **Speakers run on UCloud.** If the gated pyannote snapshot is neither cached on this computer
  nor seeded in the runtime:
  - Queueing is allowed only with explicit preparation consent ("Prepare the UCloud runtime",
    or the WhisperX prepare confirmation) and a saved Hugging Face token. Otherwise the refusal
    explains exactly what is needed.
  - During the job, before any private upload, the app downloads the pinned snapshot into this
    computer's Hugging Face cache using the token, then copies it to the runtime as before.
  - Speaker detection itself runs only on UCloud. The token is used only on this computer and
    is never uploaded.

## Consequences

The Transcriber app stays in use for native UCloud Transcriber models (ADR 0036/0045). Its
connection still provides the project, drive, credentials and GPU product for the CBS runtime.
The Terminal flavours, and `batchScript`, were confirmed from the user's app catalogue on
23 October 2026; the new path still needs one live acceptance run. The two failed run folders
remain on the drive with only scripts in them; the run that cleans them up is not yet decided.

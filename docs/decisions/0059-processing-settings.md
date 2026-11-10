# 0059 — Processing settings in Provider Settings; one-click actions in the editor

Accepted 23 October 2026. Amends ADR 0046, 0047, 0053 and 0054.

The "Add word timing / speakers" and "Re-transcribe passages" dialogs repeated machine setup
in every run: language, device, the Python runtime and its check, the Hugging Face token,
permission to download models, preparing the UCloud runtime and checking it. The researcher
asked for three things:

- move these settings to Provider Settings;
- check and set up the runtimes automatically, here and on UCloud, and use a local GPU by
  default when there is one;
- make Re-transcribe on a passage start straight away.

The researcher also asked to take the "Add word timing / speakers…" button off the recording
page.

## Decision

- **Provider Settings → Processing.** One tab holds:
  - the transcript language and the processing device ("Automatic: GPU when available", CPU
    or CUDA);
  - standing permission to download missing pinned local models when a job needs them (off by
    default);
  - the Python runtime, under Advanced;
  - the rules for Automatic.

  The tab shows this computer's runtime and GPU status and the UCloud runtime status. Both
  are checked when the tab opens: read-only, and starting no job. Changes save
  automatically.
- **Checks happen by themselves.**
  - **At startup.** The app checks this computer's runtime and GPU once, in the background.
    The check runs in the selected interpreter, without models or network. Reading the status
    later starts no new check.
  - **Before a UCloud job.** Explicit UCloud work on an unprepared runtime is asked about once
    (409 `prepare_runtime`: one confirmation, then the same request with preparation). This
    replaces the per-run "Prepare the UCloud runtime" checkbox. Automatic routing still never
    prepares a runtime.
- **Automatic device.** New libraries default to `auto`. A job that will run a stage resolves
  it from the cached check: CUDA when a GPU was found, otherwise CPU. The concrete device is
  written into the job, so provenance names the device actually used. Finished checkpoints are
  finalised without a runtime check. Saved explicit choices are kept; the tab offers Automatic
  when a GPU is found.
- **One-click actions.**
  - Re-transcribe on a passage starts at once with Automatic place, large-v3-turbo and the
    saved language.
  - "Re-transcribe all N of M flagged passages" asks one confirmation.
  - The tag row above the passages adds "Add speaker labels" when the result has none. That
    action, and timing for passages too long to time alone, run a whole-result enhancement
    (Automatic place) after one confirmation.
  - The "Add word timing / speakers…" button is removed from the recording page. The dialog
    remains only as the feature step of batch transcription and shows the settings summary
    with a link to Processing.

## Consequences

Job parameters still freeze everything they use, and earlier jobs keep their recorded options.
Standing download permission is a researcher's explicit setting, never a default. Tests set
`CBS_TRANSCRIBER_NO_STARTUP_CHECK` and reset the cached check, so they never run the real
hardware check.

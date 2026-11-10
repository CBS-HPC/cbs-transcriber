# 0036 — Web-capable Transcriber Batch

Accepted 22 October 2026. Refines ADRs 0012/0028: native Batch behavior is not determined
solely by `invocation.applicationType == BATCH`.

A read-only discovery retry with the user identified the actual Transcriber 1.12 contract:
application name `transcriber`, flavour `Batch`, API type `WEB`; fields include `inputFile`,
`outputDir`, `outputFormat` and boolean `jupyterLab`. The documented Batch application
exits automatically after transcription when JupyterLab is disabled. The separate
`transcriber-gui` application must remain excluded.

Share one contract check between discovery and native execution. Accept genuine BATCH
applications as before. Also accept the exact named Transcriber Batch flavour with a
declared boolean JupyterLab switch, explicitly forced false. Apply that override after
declared defaults or template parameters, without modifying the source template. Preserve
the template's machine/resources and retain rejection of extra file/script inputs before upload.
An arbitrary WEB application, GUI flavour, missing switch or nonboolean switch is unsupported.

During new configuration discovery only, resolve legacy default names `input_file`,
`output_dir`, `output_format` to the verified declared `inputFile`, `outputDir`, `outputFormat`
when the legacy names are absent. Explicit custom names are preserved, not guessed or replaced.
Return resolved names to the form and freeze them on Save/enqueue. Existing frozen settings
are not rewritten. Execution validates the pinned identity and declared fields again.

The captured fixture retains only identity, flavour, type and parameter names/types. Tests
add synthetic defaults, enum options, optionality and tool metadata; those are not claimed
as live observations. Cover discovery, hostile/GUI variants, inherited JupyterLab=true,
unchanged template resources and full mocked transfer/submit/download. No live job, recording
upload or token persistence was needed for the diagnosis. Remove temporary catalog instrumentation
afterward; no diagnostic logger belongs in normal processing.

Sources: [public schema subset](../../tests/fixtures/ucloud_transcriber_batch_1_12.json),
[fixture provenance](../../tests/fixtures/ucloud_transcriber_batch_1_12.README.md),
[official Batch parameters and JupyterLab behavior](https://docs.cloud.sdu.dk/Apps/transcriber.html#transcriber-batch).

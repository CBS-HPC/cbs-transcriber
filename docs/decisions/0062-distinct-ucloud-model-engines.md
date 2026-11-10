# 0062 — Distinct UCloud model engines in one connection

Accepted 27 October 2026. Amends ADR 0028, 0040 and 0053.

The UCloud connection offers two execution engines. Native Transcriber models run its Batch
application; CBS Runtime models run WhisperX through a Terminal batchScript. Keeping both
inside the `ucloud_transcriber` catalog group incorrectly described the CBS worker as a
Transcriber model and exposed it to native-only enumeration handling.

Provider catalog schema 2 separates `openai`, `ucloud_transcriber` and `ucloud_cbs_runtime`.
Runtime entries require `engine: "whisperx"` and a supported `whisper_model`; native entries
cannot use that engine. Model IDs are unique across both UCloud groups. Provider profile
expansion combines the groups for the shared connection; native application metadata uses
only the native group. The UI groups both Available models and transcription Model selectors,
with `(Transcriber)` and `(CBS Runtime)` suffixes on catalog labels.

Schema-1 user overrides remain readable: split their explicit engine entries in memory,
without editing the file or renaming custom labels. Empty UCloud groups have a null default.
Reject duplicate IDs, wrong engines and malformed groups before startup with a safe error.
Existing saved names, enable flags, approvals, selected IDs, results and frozen jobs are not
rewritten. This is a catalog version change, not a SQLite migration.

Unit/API tests cover grouping, legacy overrides, validation and profile preservation.
Chromium checks grouped choices in project/review selectors and Provider Settings.

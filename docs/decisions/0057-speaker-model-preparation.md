# 0057 — Speaker model preparation in Provider Settings and before any GPU job

Accepted 23 October 2026. Amends ADR 0055.

A UCloud WhisperX run with speaker labels failed twice with "Local file operation failed".

- **First run.** The token's account had not accepted the gated model's terms (B125).
- **Second run, after the terms were accepted.** The download was interrupted: on Windows
  without symlinks, Hugging Face moves each downloaded file into place, and one move failed.
  The cache kept 6 of 7 files and no completion record.

In both runs the download happened after the UCloud GPU job had been submitted, so each failure
also ended an allocation. A repeated attempt at the same download succeeded. The researcher
asked for speaker download (Hugging Face access, retry, file check) to be part of the Provider
Settings checks.

## Decision

- **Pinned files, verified.** The app verifies the size and SHA-256 of each file that speaker
  detection loads from the pinned `pyannote/speaker-diarization-community-1` revision. The
  required files are `config.yaml`, `embedding/`, `segmentation/` and `plda/`. A partial or
  damaged copy is never used or copied; it is fetched again.
- **Robust download.** Only the pinned files are downloaded (`allow_patterns`).
  - A dropped connection or a briefly locked file is retried up to three times, with a short
    back-off; finished files are kept, so each attempt resumes.
  - Refusals (gated, 401, 403, 404) are not retried.
  - Each failure is named: terms not accepted (with the link), connection, or a local file
    error with its reason. "Local file operation failed" is no longer used for this.
- **Part of Provider Settings.** Hugging Face → **Check token** checks the token and the
  model's terms. When access is allowed, it also downloads and verifies the model on this
  computer. The tab shows three lines: token, terms (with a link to the model page while they
  are not accepted), and "Speaker model: downloaded and verified on this computer". Opening the
  tab reports the local copy without any network use.
- **Before allocation.** A UCloud job with speaker labels prepares the speaker model before it
  requests a GPU. If the runtime has no completed copy, the job verifies the local files. With
  explicit preparation consent it fetches them using the steps above. It then copies them to
  the runtime and confirms the completion marker. Only after that is the Terminal job
  submitted. While a GPU job is running, nothing is downloaded. If the running job still
  reports the model missing, it is copied again only from the verified local files.
- Unchanged:
  - The token is used only on this computer.
  - Speaker detection runs only on UCloud.
  - Queueing still checks gated access first (B125).

- **The runtime copy is checked file by file (B127).** A third run, still on the old code,
  copied the files in sorted order, so the dot-file `.cbs-complete` went first. Its upload of
  `plda/xvec_transform.npz` was then interrupted, leaving a marker that claims a complete
  model.

  The fix:
  - The marker is never one of the copied files. It is written only after every pinned file
    is confirmed on UCloud at its pinned size.
  - A runtime copy counts as present only when all files and the marker are there.
  - Missing or wrong-size files are copied again, each upload retried up to three times.
    Only transfer errors are retried; a refused request keeps its own message.
- **Damaged local files are removed before fetching (B128).** The Hub never re-downloads a
  file already present in a pinned snapshot. So any pinned file that fails verification is
  deleted, with its cache blob, before the download. A download that returns without error but
  leaves files missing is tried once more, then named as incomplete. A rejected token (401) is
  named as such, and rate limiting (429) is retried like a dropped connection.

## Consequences

A local download problem now costs no GPU allocation and points to Provider Settings. Tests use
synthetic pinned hashes, an isolated Hugging Face cache and a download stub; they never read the
real cache or download. The pinned hashes must change together with the pinned revision.

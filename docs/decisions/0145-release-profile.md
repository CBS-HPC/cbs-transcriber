# 0145 — One release profile hides unfinished features without stranding data

Status: accepted for synthetic verification. The MVP release branch, its release candidate and
real Windows acceptance remain separate work (docs/work-plans/mvp-2026-11-11.md).

A colleague will test an early release while several features are still prototypes or parked:
passage Re-transcribe, Topics & Sentiments, the Research Report, CBS Runtime transcription,
UCloud word timing/speakers/repairs, Automatic routing rules, an alternative Python for local
processing, per-passage realignment after saving, comparison wording adoption, UCloud backup
upload/retention, the restore panel and custom/UCloud vLLM providers. Removing their code would
strand saved data and recovery; leaving them visible invites work the release cannot support.

One central switch, `release_profile.py`, decides the effective profile when the app starts:
`--profile full|mvp`, then `CBS_TRANSCRIBER_PROFILE`, then the packaged
`catalogs/release.json`, then `full`. An unknown name refuses startup before a library opens.
`main` ships `full`. A release branch writes `mvp` with
`uv run python -m cbs_transcriber.release_profile mvp --label "MVP rc1"` and commits the file.
The label only names the build in Settings for bug reports. `--create-shortcut` freezes an
explicit `--profile`, never the ambient environment. `full` hides nothing; `mvp` hides every
listed feature, and `GET /api/features` returns the effective profile and hidden set.

The rule is to hide entry points to new work, never existing data or recovery:

- The server writes `data-profile`/`data-off` on the page's `<html>` element before any script
  runs, so nothing flashes. Static entry points carry `data-feature`; one CSS rule per feature
  keeps them hidden even when later code un-hides them, and one script function removes hidden
  `<option>`s. Dynamic UI (passage actions, tags, model groups, the comparison, confirmations
  and help text) asks `featureOn()` and omits hidden choices so toolbars and tag rows reflow.
- A middleware refuses a fixed table of new-work routes with
  `403 {"code": "feature_disabled", "feature", "detail"}` outside the capture admission gate.
  Body-dependent refusals live in their handlers: CBS Runtime models, custom/vLLM provider
  setup and UCloud placement. Without UCloud processing, Automatic is frozen as this computer
  and Repair all never routes a large selection to UCloud, whatever saved rules say. With
  realignment hidden, a free edit saves and simply queues no realignment.
- Reads, exports and every recovery route stay open: job Stop/Retry/Reconnect/cleanup, Runtime
  batch and preparation controls, input-intent Retry/Prepare, UCloud backup Stop/Abandon/Review,
  retention retries and local backup creation. Frozen jobs keep their place. In `mvp`, the UCloud
  backup panel reappears only while an earlier upload needs attention, with upload controls
  still hidden; Activity folds routing reasons and UCloud links into a collapsed Details while
  an uncertain submission's run name stays visible.

Existing hidden-feature data stays in the library and in exports: saved re-transcription
results, codebooks and coding runs (readable through the API and exported with the project).
Restore and playback preparation for a restored library are maintainer tasks under `full`.
Local speaker labels, the Hugging Face tab, native UCloud Transcriber and OpenAI, Compare,
Preview merge, Import, Add word timing/Repair all, Show uncertainty and Collections remain.

The test suite pins `full` by default (an explicit `CBS_TRANSCRIBER_PROFILE=mvp` run is
honoured), so existing suites verify the unchanged full behavior on any branch. Synthetic
tests cover resolution, each refusal, open recovery routes, readable data and an MVP browser
walkthrough. Hiding is not a security boundary: it is a product scope for one release.

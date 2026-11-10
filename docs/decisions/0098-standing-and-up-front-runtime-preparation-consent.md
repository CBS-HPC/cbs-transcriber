# 0098 — Standing and up-front runtime preparation consent

Accepted 29 October 2026. Amends ADR 0053, 0055, 0059 and 0085.

Amended 30 October by [ADR0103](0103-processing-preparation-defaults.md): fresh/unset
preferences now default on at the user's request. Explicit saved opt-outs, frozen choices
and the Automatic-placement restriction below remain unchanged.

Before a CBS Runtime model such as Røst v3 (ADR 0084) can run on UCloud, its pinned runtime and
models must be prepared on the project drive: several GB, 20 minutes or longer, possible UCloud
charges, and local use of the saved Hugging Face token for the gated speaker model. Preparation
was "asked for, never implied", but ADR 0085 moved "Transcribe selected" to background input
admission, which discovers an unprepared runtime only after accepting the request. Every
selected file then stopped as its own terminal `needs_confirmation` card. A researcher saw 13
stopped cards. The Activity "Prepare and continue…" action also covered only the clicked file
for a non-native request, so the 11 siblings of a 12-file Røst request stayed stopped (B284).

The researcher chose to combine a standing setting with one question per request.

## Decision

- **Standing consent.** Settings → Processing gains "Prepare UCloud models automatically when
  needed" (`prepare_ucloud_models`, off by default). It is saved with the Processing choices
  but is not part of `LocalSettings`, so `EnhancementOptions` and frozen job identities are
  unchanged. A client that omits the field never changes the saved choice; saved choices are
  never changed silently. The inline note states the download size, time, UCloud charges, that
  no recording is uploaded before the runtime is ready, and that the token stays local.
- **Ask once, before acceptance.** Requests that create input intents for explicit UCloud CBS
  Runtime work (CBS Runtime full ASR, explicit UCloud follow-ups of "Transcribe selected", and
  whole explicit UCloud enhancements) first run one bounded read-only readiness probe per
  distinct connection/options target, outside queue and database locks. If the runtime or a
  pinned model is definitely missing and there is no standing consent, the whole request is
  refused with 409 `prepare_runtime` before anything is accepted. The message names each model
  and its recording count. The browser confirms once and resubmits with `prepare_runtime`.
- **Frozen provenance.** Intent version 2 choices carry `prepare_consent`: `null`, `request`
  (this request's explicit confirmation, including an Activity confirmation) or `standing`.
  It must be present exactly when a preparation flag is set. Version 1 intents keep their exact
  legacy fields and remain valid; materialized receipts keep version 1. Consent is frozen into
  every accepted choice, so background admission prepares and continues without stopping.
- **Standing consent only where needed.** It is frozen for choices whose probe reports missing
  preparation or cannot answer. A prepared runtime freezes no consent, so its jobs mount the
  runtime read-only as before.
- **Unknown readiness never blocks.** Every probe shares one 8-second deadline, and each UCloud
  read has a 4-second timeout; daemon probe threads that answer late are ignored. Offline,
  refused or slow probes make no readiness claim: the request is accepted as before, and the
  terminal `needs_confirmation` card with "Prepare and continue…" remains the fallback,
  including for already stopped intents.
- **Impossible consent is refused plainly.** When speaker labels need the gated model, it is
  neither on this computer nor on the runtime, and no Hugging Face token is saved, the request
  is refused with the existing speaker guidance instead of a preparation question.
- **One Activity confirmation per request.** For a non-native request, "Prepare and continue…"
  covers every terminal `needs_confirmation` intent of the same request ID with exactly the
  same frozen accepted choice, skipping intents that already have successors and restored
  history. Each member gets the same validation as a native cohort member (source, generation,
  current Protected policy and accepted identity). The preview count and fingerprint bind that
  exact set (`request_selection` scope), any of its cards returns the same successors, and the
  successors are created together so compatible files can share one Runtime allocation
  (ADR 0065). Native selections keep their frozen barrier.
- **Unchanged.** Automatic routing never prepares a runtime. Frozen jobs keep their frozen
  choices. Ordinary Retry never grants consent. Tokens are never uploaded. Passage
  re-transcription keeps its direct per-request 409 question.

## Consequences

The probe is the only network read at request time and makes no claim about ASR capability,
allocation availability or cost. A choice with standing consent may run in preparation mode
when the probe could not answer, even if the runtime turns out to be ready; the supervisor
then only verifies and fetches pinned models under its lock. Tests replace the probe with an
"unknown" answer by default and never contact UCloud or Hugging Face.

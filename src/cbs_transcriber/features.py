"""Preflight and freeze optional post-transcription enhancement without starting inference."""

import math

from pydantic import BaseModel

from . import enhancement
from .providers import Settings
from .store import new_id


class FeatureRequest(BaseModel):
    word_timing: bool = False
    speaker_labels: bool = False


def native_features(settings):
    return FeatureRequest(
        word_timing=settings.capability in {"words", "words_speakers"},
        speaker_labels=settings.capability in {"speakers", "words_speakers"},
    )


def effective_features(settings, requested):
    native = native_features(settings)
    return FeatureRequest(
        word_timing=native.word_timing or requested.word_timing,
        speaker_labels=native.speaker_labels or requested.speaker_labels,
    )


def prepare_followups(
    store,
    credentials,
    recordings,
    providers,
    requested,
    options,
    *,
    audio_inputs_by_id=None,
    routing_snapshot=None,
):
    if options is not None and options.language == "auto":
        raise ValueError("Automatic language follow-ups are not yet available.")
    if requested is None:
        return {}
    pending = {}
    for recording in recordings:
        for provider in providers:
            settings = Settings.model_validate(provider["settings"])
            native = native_features(settings)
            align = requested.word_timing and not native.word_timing
            diarize = requested.speaker_labels and not native.speaker_labels
            if not align and not diarize:
                continue
            if options is None:
                raise ValueError(
                    "Choose Local or UCloud enhancement settings for the missing features."
                )
            if settings.capability == "json" and diarize and not align:
                raise ValueError(
                    "Speaker detection needs word alignment for untimed output. "
                    "Select Word timing too."
                )
            duration = recording["duration"]
            if duration is None or not math.isfinite(duration) or not 0 < duration <= 21600:
                raise ValueError(
                    "Enhancement needs a known recording duration of at most six hours."
                )
            pending[recording["id"], provider["id"]] = options.model_copy(
                update={"align": align, "diarize": diarize}
            )
    if not pending:
        return {}
    frozen = {}
    if options.execution == "auto":
        from .routing import Router

        router = Router(store, credentials, options, accepted=routing_snapshot)
        recordings_by_id = {recording["id"]: recording for recording in recordings}
        for key, value in pending.items():
            concrete, extra, reason = router.decide(recordings_by_id[key[0]], value)
            frozen[key] = (concrete, {**extra, "routing": {"mode": "auto", "reason": reason}})
    elif options.execution == "local":
        enhancement.require_local_runtime(options)
        frozen = {key: (value, {}) for key, value in pending.items()}
    else:
        from .enhancement_api import freeze, freeze_profile

        if options.allow_downloads:
            raise ValueError("UCloud enhancement requires a prepared runtime and cached models.")

        projects = {
            recording["project_id"]
            for recording in recordings
            if any(key[0] == recording["id"] for key in pending)
        }
        # One frozen Provider Settings connection serves every follow-up of this request.
        # Freeze with each follow-up's own features: only those adding speakers need the
        # cached speaker model, and those that do are refused before any allocation.
        clouds = {}
        for key, value in pending.items():
            if value.diarize not in clouds:
                if routing_snapshot is None:
                    _, clouds[value.diarize] = freeze(store, credentials, sorted(projects), value)
                else:
                    chosen = routing_snapshot["connection"]
                    if chosen is None:
                        raise ValueError("No UCloud connection was accepted for these follow-ups.")
                    _, clouds[value.diarize] = freeze_profile(
                        store, credentials, chosen, sorted(projects), value
                    )
            frozen[key] = (value.model_copy(update={"device": "cuda"}), clouds[value.diarize])
    if any(
        value.execution == "local" and value.allow_downloads and value.diarize
        for value, _ in frozen.values()
    ) and not credentials.get(enhancement.HF_SCOPE):
        raise ValueError("Save a Hugging Face read token before downloading speaker models.")
    return {
        key: {
            "id": new_id(),
            "params": {
                "enhancement": value.model_dump(),
                **(
                    {"audio_input": audio_inputs_by_id[key[0]]}
                    if audio_inputs_by_id is not None
                    else {}
                ),
                "provider_name": ("UCloud" if value.execution == "ucloud" else "Local")
                + " WhisperX enhancement",
                **extra,
            },
        }
        for key, (value, extra) in frozen.items()
    }

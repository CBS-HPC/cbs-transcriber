"""Choose where optional WhisperX enhancement runs: short recordings here, long ones on UCloud.

Each decision is made once when work is queued and frozen with a readable reason; processing
never moves between computers afterwards. The default limits are heuristics: local CPU alignment
is practical for short recordings, local speaker detection is much slower, and a UCloud job adds
minutes of start-up, transfer and runtime checks that only pay off for longer audio. Researchers
can set their own limits and fallback per library (T68).
"""

import math
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field
from ucloud_workflow import UCloudAPIError

from . import enhancement, enhancement_api, providers
from . import ucloud_native as native

# Defaults: audio minutes that still finish acceptably on this computer's CPU.
LOCAL_MINUTES = {"words": 10, "speakers": 3}
# A local CUDA GPU makes local processing several times faster.
LOCAL_CUDA_FACTOR = 6
RULES_SETTING = 5


class Rules(BaseModel):
    """Where Automatic runs: limits for this computer and what to do when UCloud is unavailable."""

    model_config = ConfigDict(extra="forbid")
    word_minutes: float = Field(default=LOCAL_MINUTES["words"], ge=0, le=360)
    speaker_minutes: float = Field(default=LOCAL_MINUTES["speakers"], ge=0, le=360)
    cuda_factor: float = Field(default=LOCAL_CUDA_FACTOR, ge=1, le=20)
    # "local": run a longer recording here anyway (slow); "stop": refuse to queue it.
    fallback: Literal["local", "stop"] = "local"


def rules(store):
    rows = store.all("SELECT value FROM settings WHERE id=?", (RULES_SETTING,))
    return Rules.model_validate_json(rows[0]["value"]) if rows else Rules()


def save_rules(store, value):
    value = Rules.model_validate(value.model_dump())
    store.execute(
        "INSERT INTO settings(id,value) VALUES (?,?) "
        "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
        (RULES_SETTING, value.model_dump_json()),
    )
    return value


def local_limit(options, chosen=None):
    chosen = chosen or Rules()
    minutes = chosen.speaker_minutes if options.diarize else chosen.word_minutes
    # "auto" counts as a GPU only when this computer's last runtime check found one.
    gpu = options.device == "cuda" or (
        options.device == "auto"
        and ((enhancement.cached_hardware(options) or {}).get("gpu") or {}).get("available")
    )
    return minutes * (chosen.cuda_factor if gpu else 1)


def limits(chosen=None):
    chosen = chosen or Rules()
    return {
        "local_minutes": {"words": chosen.word_minutes, "speakers": chosen.speaker_minutes},
        "cuda_factor": chosen.cuda_factor,
        "fallback": chosen.fallback,
        "rules": chosen.model_dump(),
        "defaults": Rules().model_dump(),
    }


def minutes_text(minutes):
    return f"{minutes:.1f}" if minutes < 10 else f"{minutes:.0f}"


class Router:
    """Caches local runtime and per-project UCloud checks for one request.

    Checks use each recording's own options: follow-ups of one request can differ in whether
    they need speakers, which decides the speaker-model requirement.
    """

    def __init__(self, store, credentials, options, *, accepted=None):
        self.store, self.credentials, self.options = store, credentials, options
        self.accepted = accepted
        self.rules = (
            Rules.model_validate(accepted["rules"])
            if accepted is not None
            else rules(store)
            if store is not None
            else Rules()
        )
        self._local = None
        self._cloud = {}

    def local(self):
        # Interpreter/device readiness does not depend on the requested features.
        if self._local is None:
            try:
                enhancement.require_local_runtime(
                    self.options.model_copy(update={"execution": "local"})
                )
                self._local = (True, "")
            except ValueError as exc:
                self._local = (False, str(exc))
        return self._local

    def cloud(self, project_id, options):
        key = project_id, options.diarize
        if key not in self._cloud:
            try:
                # Automatic never prepares a runtime (ADR 0059), even when another part of the
                # same request confirmed preparation for a UCloud WhisperX model.
                candidate = options.model_copy(
                    update={
                        "execution": "ucloud",
                        "allow_downloads": False,
                        "prepare_runtime": False,
                    }
                )
                if self.accepted is None:
                    _, params = enhancement_api.freeze(
                        self.store, self.credentials, [project_id], candidate
                    )
                else:
                    chosen = self.accepted["connection"]
                    if chosen is None:
                        raise ValueError("no UCloud connection was accepted for this request")
                    _, params = enhancement_api.freeze_profile(
                        self.store, self.credentials, chosen, [project_id], candidate
                    )
                if not prepared(self.credentials, params):
                    raise ValueError("the UCloud runtime is not prepared")
                self._cloud[key] = (params, "")
            except ValueError as exc:
                self._cloud[key] = (None, str(exc).rstrip("."))
        return self._cloud[key]

    def decide(self, recording, options):
        """Return concrete frozen options, extra job params and the routing reason."""
        minutes = recording["duration"] / 60
        limit = local_limit(options, self.rules)
        length = f"{minutes_text(minutes)}-minute recording"
        local = options.model_copy(update={"execution": "local", "prepare_runtime": False})
        if minutes <= limit:
            ready, error = self.local()
            if ready:
                return local, {}, f"{length} (up to {limit:g} min run on this computer)"
            params, why = self.cloud(recording["project_id"], options)
            if params:
                return remote(options, params, f"{length}; this computer's runtime is unavailable")
            raise ValueError(error)
        params, why = self.cloud(recording["project_id"], options)
        if params:
            return remote(options, params, f"{length} (longer than {limit:g} min run on UCloud)")
        if self.rules.fallback == "stop":
            raise ValueError(
                f"The {length} is longer than the {limit:g}-minute local limit and "
                f"UCloud is unavailable ({why}). Your Automatic rules do not run it here; "
                "change the rules or choose On this computer."
            )
        ready, error = self.local()
        if ready:
            return local, {}, f"{length} on this computer (slow): UCloud unavailable, {why}"
        raise ValueError(error)


def remote(options, params, reason):
    concrete = options.model_copy(
        update={
            "execution": "ucloud",
            "device": "cuda",
            "allow_downloads": False,
            "prepare_runtime": False,
        }
    )
    return concrete, params, reason


def prepared(credentials, params):
    """Read-only storage check; a connection problem counts as not available."""
    settings = providers.Settings.model_validate(params["ucloud_api"]["settings"])
    key = credentials.get(providers.credential_scope(settings))
    try:
        with native.client_for(settings, key) as client:
            return enhancement_api.stamped(client, settings)
    except (ValueError, UCloudAPIError, httpx.HTTPError):
        return False


def repair_workload(units):
    """Selected union duration is routing evidence; transferred audio counts overlap twice."""
    if not isinstance(units, list) or not 0 < len(units) <= 1000:
        raise ValueError("Choose between one and 1,000 bounded repair units.")
    windows, identifiers = [], []
    for unit in units:
        if not isinstance(unit, dict) or set(unit) != {"ids", "start", "end"}:
            raise ValueError("The selected repair windows are invalid.")
        start, end = unit["start"], unit["end"]
        if (
            any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
            or not 0 <= start < end
            or end - start > 60
            or not isinstance(unit["ids"], list)
            or not unit["ids"]
            or any(not isinstance(i, str) or not 0 < len(i) <= 100 for i in unit["ids"])
        ):
            raise ValueError("The selected repair windows are invalid.")
        windows.append((start, end))
        identifiers.extend(unit["ids"])
    if len(identifiers) > 1000 or len(set(identifiers)) != len(identifiers):
        raise ValueError("Choose each selected repair passage only once.")
    union, right = 0, -1
    for start, end in sorted(windows):
        union += max(0, end - max(start, right))
        right = max(right, end)
    return {
        "unit_count": len(units),
        "passage_count": len(identifiers),
        "selected_seconds": union,
        "transfer_seconds": sum(b - a for a, b in windows),
    }


def repair_route(store, credentials, recording, source, units, options, *, cloud=True):
    """Fresh bounded requests only; never inspect a whole original or reroute frozen work.

    ``cloud=False`` (a release profile without UCloud processing, ADR 0145) keeps every
    selection on this computer, whatever the saved Automatic rules say."""
    from . import audio_inputs, original_inspection, repair_admission, repair_cloud, repair_remote

    chosen = rules(store)
    workload = repair_workload(units)
    limit = local_limit(options, chosen)
    local = options.model_copy(
        update={
            "execution": "local",
            "prepare_runtime": False,
            "allow_downloads": False,
            "timing_policy": "retain-estimates",
        }
    )
    decision = {
        "policy": "selected-repair-v1",
        **workload,
        "rules": chosen.model_dump(),
        "local_limit_minutes": limit,
        "source_policy": "legacy-bounded",
    }

    def here(reason):
        enhancement.require_local_runtime(local)
        return local, {
            "repair_routing": {**decision, "place": "local", "reason": reason},
            "routing": reason,
        }

    description = (
        f"{workload['passage_count']} passages ({workload['selected_seconds']:g}s selected)"
    )
    if workload["unit_count"] == 1 or workload["selected_seconds"] <= limit * 60:
        return here(f"Local word timing: {description}; small selected workload.")
    if not cloud:
        return here(f"Local word timing: {description}; this version runs word timing here.")
    why = "source-ready original-v1 proof is not ready"
    if workload["unit_count"] <= repair_remote.MAX_UNITS:
        try:
            cache = original_inspection.read(store, recording["id"])
            if cache is not None and cache["state"] == "ready":
                snapshot = audio_inputs.validate(cache["receipt"])
                if (
                    snapshot["recording_id"] != recording["id"]
                    or snapshot["duration_basis"] != "decoded_pcm_frames"
                ):
                    raise ValueError("The original-v1 repair source proof is not ready.")
                ids = [i for unit in units for i in unit["ids"]]
                if enhancement.timing_units(source, ids, local, snapshot["duration"]) != units:
                    raise ValueError("The selected windows disagree with original-v1 source proof.")
                source_extra = {
                    "audio_input": snapshot,
                    "source_policy": "original-v1",
                    "source_generation": cache["generation"],
                    "source_receipt_sha256": cache["receipt_sha256"],
                }
            else:
                # No decode/hash in the HTTP request. Only this job's selected windows may
                # be prepared after its worker proves the first-stream zero timeline.
                source_extra = {
                    "repair_source": repair_admission.choice(
                        recording, units, cache["anchor"] if cache else None
                    ),
                    "source_policy": repair_admission.POLICY,
                }
            cloud, extra = repair_cloud.freeze(
                store,
                credentials,
                [recording["project_id"]],
                local.model_copy(update={"execution": "ucloud", "device": "cuda"}),
            )
            reason = f"UCloud word timing: {description}; approved prepared connection; " + (
                "source-ready original-v1 proof is available."
                if "audio_input" in source_extra
                else "selected original-source proof will run before allocation."
            )
            return cloud, {
                **extra,
                **{k: v for k, v in source_extra.items() if k in {"audio_input", "repair_source"}},
                "routing": reason,
                "repair_routing": {
                    **decision,
                    "place": "ucloud",
                    "reason": reason,
                    **{
                        k: v
                        for k, v in source_extra.items()
                        if k not in {"audio_input", "repair_source"}
                    },
                },
            }
        except ValueError as error:
            why = str(error).rstrip(".")
        except (KeyError, UCloudAPIError, httpx.HTTPError):
            why = "the original-v1 proof or prepared UCloud connection is unavailable"
    else:
        why = f"the selection exceeds the {repair_remote.MAX_UNITS}-clip UCloud bundle limit"
    if chosen.fallback == "stop":
        raise ValueError(f"UCloud repair unavailable: {why}. Your Automatic fallback is Stop.")
    return here(f"Local word timing: {description}; UCloud unavailable because {why}.")

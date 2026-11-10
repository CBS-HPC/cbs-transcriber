"""Durable accepted selections, local preflight and same-ID producer materialization (ADR 0085)."""

import copy
import hashlib
import json
import re
from contextlib import nullcontext
from pathlib import Path

from . import (
    audio_inputs,
    language_preferences,
    original_inspection,
    providers,
    result_roles,
    routing,
)
from .catalog import whisperx_model
from .enhancement import EnhancementOptions
from .features import FeatureRequest, effective_features, prepare_followups
from .history import decode_payload, encode_payload
from .store import new_id, now
from .transcripts import MAX_TRANSCRIPT_BYTES

# Intent version 2 choices record preparation-consent provenance (ADR 0098); version 1
# intents keep their exact legacy choice fields. Materialized receipts keep version 1.
VERSION = 2
_LEGACY_VERSION = 1
_RECEIPT_VERSION = 1
MAX_BYTES = MAX_TRANSCRIPT_BYTES + 1024 * 1024
_ID = re.compile(r"[0-9a-f]{32}")
_SHA = re.compile(r"[0-9a-f]{64}")
_SAFE_PARAMS = "CASE WHEN json_valid(params) THEN params ELSE 'null' END"
_PREDECESSOR_SQL = (
    f"COALESCE(json_extract({_SAFE_PARAMS},'$.input_intent.predecessor'),"
    f"json_extract({_SAFE_PARAMS},'$.input_acceptance.predecessor'))"
)
_FIELDS = {
    "version",
    "request_id",
    "ordinal",
    "total",
    "epoch",
    "phase",
    "cancel_requested",
    "generation",
    "project_id",
    "accepted",
    "accepted_sha256",
    "source_snapshot",
    "baseline",
    "predecessor",
    "native_group",
    "error",
}
_LEGACY_CHOICE_FIELDS = {
    "operation",
    "provider",
    "options",
    "requested_features",
    "enhancement",
    "prepare_runtime",
    "routing",
    "replace",
    "source",
    "language_default",
}
_CHOICE_FIELDS = _LEGACY_CHOICE_FIELDS | {"prepare_consent"}
# Who consented to runtime/model preparation: this request's explicit confirmation, or the
# researcher's standing Settings → Processing choice (ADR 0098).
_CONSENT_SOURCES = ("request", "standing")
_ERRORS = {
    "source_error": (
        "Original inspection failed. Retry the inspection or import a supported original."
    ),
    "cancelled": "Preparation of this selected job was cancelled.",
    "preparation_failed": (
        "This job's accepted preparation could not be completed. Check its processing settings."
    ),
    "needs_confirmation": (
        "This job requires preparation consent. Start a new request with preparation confirmed."
    ),
    "provider_changed": (
        "The accepted provider identity changed or was removed. Start a new request."
    ),
    "policy_denied": "Current project policy does not approve the accepted provider.",
    "source_invalid": (
        "The accepted source evidence changed or could not be verified. Preserve its history."
    ),
}


class AdmissionError(ValueError):
    pass


class AdmissionConflict(AdmissionError):
    pass


class PolicyDenied(AdmissionError):
    pass


class StaleInput(AdmissionConflict):
    pass


def _invalid():
    raise AdmissionError("The accepted input preparation is invalid; preserve the library.")


def _canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError, RecursionError):
        _invalid()


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _identifier(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _predecessor(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"job_id", "request_id", "epoch", "accepted_sha256", "fingerprint"}
        or any(not _identifier(value[key]) for key in ("job_id", "request_id"))
        or type(value["epoch"]) is not int
        or not 1 <= value["epoch"] <= 2**53
        or any(
            not isinstance(value[key], str) or not _SHA.fullmatch(value[key])
            for key in ("accepted_sha256", "fingerprint")
        )
    ):
        _invalid()
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _parse(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BYTES:
        _invalid()
    depth, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                _invalid()
        elif char in "]}":
            depth -= 1
            if depth < 0:
                _invalid()
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _invalid())
    except (ValueError, TypeError, RecursionError):
        _invalid()


def choice(
    *,
    provider=None,
    options=None,
    requested_features=None,
    enhancement=None,
    prepare_runtime=False,
    routing_snapshot=None,
    replace=False,
    source=None,
    language_default="da",
    prepare_consent=None,
    native_allocation=None,
    language_preference=None,
):
    """Build a fresh server choice; never called from insertion/retry of executable jobs.

    `prepare_consent` records who allowed runtime/model preparation (ADR 0098). A fresh
    request flag without a stated source is this request's own explicit confirmation."""
    value = {
        "operation": "transcription" if provider is not None else "enhancement",
        "provider": copy.deepcopy(provider),
        "options": fresh_options(options).model_dump() if options is not None else None,
        "requested_features": FeatureRequest.model_validate(requested_features or {}).model_dump(),
        "enhancement": fresh_options(enhancement).model_dump() if enhancement is not None else None,
        "prepare_runtime": prepare_runtime,
        "routing": copy.deepcopy(
            routing_snapshot or {"rules": routing.Rules().model_dump(), "connection": None}
        ),
        "replace": replace,
        "source": copy.deepcopy(source),
        "language_default": language_default,
        "prepare_consent": prepare_consent,
    }
    if native_allocation is not None:
        from .native_allocation import validate

        value["native_allocation"] = validate({"native_allocation": native_allocation})
    if language_preference is not None:
        value["language_preference"] = copy.deepcopy(language_preference)
    value["routing"]["rules"] = routing.Rules.model_validate(value["routing"]["rules"]).model_dump()
    if provider is not None and whisperx_model(
        providers.Settings.model_validate(provider["settings"])
    ):
        settings = providers.Settings.model_validate(provider["settings"])
        language = {"danish": "da", "english": "en"}.get(
            settings.language.strip().lower(), settings.language.strip().lower()
        ) or language_default
        requested = effective_features(
            settings, FeatureRequest.model_validate(value["requested_features"])
        )
        value["options"] = fresh_options(
            {
                "execution": "ucloud",
                "transcribe": True,
                "align": True,
                "diarize": requested.speaker_labels,
                "whisper_model": whisperx_model(settings),
                "language": language,
                "device": "cuda",
                "prepare_runtime": prepare_runtime,
            }
        ).model_dump()
    if value["prepare_consent"] is None and _consented(value):
        value["prepare_consent"] = "request"
    return _choice(value)


def fresh_options(value):
    options = EnhancementOptions.model_validate(value)
    if "timing_policy" in EnhancementOptions.model_fields:
        options = options.model_copy(update={"timing_policy": "retain-estimates"})
    return options


def _consented(value):
    """Whether this choice carries any frozen runtime/model preparation consent."""
    return value["prepare_runtime"] or any(
        value[key] is not None and value[key]["prepare_runtime"] is True
        for key in ("options", "enhancement")
    )


def _choice(value, *, version=VERSION):
    fields = _CHOICE_FIELDS if version == VERSION else _LEGACY_CHOICE_FIELDS
    if version == VERSION and isinstance(value, dict) and "native_allocation" in value:
        fields = fields | {"native_allocation"}
    if version == VERSION and isinstance(value, dict) and "language_preference" in value:
        fields = fields | {"language_preference"}
    if version not in (VERSION, _LEGACY_VERSION) or not isinstance(value, dict):
        _invalid()
    if set(value) != fields:
        _invalid()
    if (
        value["operation"] not in ("transcription", "enhancement")
        or any(type(value[key]) is not bool for key in ("replace", "prepare_runtime"))
        or value["language_default"] not in ("da", "en")
    ):
        _invalid()
    provider = value["provider"]
    if "language_preference" in value:
        preference = value["language_preference"]
        if (
            type(preference) is not dict
            or set(preference) != {"language", "scope"}
            or type(preference["scope"]) is not str
            or preference["scope"] not in {"project", "recording"}
            or preference["language"] is None
        ):
            _invalid()
        try:
            language_preferences.validate_frozen(preference["language"])
        except ValueError:
            _invalid()
        if provider is None:
            if type(value.get("options")) is not dict or (
                value["options"].get("language") != preference["language"]
                and not language_preferences.speaker_only(value["options"])
            ):
                _invalid()
        elif (
            type(provider) is not dict
            or type(provider.get("settings")) is not dict
            or provider["settings"].get("language") != preference["language"]
        ):
            _invalid()
    if "native_allocation" in value:
        from .native_allocation import validate

        validate({"native_allocation": value["native_allocation"]})
        if (
            not isinstance(provider, dict)
            or "settings" not in provider
            or value["operation"] != "transcription"
            or value["options"] is not None
        ):
            _invalid()
        selected = providers.Settings.model_validate(provider["settings"])
        if (
            selected.provider_type != "ucloud"
            or whisperx_model(selected)
            or selected.ucloud_transcriber.template_job_id
        ):
            _invalid()
    if value["operation"] == "transcription" and provider is None:
        _invalid()
    if provider is not None:
        if (
            not isinstance(provider, dict)
            or set(provider) != {"id", "name", "settings"}
            or not _identifier(provider["id"])
        ):
            _invalid()
        if not isinstance(provider["name"], str) or not 0 < len(provider["name"]) <= 160:
            _invalid()
        if _canonical(
            providers.Settings.model_validate(provider["settings"]).model_dump()
        ) != _canonical(provider["settings"]):
            _invalid()
    for field in ("options", "enhancement"):
        if value[field] is not None and _canonical(
            EnhancementOptions.model_validate(value[field]).model_dump()
        ) != _canonical(value[field]):
            _invalid()
    if _canonical(
        FeatureRequest.model_validate(value["requested_features"]).model_dump()
    ) != _canonical(value["requested_features"]):
        _invalid()
    route = value["routing"]
    if not isinstance(route, dict) or set(route) != {"rules", "connection"}:
        _invalid()
    if _canonical(routing.Rules.model_validate(route["rules"]).model_dump()) != _canonical(
        route["rules"]
    ):
        _invalid()
    connection = route["connection"]
    if connection is not None:
        if (
            not isinstance(connection, dict)
            or set(connection) != {"id", "name", "settings"}
            or not _identifier(connection["id"])
        ):
            _invalid()
        settings = providers.Settings.model_validate(connection["settings"])
        if settings.provider_type != "ucloud" or _canonical(settings.model_dump()) != _canonical(
            connection["settings"]
        ):
            _invalid()
    source = value["source"]
    if value["operation"] == "enhancement":
        if (
            value["options"] is None
            or not isinstance(source, dict)
            or set(source) != {"result_id", "revision"}
        ):
            _invalid()
        if (
            not _identifier(source["result_id"])
            or type(source["revision"]) is not int
            or not 0 <= source["revision"] < 2**63
        ):
            _invalid()
    elif source is not None:
        _invalid()
    elif value["options"] is not None and not value["options"]["transcribe"]:
        _invalid()
    if version == VERSION and (
        value["prepare_consent"] is not None
        and value["prepare_consent"] not in _CONSENT_SOURCES
        or (value["prepare_consent"] is not None) != bool(_consented(value))
    ):
        _invalid()
    return copy.deepcopy(value)


def _kind(accepted):
    provider = accepted["provider"]
    return "enhance" if provider is None or accepted["options"] is not None else "transcribe"


def _envelope(intent):
    accepted = intent["accepted"]
    provider = accepted["provider"]
    result = {
        "input_intent": intent,
        "provider_name": provider["name"] if provider else "WhisperX enhancement",
    }
    if provider:
        result.update(provider_id=provider["id"], settings=provider["settings"])
    if accepted["options"] is not None:
        result["enhancement"] = accepted["options"]
    if provider and _kind(accepted) == "enhance":
        result["transcription"] = True
    return result


def _accepted_payload(intent):
    return {key: intent[key] for key in ("accepted", "source_snapshot", "baseline", "predecessor")}


def _validate(intent):
    if not isinstance(intent, dict) or set(intent) != _FIELDS:
        _invalid()
    if (
        type(intent["version"]) is not int
        or intent["version"] not in (VERSION, _LEGACY_VERSION)
        or any(not _identifier(intent[key]) for key in ("request_id", "generation", "project_id"))
        or type(intent["epoch"]) is not int
        or not 1 <= intent["epoch"] <= 2**53
        or type(intent["ordinal"]) is not int
        or type(intent["total"]) is not int
        or not 0 <= intent["ordinal"] < intent["total"] <= 100
        or intent["phase"] not in ("waiting", "materializing", "terminal")
        or type(intent["cancel_requested"]) is not bool
    ):
        _invalid()
    _choice(intent["accepted"], version=intent["version"])
    if intent["predecessor"] is not None:
        _predecessor(intent["predecessor"])
    baseline = intent["baseline"]
    if (
        not isinstance(baseline, dict)
        or set(baseline)
        not in (
            {"revision", "active_result_id"},
            {"revision", "active_result_id", "working_role"},
        )
        or type(baseline["revision"]) is not int
        or not 0 <= baseline["revision"] < 2**63
        or baseline["active_result_id"] is not None
        and not _identifier(baseline["active_result_id"])
    ):
        _invalid()
    if "working_role" in baseline:
        result_roles.validate_frozen(baseline["working_role"])
    if intent["accepted_sha256"] != _digest(_accepted_payload(intent)):
        _invalid()
    if intent["accepted"]["operation"] == "enhancement" and not isinstance(
        intent["source_snapshot"], dict
    ):
        _invalid()
    if intent["accepted"]["operation"] == "transcription" and intent["source_snapshot"] is not None:
        _invalid()
    group = intent["native_group"]
    if group is not None:
        if (
            not isinstance(group, dict)
            or set(group) != {"job_ids", "sha256", "resolved"}
            or not isinstance(group["job_ids"], list)
            or not 1 <= len(group["job_ids"]) <= 100
            or any(not _identifier(identifier) for identifier in group["job_ids"])
            or len(group["job_ids"]) != len(set(group["job_ids"]))
            or group["sha256"] != _digest(sorted(group["job_ids"]))
            or type(group["resolved"]) is not bool
        ):
            _invalid()
    error = intent["error"]
    if error is not None and (
        not isinstance(error, dict)
        or set(error) != {"code", "message"}
        or error.get("code") not in tuple(_ERRORS)
        or error["message"] != _ERRORS[error["code"]]
    ):
        _invalid()
    if (intent["phase"] == "terminal") != (error is not None):
        _invalid()
    return intent


def _load(db, job_id):
    if not _identifier(job_id):
        _invalid()
    row = db.execute(
        "SELECT id,recording_id,kind,state,created,length(CAST(params AS BLOB)) AS size,"
        "length(CAST(chunks AS BLOB)) AS chunks_size FROM jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise KeyError(job_id)
    if row["size"] > MAX_BYTES or row["chunks_size"] != 2:
        _invalid()
    body = db.execute("SELECT params,chunks FROM jobs WHERE id=?", (job_id,)).fetchone()
    params = _parse(body["params"])
    if not isinstance(params, dict) or "input_intent" not in params:
        raise StaleInput(
            "Preparation finished or changed scope. Refresh Activity before an allocation action."
        )
    intent = _validate(params["input_intent"])
    if (
        _canonical(params) != _canonical(_envelope(intent))
        or body["chunks"] != "[]"
        or row["kind"] != _kind(intent["accepted"])
    ):
        _invalid()
    state = (
        "queued"
        if intent["phase"] == "waiting"
        else "running"
        if intent["phase"] == "materializing"
        else "cancelled"
        if intent["error"]["code"] == "cancelled"
        else "failed"
    )
    if row["state"] != state:
        _invalid()
    recording = db.execute(
        "SELECT project_id FROM recordings WHERE id=?", (row["recording_id"],)
    ).fetchone()
    if recording is None or recording["project_id"] != intent["project_id"]:
        _invalid()
    return {**dict(row), "params": params, "intent": intent}


def _write(db, job, intent):
    from . import restored_owners

    restored_owners.require_owned(db, "job", job["id"])
    _validate(intent)
    params = _canonical(_envelope(intent))
    if len(params.encode("utf-8")) > MAX_BYTES:
        _invalid()
    state = (
        "queued"
        if intent["phase"] == "waiting"
        else "running"
        if intent["phase"] == "materializing"
        else "cancelled"
        if intent["error"]["code"] == "cancelled"
        else "failed"
    )
    message = (
        "Waiting for original inspection and preparation."
        if state == "queued"
        else "Preparing the accepted processing request."
        if state == "running"
        else intent["error"]["message"]
    )
    if state == "running" and intent["cancel_requested"]:
        message = "Cancelling preparation; waiting for active local work to stop."
    db.execute(
        "UPDATE jobs SET params=?,state=?,message=?,updated=? WHERE id=?",
        (params, state, message, now(), job["id"]),
    )


def _profile(db, recording, chosen, *, enabled=False):
    row = db.execute("SELECT value FROM providers WHERE id=?", (chosen["id"],)).fetchone()
    if row is None:
        raise AdmissionConflict(_ERRORS["provider_changed"])
    current = providers.Settings.model_validate_json(row["value"])
    accepted = providers.Settings.model_validate(chosen["settings"])
    if (current.provider_type, current.base_url, current.model, current.ucloud_transcriber) != (
        accepted.provider_type,
        accepted.base_url,
        accepted.model,
        accepted.ucloud_transcriber,
    ):
        raise AdmissionConflict(_ERRORS["provider_changed"])
    if enabled:
        providers.require_enabled(current)
    project = db.execute(
        "SELECT classification FROM projects WHERE id=?", (recording["project_id"],)
    ).fetchone()
    if (
        project is None
        or project["classification"] != "standard"
        and project["classification"] not in current.approved_for
    ):
        raise PolicyDenied(_ERRORS["policy_denied"])


def _duplicate(db, recording_id, accepted, *, exclude=None):
    for row in db.execute(
        f"SELECT id,kind,json_type({_SAFE_PARAMS})='object' AS valid,"
        "json_extract(CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
        "'$.provider_id') AS provider_id,"
        "json_extract(CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
        "'$.enhancement.execution') AS execution,"
        "json_extract(CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
        "'$.settings.base_url') AS base_url,"
        "json_extract(CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
        "'$.settings.model') AS model "
        "FROM jobs WHERE recording_id=? AND kind IN ('transcribe','enhance') "
        "AND state IN ('queued','running')",
        (recording_id,),
    ):
        if row["id"] == exclude:
            continue
        from . import restored_owners

        if restored_owners.is_restored(db, "job", row["id"]):
            continue
        if not row["valid"]:
            return True  # Unknown active ownership is a conflict on this file, never on another.
        provider = accepted["provider"]
        if provider is not None:
            if (
                row["provider_id"] == provider["id"]
                or row["provider_id"] is None
                and (row["base_url"], row["model"])
                == (provider["settings"]["base_url"], provider["settings"]["model"])
            ):
                return True
        elif row["kind"] == "enhance":
            execution = accepted["options"]["execution"]
            other = row["execution"]
            if execution == "auto" or other == "auto" or execution == other:
                return True
    return False


def _native_allocation_key(accepted):
    return "native_allocation" in accepted, _canonical(accepted.get("native_allocation"))


def accept(store, items, *, request_id=None):
    """One metadata transaction accepts every combination and its shared inspection work."""
    original_inspection._lease(store)
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise AdmissionError("Select between one and 100 recording/provider combinations.")
    request_id = request_id or new_id()
    if not _identifier(request_id):
        _invalid()
    identifiers, pending, groups = [], [], {}
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for ordinal, item in enumerate(items):
            if not isinstance(item, dict) or set(item) not in (
                {"recording_id", "choice"},
                {"recording_id", "choice", "language_guard"},
            ):
                _invalid()
            accepted = _choice(item["choice"])
            recording = db.execute(
                "SELECT * FROM recordings WHERE id=?", (item["recording_id"],)
            ).fetchone()
            if recording is None:
                raise KeyError(item["recording_id"])
            if "language_guard" in item:
                try:
                    language_preferences.require_current(db, recording, item["language_guard"])
                except language_preferences.Conflict as error:
                    raise AdmissionConflict(str(error)) from None
                if accepted.get("language_preference") != language_preferences.selected(
                    item["language_guard"]
                ):
                    _invalid()
            if _duplicate(db, recording["id"], accepted) or any(
                rid == recording["id"] and _canonical(a) == _canonical(accepted)
                for _, rid, a, _ in pending
            ):
                raise AdmissionConflict(
                    "This recording has active work for the selected provider or enhancement lane."
                )
            if accepted["provider"]:
                _profile(db, recording, accepted["provider"], enabled=True)
                if recording["active_transcript_id"] is not None and not accepted["replace"]:
                    raise AdmissionConflict("Confirm adding another transcript first.")
            if accepted["routing"]["connection"]:
                _profile(db, recording, accepted["routing"]["connection"], enabled=True)
            source = accepted["source"]
            snapshot = (
                store._freeze_result(db, recording["id"], source["result_id"], source["revision"])
                if source is not None
                else None
            )
            cache = original_inspection._ensure(db, recording["id"])
            identifier = new_id()
            from . import restored_owners

            restored_owners.require_owned(db, "job", identifier)
            intent = {
                "version": VERSION,
                "request_id": request_id,
                "ordinal": ordinal,
                "total": len(items),
                "epoch": 1,
                "phase": "waiting",
                "cancel_requested": False,
                "generation": cache["generation"],
                "project_id": recording["project_id"],
                "accepted": accepted,
                "accepted_sha256": "",
                "source_snapshot": snapshot,
                "predecessor": None,
                "baseline": {
                    "revision": recording["revision"],
                    "active_result_id": recording["active_transcript_id"],
                    "working_role": result_roles.read(db, store, recording["id"]),
                },
                "native_group": None,
                "error": None,
            }
            intent["accepted_sha256"] = _digest(_accepted_payload(intent))
            chosen = accepted["provider"]
            if (
                chosen
                and _kind(accepted) == "transcribe"
                and chosen["settings"]["provider_type"] == "ucloud"
            ):
                key = (
                    recording["project_id"],
                    chosen["id"],
                    _canonical(chosen["settings"]),
                    _native_allocation_key(accepted),
                )
                groups.setdefault(key, []).append(identifier)
            pending.append((identifier, recording["id"], accepted, intent))
            identifiers.append(identifier)
        for identifier, recording_id, accepted, intent in pending:
            chosen = accepted["provider"]
            if (
                chosen
                and _kind(accepted) == "transcribe"
                and chosen["settings"]["provider_type"] == "ucloud"
            ):
                members = groups[
                    intent["project_id"],
                    chosen["id"],
                    _canonical(chosen["settings"]),
                    _native_allocation_key(accepted),
                ]
                intent["native_group"] = {
                    "job_ids": members,
                    "sha256": _digest(sorted(members)),
                    "resolved": False,
                }
            db.execute(
                "INSERT INTO jobs(id,recording_id,kind,created,updated) VALUES (?,?,?,?,?)",
                (identifier, recording_id, _kind(accepted), now(), now()),
            )
            _write(db, {"id": identifier}, intent)
            result_roles.authorize_job(
                db,
                store,
                identifier,
                recording_id,
                "result",
                intent["baseline"]["working_role"],
                intent["source_snapshot"],
            )
    return [store.job(identifier) for identifier in identifiers]


def _terminal(intent, code):
    intent.update(
        phase="terminal",
        error={"code": code, "message": _ERRORS[code]},
        cancel_requested=code == "cancelled",
    )


def cancel(store, job_id, *, epoch, phase):
    from . import restored_owners

    restored_owners.require_job(store, job_id)
    original_inspection._lease(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        job = _load(db, job_id)
        intent = job["intent"]
        if type(epoch) is not int or intent["epoch"] != epoch or intent["phase"] != phase:
            raise StaleInput(
                "Preparation changed. Refresh Activity before choosing its current action."
            )
        if intent["phase"] == "waiting":
            intent["epoch"] += 1
            _terminal(intent, "cancelled")
        elif intent["phase"] == "materializing":
            intent["cancel_requested"] = True
        _write(db, job, intent)
    return store.job(job_id)


def claim(store):
    original_inspection._lease(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        # A waiting intent whose own inspection is still pending/running cannot yield a token:
        # _claim_ready returns None for every cohort containing it (or refuses the evidence).
        # Skip it before full validation, so large batches waiting on inspection do not
        # re-parse every intent under the write lock on each poll (ADR 0146).
        rows = db.execute(
            "SELECT id FROM jobs WHERE json_type("
            "CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
            "'$.input_intent') IS NOT NULL AND "
            "(state='queued' OR (state IN ('failed','cancelled') AND "
            "json_extract(CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
            "'$.input_intent.native_group.resolved')=0)) AND NOT ("
            "json_extract(CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
            "'$.input_intent.phase') IS 'waiting' AND EXISTS (SELECT 1 FROM recordings r "
            "WHERE r.id=jobs.recording_id AND json_valid(r.original_input) AND "
            "json_extract(r.original_input,'$.state') IN ('pending','running'))) "
            "ORDER BY created,id"
        ).fetchall()
        seen = set()
        for row in rows:
            if row["id"] in seen:
                continue
            from . import restored_owners

            if restored_owners.is_restored(db, "job", row["id"]):
                continue
            db.execute("SAVEPOINT input_claim")
            try:
                job = _load(db, row["id"])
                cohort = _cohort(db, job)
                seen.update(member["id"] for member in cohort)
                token = _claim_ready(db, cohort)
            except (ValueError, TypeError, KeyError):
                # Nested persisted Settings/EnhancementOptions can fail their own schema
                # validation before _invalid(). Preserve that candidate and scan onward.
                db.execute("ROLLBACK TO input_claim")
                db.execute("RELEASE input_claim")
                continue
            db.execute("RELEASE input_claim")
            if token is not None:
                return token
    return None


def _cohort(db, job):
    group = job["intent"]["native_group"]
    if group is None:
        return [job]
    if group["resolved"]:
        raise StaleInput(
            "This native selection is already resolved. Select a new request for excluded files."
        )
    members = [_load(db, identifier) for identifier in group["job_ids"]]
    accepted = job["intent"]["accepted"]
    for member in members:
        intent = member["intent"]
        if (
            intent["native_group"] != group
            or intent["request_id"] != job["intent"]["request_id"]
            or intent["project_id"] != job["intent"]["project_id"]
            or intent["accepted"]["provider"] != accepted["provider"]
            or _native_allocation_key(intent["accepted"]) != _native_allocation_key(accepted)
            or member["kind"] != "transcribe"
            or accepted["provider"]["settings"]["provider_type"] != "ucloud"
        ):
            _invalid()
    return members


def _claim_ready(db, cohort):
    caches = {}
    for job in cohort:
        intent = job["intent"]
        if intent["phase"] == "materializing":
            return None
        if intent["phase"] == "terminal":
            continue
        cache = original_inspection._load(db, job["recording_id"])
        if cache is None or cache["generation"] != intent["generation"]:
            _invalid()
        if cache["state"] in {"pending", "running"}:
            return None
        caches[job["id"]] = cache
    token = {"jobs": [], "receipts": {}}
    for job in cohort:
        intent = job["intent"]
        cache = caches.get(job["id"])
        if intent["phase"] == "waiting":
            intent["epoch"] += 1
            if cache["state"] == "error":
                _terminal(intent, "source_error")
            else:
                intent["phase"] = "materializing"
                token["receipts"][job["id"]] = copy.deepcopy(cache["receipt"])
            _write(db, job, intent)
        token["jobs"].append(
            {
                "id": job["id"],
                "epoch": intent["epoch"],
                "phase": intent["phase"],
                "generation": intent["generation"],
                "accepted_sha256": intent["accepted_sha256"],
                "receipt_sha256": cache["receipt_sha256"]
                if cache and cache["state"] == "ready"
                else None,
            }
        )
    return token


def _current(db, token):
    from . import restored_owners

    if (
        not isinstance(token, dict)
        or set(token) != {"jobs", "receipts"}
        or not isinstance(token["jobs"], list)
        or not 1 <= len(token["jobs"]) <= 100
        or not isinstance(token["receipts"], dict)
    ):
        _invalid()
    jobs = []
    for member in token["jobs"]:
        if (
            not isinstance(member, dict)
            or set(member)
            != {"id", "epoch", "phase", "generation", "accepted_sha256", "receipt_sha256"}
            or type(member["epoch"]) is not int
        ):
            _invalid()
        restored_owners.require_owned(db, "job", member["id"])
        job = _load(db, member["id"])
        intent = job["intent"]
        if any(
            intent[key] != member[key]
            for key in ("epoch", "phase", "generation", "accepted_sha256")
        ):
            raise StaleInput("A newer preparation attempt owns this job.")
        if intent["phase"] == "materializing":
            cache = original_inspection._load(db, job["recording_id"])
            if (
                cache is None
                or cache["state"] != "ready"
                or cache["generation"] != member["generation"]
                or cache["receipt_sha256"] != member["receipt_sha256"]
                or _canonical(cache["receipt"]) != _canonical(token["receipts"].get(job["id"]))
            ):
                raise StaleInput("Original inspection changed during preparation.")
        jobs.append(job)
    if len({job["id"] for job in jobs}) != len(jobs) or {
        job["id"] for job in _cohort(db, jobs[0])
    } != {job["id"] for job in jobs}:
        _invalid()
    return jobs


def _source(db, job):
    intent = job["intent"]
    source = intent["source_snapshot"]
    if source is None:
        return
    row = db.execute(
        "SELECT v.payload_sha256,length(CAST(v.payload AS BLOB)) AS size,"
        "r.recording_id,v.result_id "
        "FROM result_versions v JOIN transcript_results r ON r.id=v.result_id WHERE v.id=?",
        (source.get("source_version_id"),),
    ).fetchone()
    if (
        row is None
        or row["size"] > MAX_TRANSCRIPT_BYTES
        or row["recording_id"] != job["recording_id"]
        or row["result_id"] != source.get("id")
        or row["payload_sha256"] != source.get("source_payload_sha256")
    ):
        raise AdmissionError(_ERRORS["source_invalid"])
    payload = db.execute(
        "SELECT payload FROM result_versions WHERE id=?", (source["source_version_id"],)
    ).fetchone()[0]
    decoded = decode_payload(payload, row["payload_sha256"])
    if (
        encode_payload(source.get("segments"), source.get("speakers"))[0] != payload
        or decoded["segments"] != source["segments"]
    ):
        raise AdmissionError(_ERRORS["source_invalid"])


def _acceptance(job, token_member):
    intent = job["intent"]
    result = {
        "version": _RECEIPT_VERSION,
        "request_id": intent["request_id"],
        "ordinal": intent["ordinal"],
        "accepted_sha256": intent["accepted_sha256"],
        "epoch": intent["epoch"],
        "generation": intent["generation"],
        "receipt_sha256": token_member["receipt_sha256"],
        "native_group": copy.deepcopy(intent["native_group"]),
        "predecessor": copy.deepcopy(intent["predecessor"]),
    }
    if "language_preference" in intent["accepted"]:
        result["language_preference"] = copy.deepcopy(intent["accepted"]["language_preference"])
    if "working_role" in intent["baseline"]:
        result["working_role"] = copy.deepcopy(intent["baseline"]["working_role"])
    return result


def authorizes_attachment(db, recording, job_id, params):
    """Only an existing exact materializing row authorizes accepted-profile attachment."""
    from . import restored_owners

    restored_owners.require_owned(db, "job", job_id)
    receipt = params.get("input_acceptance")
    if receipt is None:
        return False
    job = _load(db, job_id)
    intent = job["intent"]
    cache = original_inspection._load(db, job["recording_id"])
    if (
        intent["phase"] != "materializing"
        or intent["cancel_requested"]
        or recording["id"] != job["recording_id"]
        or cache is None
        or cache["state"] != "ready"
        or cache["generation"] != intent["generation"]
    ):
        _invalid()
    expected = _acceptance(job, {"receipt_sha256": cache["receipt_sha256"]})
    if _canonical(receipt) != _canonical(expected) or _canonical(
        params.get("audio_input")
    ) != _canonical(cache["receipt"]):
        _invalid()
    chosen = intent["accepted"]["provider"] or intent["accepted"]["routing"]["connection"]
    actual = params.get("ucloud_api", {})
    if (
        chosen is None
        or actual.get("provider_id") != chosen["id"]
        or _canonical(actual.get("settings")) != _canonical(chosen["settings"])
    ):
        _invalid()
    proof_params = {key: value for key, value in params.items() if key != "input_acceptance"}
    _check_params(job, proof_params, cache["receipt"])
    _profile(db, recording, chosen)
    return True


def retry(store, job_id, *, epoch, phase):
    from . import restored_owners

    restored_owners.require_job(store, job_id)
    original_inspection._lease(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        job = _load(db, job_id)
        intent = job["intent"]
        if (
            type(epoch) is not int
            or intent["epoch"] != epoch
            or phase != intent["phase"]
            or phase != "terminal"
        ):
            raise StaleInput("Preparation changed. Refresh Activity before retrying.")
        if intent["native_group"] and intent["native_group"]["resolved"]:
            if _group_materialized(db, intent["native_group"]):
                raise AdmissionError(
                    "This native selection is already materialized. "
                    "Select excluded files in a new request."
                )
            for identifier in intent["native_group"]["job_ids"]:
                sibling = _load(db, identifier)
                sibling["intent"]["native_group"]["resolved"] = False
                _write(db, sibling, sibling["intent"])
            intent["native_group"]["resolved"] = False
        if intent["error"]["code"] == "needs_confirmation":
            raise AdmissionError(_ERRORS["needs_confirmation"])
        cache = original_inspection._load(db, job["recording_id"])
        if cache is None or cache["generation"] != intent["generation"]:
            _invalid()
        if cache["state"] == "error":
            cache.update(
                state="pending",
                epoch=cache["epoch"] + 1,
                updated_at=now(),
                cancel_requested=False,
                error=None,
            )
            original_inspection._write(db, job["recording_id"], cache)
        intent.update(
            phase="waiting", epoch=intent["epoch"] + 1, cancel_requested=False, error=None
        )
        _write(db, job, intent)
    return store.job(job_id)


def recover(store, *, job_ids=None):
    """Only proved, local materialization resumes automatically; no producer is replayed."""
    original_inspection._lease(store)
    if job_ids is not None and (
        not isinstance(job_ids, list)
        or not 1 <= len(job_ids) <= 100
        or any(not _identifier(identifier) for identifier in job_ids)
    ):
        _invalid()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            "SELECT id FROM jobs WHERE state='running' "
            "AND json_type(CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
            "'$.input_intent') IS NOT NULL"
            + (
                " AND id IN (" + ",".join("?" for _ in job_ids) + ")" if job_ids is not None else ""
            ),
            job_ids or [],
        ).fetchall()
        recovered = 0
        for row in rows:
            from . import restored_owners

            if restored_owners.is_restored(db, "job", row["id"]):
                continue
            job = _load(db, row["id"])
            intent = job["intent"]
            if (
                db.execute(
                    "SELECT 1 FROM runtime_batch_members WHERE job_id=?", (job["id"],)
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM transcript_results WHERE job_id=?", (job["id"],)
                ).fetchone()
            ):
                _invalid()
            intent["epoch"] += 1
            if intent["cancel_requested"]:
                _terminal(intent, "cancelled")
            else:
                intent["phase"] = "waiting"
            _write(db, job, intent)
            recovered += 1
        return recovered


def _group_materialized(db, group):
    placeholders = ",".join("?" for _ in group["job_ids"])
    rows = db.execute(
        "SELECT id,json_type(CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
        f"'$.input_acceptance') AS accepted FROM jobs WHERE id IN ({placeholders})",
        group["job_ids"],
    ).fetchall()
    if len(rows) != len(group["job_ids"]):
        raise AdmissionError(
            "This resolved selection no longer retains every member. Start a new request."
        )
    return any(row["accepted"] is not None for row in rows)


def obligations(store, *, project_id=None, recording_ids=None):
    with store.connection() as db:
        db.execute("BEGIN")
        result = []
        for row in db.execute(
            "SELECT id,recording_id FROM jobs WHERE "
            "json_extract(CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
            "'$.input_intent.native_group.resolved')=0"
        ):
            from . import restored_owners

            if restored_owners.is_restored(db, "job", row["id"]):
                continue
            job = _load(db, row["id"])
            intent = job["intent"]
            if (project_id is None or project_id == intent["project_id"]) and (
                recording_ids is None or job["recording_id"] in recording_ids
            ):
                result.append(
                    {
                        "job_id": job["id"],
                        "recording_id": job["recording_id"],
                        "project_id": intent["project_id"],
                    }
                )
        return result


def projection(store, job_id, *, connection=None):
    with store.connection() if connection is None else nullcontext(connection) as db:
        if connection is None:
            db.execute("BEGIN")
        try:
            job = _load(db, job_id)
        except StaleInput:
            return None
        intent = job["intent"]
        cache = original_inspection._load(db, job["recording_id"])
        group = intent["native_group"]
        waiting = 0
        if group and not group["resolved"]:
            for member in _cohort(db, job):
                if member["intent"]["phase"] != "terminal":
                    source = original_inspection._load(db, member["recording_id"])
                    waiting += source["state"] in {"pending", "running"}
        successors = (
            _successors(db, job)
            if intent["phase"] == "terminal" and intent["error"]["code"] == "needs_confirmation"
            else []
        )
        preview, blocker = None, None
        if (
            intent["phase"] == "terminal"
            and intent["error"]["code"] == "needs_confirmation"
            and not successors
        ):
            try:
                preview = _consent_preview(db, job, _consent_scope(db, job))
            except AdmissionError as error:
                code = next(
                    (
                        key
                        for key in ("policy_denied", "provider_changed")
                        if str(error) == _ERRORS[key]
                    ),
                    None,
                )
                if code is None:
                    raise
                blocker = {"code": code, "message": _ERRORS[code]}
        return {
            "kind": "intent",
            "phase": intent["phase"],
            "epoch": intent["epoch"],
            "request_id": intent["request_id"],
            "source_state": cache["state"] if cache else "error",
            "native_group_size": len(group["job_ids"]) if group else 0,
            "native_group_waiting": waiting,
            # An unresolved selection still owes one shared allocation decision (V162).
            "native_group_resolved": not group or bool(group["resolved"]),
            "can_cancel": intent["phase"] != "terminal" and not intent["cancel_requested"],
            "can_retry": intent["phase"] == "terminal"
            and not (group and group["resolved"] and _group_materialized(db, group))
            and intent["error"]["code"] != "needs_confirmation",
            "error_code": intent["error"]["code"] if intent["error"] else None,
            "can_prepare": preview is not None,
            "prepare_preview": preview,
            "prepare_blocker": blocker,
            "continuation": {
                "request_id": successors[0]["request_id"],
                "job_ids": [row["id"] for row in successors],
            }
            if successors
            else None,
        }


def source_readiness(store, recording_id):
    try:
        cache = original_inspection.read(store, recording_id)
    except (ValueError, KeyError):
        return {"state": "error", "duration": None, "error_code": "invalid_evidence"}
    if cache is None:
        return {"state": "uninspected", "duration": None, "error_code": None}
    from . import restored_owners

    with store.connection() as db:
        if restored_owners.is_restored(db, "job", cache["inspection_job_id"]):
            return {"state": "uninspected", "duration": None, "error_code": "restored_history"}
    return {
        "state": cache["state"],
        "duration": cache["receipt"]["duration"] if cache["state"] == "ready" else None,
        "error_code": cache["error"]["code"] if cache["error"] else None,
    }


def _request_members(db, job):
    """Stopped intents of this job's non-native request with the exact same accepted choice.

    One preparation confirmation covers every such file (ADR 0098); a request holds at most
    `total` intents, so the lookup stays bounded. Restored history is never included."""
    from . import restored_owners

    intent = job["intent"]
    if intent["native_group"] is not None or intent["phase"] != "terminal":
        _invalid()
    if job["state"] != "failed":
        return [job]  # A cancelled intent has no consent scope; its caller refuses it.
    rows = db.execute(
        "SELECT id FROM jobs WHERE state='failed' AND kind=? AND "
        f"json_extract({_SAFE_PARAMS},'$.input_intent.request_id')=? ORDER BY created,id",
        (job["kind"], intent["request_id"]),
    ).fetchall()
    if len(rows) > intent["total"] or job["id"] not in {row["id"] for row in rows}:
        _invalid()
    members = []
    for row in rows:
        if row["id"] == job["id"]:
            members.append(job)
            continue
        if restored_owners.is_restored(db, "job", row["id"]):
            continue
        other = _load(db, row["id"])
        if (
            other["intent"]["request_id"] != intent["request_id"]
            or other["intent"]["total"] != intent["total"]
        ):
            _invalid()
        if other["intent"]["native_group"] is None and _canonical(
            other["intent"]["accepted"]
        ) == _canonical(intent["accepted"]):
            members.append(other)
    return members


def _successors(db, job, *, fingerprint=None):
    """Bound lookup to this original selection's files and later jobs; never read chunk bodies."""
    group = job["intent"]["native_group"]
    if group:
        old_ids = group["job_ids"]
    elif fingerprint is not None:
        # A repeated confirmation may arrive from any card of the confirmed request scope.
        old_ids = [member["id"] for member in _request_members(db, job)]
    else:
        old_ids = [job["id"]]
    placeholders = ",".join("?" for _ in old_ids)
    originals = db.execute(
        f"SELECT recording_id,created FROM jobs WHERE id IN ({placeholders})", old_ids
    ).fetchall()
    if len(originals) != len(old_ids):
        _invalid()
    recording_ids = list({row["recording_id"] for row in originals})
    records = ",".join("?" for _ in recording_ids)
    rows = db.execute(
        f"SELECT id,json_type({_SAFE_PARAMS})='object' AS valid,"
        f"length(CAST({_PREDECESSOR_SQL} AS BLOB)) AS size,"
        f"COALESCE(json_extract({_SAFE_PARAMS},'$.input_intent.request_id'),"
        f"json_extract({_SAFE_PARAMS},'$.input_acceptance.request_id')) AS request_id,"
        f"COALESCE(json_extract({_SAFE_PARAMS},'$.input_intent.ordinal'),"
        f"json_extract({_SAFE_PARAMS},'$.input_acceptance.ordinal')) AS ordinal "
        f"FROM jobs WHERE recording_id IN ({records}) AND created>=?",
        (*recording_ids, min(row["created"] for row in originals)),
    ).fetchall()
    result = []
    for row in rows:
        if not row["valid"]:
            _invalid()
        if row["size"] is None:
            continue
        if not 0 < row["size"] <= 2048:
            _invalid()
        value = db.execute(
            f"SELECT {_PREDECESSOR_SQL} FROM jobs WHERE id=?", (row["id"],)
        ).fetchone()[0]
        predecessor = _predecessor(_parse(value))
        if (
            predecessor["request_id"] != job["intent"]["request_id"]
            or predecessor["job_id"] not in old_ids
        ):
            continue
        if fingerprint is not None and predecessor["fingerprint"] != fingerprint:
            continue
        if fingerprint is None and predecessor["job_id"] != job["id"]:
            continue
        if (
            not _identifier(row["request_id"])
            or type(row["ordinal"]) is not int
            or not 0 <= row["ordinal"] < 100
        ):
            _invalid()
        result.append({"id": row["id"], "request_id": row["request_id"], "ordinal": row["ordinal"]})
    if result and len({row["request_id"] for row in result}) != 1:
        _invalid()
    return sorted(result, key=lambda row: row["ordinal"])


def _needs_preparation(accepted):
    options = accepted["options"] or accepted["enhancement"]
    return (
        options is not None and options["execution"] == "ucloud" and not options["prepare_runtime"]
    )


def _consent_scope(db, job):
    intent, group = job["intent"], job["intent"]["native_group"]
    if (
        intent["phase"] != "terminal"
        or intent["error"]["code"] != "needs_confirmation"
        or not _needs_preparation(intent["accepted"])
        or _successors(db, job)
    ):
        return []
    if group and not group["resolved"]:
        raise StaleInput("This native preparation cohort is still settling. Refresh Activity.")
    cohort = []
    # Native selections keep their frozen barrier. Other requests cover every stopped file
    # with the same accepted choice, so one confirmation continues the whole selection.
    identifiers = group["job_ids"] if group else [row["id"] for row in _request_members(db, job)]
    for identifier in identifiers:
        marker = db.execute(
            f"SELECT json_type({_SAFE_PARAMS},'$.input_intent') AS intent,"
            f"json_extract({_SAFE_PARAMS},'$.input_acceptance.request_id') AS request_id,"
            f"json_extract({_SAFE_PARAMS},'$.input_acceptance.native_group.sha256') AS group_sha "
            "FROM jobs WHERE id=?",
            (identifier,),
        ).fetchone()
        if marker is None:
            _invalid()
        if marker["intent"] is None:
            if (
                not group
                or marker["request_id"] != intent["request_id"]
                or marker["group_sha"] != group["sha256"]
            ):
                _invalid()
            continue  # An executable/successful sibling is never replayed by consent.
        member = _load(db, identifier)
        other = member["intent"]
        if other["request_id"] != intent["request_id"] or other["native_group"] != group:
            _invalid()
        if (
            other["phase"] != "terminal"
            or other["error"]["code"] != "needs_confirmation"
            or not _needs_preparation(other["accepted"])
            or _successors(db, member)
        ):
            continue
        cache = original_inspection._load(db, member["recording_id"])
        if cache is None or cache["generation"] != other["generation"]:
            _invalid()
        _source(db, member)
        for chosen in (other["accepted"]["provider"], other["accepted"]["routing"]["connection"]):
            if chosen:
                _profile(db, {"project_id": other["project_id"]}, chosen)
        cohort.append((member, cache))
    return cohort


def _consent_preview(db, job, cohort):
    if not cohort:
        return None
    identity, labels = [], []
    for member, cache in cohort:
        intent = member["intent"]
        identity.append(
            {
                "id": member["id"],
                "epoch": intent["epoch"],
                "request_id": intent["request_id"],
                "accepted_sha256": intent["accepted_sha256"],
                "generation": intent["generation"],
                "receipt_sha256": cache["receipt_sha256"],
            }
        )
        name = db.execute(
            "SELECT name FROM recordings WHERE id=?", (member["recording_id"],)
        ).fetchone()[0]
        labels.append(
            {
                "recording_id": member["recording_id"],
                "name": name,
                "provider_name": member["params"]["provider_name"],
            }
        )
    return {
        "scope": "native_selection"
        if job["intent"]["native_group"]
        else "recording"
        if len(cohort) == 1
        else "request_selection",
        "count": len(cohort),
        "recordings": labels[:20],
        "truncated": len(labels) > 20,
        "fingerprint": _digest({"protocol": "input-preparation-consent-v1", "members": identity}),
    }


def confirm_preparation(store, job_id, *, epoch, phase, fingerprint, confirmed):
    """Create consented jobs while preserving the prior intent and choices exactly."""
    from . import restored_owners

    restored_owners.require_job(store, job_id)
    original_inspection._lease(store)
    if confirmed is not True or type(confirmed) is not bool:
        raise AdmissionError("Confirm the displayed runtime/model preparation scope first.")
    if not isinstance(fingerprint, str) or not _SHA.fullmatch(fingerprint):
        _invalid()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        job = _load(db, job_id)
        intent = job["intent"]
        if (
            type(epoch) is not int
            or epoch != intent["epoch"]
            or phase != intent["phase"]
            or phase != "terminal"
        ):
            raise StaleInput("Preparation changed. Refresh its confirmation scope.")
        repeated = _successors(db, job, fingerprint=fingerprint)
        if repeated:
            identifiers, request_id = [row["id"] for row in repeated], repeated[0]["request_id"]
        else:
            cohort = _consent_scope(db, job)
            preview = _consent_preview(db, job, cohort)
            if preview is None or preview["fingerprint"] != fingerprint:
                raise StaleInput(
                    "The preparation cohort changed. Refresh and confirm its exact scope."
                )
            request_id, identifiers = new_id(), [new_id() for _ in cohort]
            group = (
                {"job_ids": identifiers, "sha256": _digest(sorted(identifiers)), "resolved": False}
                if intent["native_group"]
                else None
            )
            for ordinal, ((member, _), identifier) in enumerate(
                zip(cohort, identifiers, strict=True)
            ):
                previous = member["intent"]
                if _duplicate(db, member["recording_id"], previous["accepted"]):
                    raise StaleInput(
                        "Another active selection now owns this file. Refresh Activity."
                    )
                renewed = copy.deepcopy(previous)
                renewed.update(
                    version=VERSION,
                    request_id=request_id,
                    ordinal=ordinal,
                    total=len(cohort),
                    epoch=1,
                    phase="waiting",
                    cancel_requested=False,
                    native_group=copy.deepcopy(group),
                    error=None,
                    predecessor={
                        "job_id": member["id"],
                        "request_id": previous["request_id"],
                        "epoch": previous["epoch"],
                        "accepted_sha256": previous["accepted_sha256"],
                        "fingerprint": fingerprint,
                    },
                )
                accepted = renewed["accepted"]
                accepted["prepare_runtime"] = True
                for key in ("options", "enhancement"):
                    if accepted[key] is not None and accepted[key]["execution"] == "ucloud":
                        accepted[key]["prepare_runtime"] = True
                # This Activity confirmation is the request's own explicit consent (ADR 0098);
                # a legacy predecessor's exact choices gain only this provenance field.
                accepted["prepare_consent"] = "request"
                renewed["accepted_sha256"] = _digest(_accepted_payload(renewed))
                db.execute(
                    "INSERT INTO jobs(id,recording_id,kind,created,updated) VALUES (?,?,?,?,?)",
                    (identifier, member["recording_id"], member["kind"], now(), now()),
                )
                _write(db, {"id": identifier}, renewed)
                result_roles.inherit_job_authority(
                    db, member["id"], identifier, member["recording_id"], renewed["accepted_sha256"]
                )
    return {"request_id": request_id, "jobs": [store.job(identifier) for identifier in identifiers]}


class NeedsConfirmation(AdmissionError):
    pass


class PreparationStopped(RuntimeError):
    """Shutdown leaves local ownership for fenced restart recovery, never a user cancellation."""


def uses_original(job, recording):
    value = job["params"].get("audio_input")
    if value is None:
        return False
    frozen = audio_inputs.validate(value)
    if (
        frozen["recording_id"] != recording["id"]
        or Path(recording["filename"]).suffix.lower() != "." + frozen["format"]
    ):
        _invalid()
    return frozen["duration_basis"] == "decoded_pcm_frames"


def snapshot_routing(store, credentials, project_id, options):
    """Freeze only locally saved rules/connection identity; no runtime or network inspection."""
    from . import enhancement_api

    chosen = None
    if options is not None and options.execution in {"auto", "ucloud"}:
        try:
            selected = enhancement_api.connection(store, credentials, [project_id])
            chosen = providers.profile(store, selected["provider_id"])
            providers.require_enabled(providers.Settings.model_validate(chosen["settings"]))
        except providers.ApprovalDenied as error:
            if options.execution != "auto":
                raise PolicyDenied(str(error)) from None
        except (ValueError, KeyError):
            if options.execution != "auto":
                raise
    return {"rules": routing.rules(store).model_dump(), "connection": chosen}


class PreparationRequired(AdmissionError):
    """Explicit UCloud work needs runtime/model preparation consent before acceptance."""

    def __init__(self, message, recordings):
        super().__init__(message)
        self.recordings = recordings


PREPARE_REQUEST = (
    "{needs}. Prepare it once as part of this request? Pinned public packages and model "
    "weights go into your UCloud project drive: this may download several GB, take 20 "
    "minutes or longer and incur UCloud charges. No recording is uploaded until the runtime "
    "is ready, and your Hugging Face token stays on this computer. To allow this "
    "automatically, use Settings → UCloud."
)


def preparation_targets(accepted):
    """The (connection, options, label) runtime preparations a fresh choice could stop for.

    Mirrors background preflight: explicit UCloud CBS Runtime transcription, explicit UCloud
    follow-ups and whole enhancements without consent. Automatic routing never prepares."""
    from .catalog import runtime_model_name
    from .features import native_features

    accepted = _choice(accepted)
    provider, route = accepted["provider"], accepted["routing"]["connection"]
    if accepted["options"] is not None:
        options = EnhancementOptions.model_validate(accepted["options"])
        if options.execution != "ucloud" or options.prepare_runtime:
            return []
        if provider is not None:
            settings = providers.Settings.model_validate(provider["settings"])
            return [(provider, options, runtime_model_name(settings) or provider["name"])]
        if route is None:
            return []
        return [(route, options, "UCloud WhisperX enhancement (CBS Runtime)")]
    followup = accepted["enhancement"]
    if (
        provider is None
        or followup is None
        or route is None
        or accepted["prepare_runtime"]
        or followup["execution"] != "ucloud"
        or followup["prepare_runtime"]
    ):
        return []
    settings = providers.Settings.model_validate(provider["settings"])
    native = native_features(settings)
    requested = FeatureRequest.model_validate(accepted["requested_features"])
    align = requested.word_timing and not native.word_timing
    diarize = requested.speaker_labels and not native.speaker_labels
    if not align and not diarize:
        return []
    options = EnhancementOptions.model_validate(followup).model_copy(
        update={"align": align, "diarize": diarize}
    )
    return [(route, options, "UCloud word timing and speaker labels (CBS Runtime)")]


def preparation_decisions(credentials, items, *, standing):
    """Ask once before acceptance (ADR 0098), outside queue/database locks.

    Returns each item's consent source to freeze (None or "standing"). Raises
    PreparationRequired when explicit UCloud work is definitely unprepared and the researcher
    has no standing consent. Readiness that cannot be read quickly makes no claim: those
    items are accepted as before and background preflight keeps its confirmation fallback."""
    from . import enhancement_api

    targets, keys = {}, []
    for item in items:
        mine = []
        for chosen, options, label in preparation_targets(item["choice"]):
            key = _canonical(
                {"settings": chosen["settings"], "options": options.model_dump(), "label": label}
            )
            targets.setdefault(key, (chosen, options, label))
            mine.append(key)
        keys.append(mine)
    if not targets:
        return [None] * len(items)
    states = enhancement_api.preparation_states(
        credentials, {key: target[:2] for key, target in targets.items()}
    )
    decisions, missing = [], {}
    for item, mine in zip(items, keys, strict=True):
        observed = [states.get(key) for key in mine]
        if all(state is not None and state["ready"] is True for state in observed):
            decisions.append(None)
            continue
        for key, state in zip(mine, observed, strict=True):
            options = targets[key][1]
            if (
                state is not None
                and options.diarize
                and state.get("speakers") is False
                and not credentials.get(enhancement_api.HF_SCOPE)
            ):
                # Preparation cannot fetch the gated speaker model without a saved token.
                raise AdmissionError(enhancement_api.speaker_refusal(credentials))
        if standing:
            decisions.append("standing")
            continue
        for key, state in zip(mine, observed, strict=True):
            if state is not None and state["ready"] is False:
                missing.setdefault(targets[key][2], set()).add(item["recording_id"])
        decisions.append(None)
    if missing:
        recordings = len(set().union(*missing.values()))
        needs = "; ".join(
            f"{label} needs the CBS WhisperX runtime and its pinned models on your UCloud "
            f"project drive for {len(rows)} recording{'s' if len(rows) != 1 else ''}"
            for label, rows in sorted(missing.items())
        )
        raise PreparationRequired(PREPARE_REQUEST.format(needs=needs), recordings)
    return decisions


def _check(store, token, job_id, cancel):
    original_inspection._lease(store)
    if cancel is not None and cancel.is_set():
        raise PreparationStopped()
    with store.connection() as db:
        db.execute("BEGIN")
        jobs = _current(db, token)
        job = next(item for item in jobs if item["id"] == job_id)
        if job["intent"]["cancel_requested"]:
            from .media import Cancelled

            raise Cancelled()
        _source(db, job)
        chosen = job["intent"]["accepted"]["provider"]
        if chosen:
            _profile(db, {"project_id": job["intent"]["project_id"]}, chosen)
    return job


def _prepared_runtime(credentials, extra):
    from . import enhancement_api

    api = extra.get("ucloud_api")
    if (
        api is not None
        and not api.get("prepare_runtime")
        and enhancement_api.runtime_stamped(credentials, api) is False
    ):
        raise NeedsConfirmation(_ERRORS["needs_confirmation"])


def prepare(store, credentials, token, cancel=None):
    """Source-dependent preflight outside write/queue locks; it never submits a producer."""
    from . import enhancement, enhancement_api
    from .media import Cancelled
    from .ucloud_native import require_compute, require_setup

    outcomes = {}
    checked_allocations = set()
    for member in token["jobs"]:
        if member["phase"] != "materializing":
            outcomes[member["id"]] = {"error": "source_error"}
            continue
        accepted = None
        try:
            job = _check(store, token, member["id"], cancel)
            accepted, intent = job["intent"]["accepted"], job["intent"]
            receipt = token["receipts"][job["id"]]
            recording = store.one(
                "SELECT id,project_id,filename,kind,status,duration,revision "
                "FROM recordings WHERE id=?",
                (job["recording_id"],),
            )
            recording = audio_inputs.effective_recording(
                {"params": {"audio_input": receipt}}, recording
            )
            provider = accepted["provider"]
            if provider is not None:
                settings = providers.Settings.model_validate(provider["settings"])
                providers.require_key(
                    settings, credentials.get(providers.credential_scope(settings))
                )
                if accepted["options"] is not None:
                    options = EnhancementOptions.model_validate(accepted["options"])
                    if not 0 < recording["duration"] <= 21600:
                        raise AdmissionError(
                            "CBS Runtime transcription supports at most six hours."
                        )
                    if not options.prepare_runtime and not enhancement_api.whisperx_ready(
                        credentials, provider, options
                    ):
                        raise NeedsConfirmation(_ERRORS["needs_confirmation"])
                    _check(store, token, job["id"], cancel)
                    concrete, extra = enhancement_api.freeze_profile(
                        store, credentials, provider, [recording["project_id"]], options
                    )
                    params = {"enhancement": concrete.model_dump(), "transcription": True, **extra}
                else:
                    if settings.provider_type == "ucloud":
                        require_setup(
                            settings, credentials.get(providers.credential_scope(settings))
                        )
                        require_compute(settings)
                        audio_inputs.require_native(receipt)
                        if "native_allocation" in accepted:
                            from . import native_allocation, ucloud_discovery, ucloud_native

                            policy = accepted["native_allocation"]
                            cache_key = (
                                provider["id"],
                                _canonical(provider["settings"]),
                                _canonical(policy),
                            )
                            if cache_key not in checked_allocations:
                                effective = providers.Settings.model_validate(
                                    native_allocation.effective(
                                        provider["settings"], {"native_allocation": policy}
                                    )
                                )
                                require_compute(effective)
                                result = ucloud_discovery.discover(
                                    effective,
                                    credentials.get(providers.credential_scope(effective)),
                                    compute_id=policy["product"]["id"],
                                )
                                if not result["ready"]:
                                    raise AdmissionError(result["note"])
                                observed = ucloud_native.NativeSetup.model_validate(result["setup"])
                                if observed.product != policy["product"] or observed.model_dump(
                                    exclude={"product"}
                                ) != effective.ucloud_transcriber.model_dump(exclude={"product"}):
                                    raise AdmissionError(
                                        "The accepted native allocation no longer matches UCloud "
                                        "metadata. Reconnect and start a new request."
                                    )
                                checked_allocations.add(cache_key)
                    followup = (
                        EnhancementOptions.model_validate(accepted["enhancement"])
                        if accepted["enhancement"] is not None
                        else None
                    )
                    if followup is not None and accepted["prepare_runtime"]:
                        followup = followup.model_copy(update={"prepare_runtime": True})
                    followups = prepare_followups(
                        store,
                        credentials,
                        [recording],
                        [provider],
                        FeatureRequest.model_validate(accepted["requested_features"]),
                        followup,
                        audio_inputs_by_id={recording["id"]: receipt},
                        routing_snapshot=accepted["routing"],
                    )
                    child = followups.get((recording["id"], provider["id"]))
                    if child is not None:
                        _prepared_runtime(credentials, child["params"])
                    params = {
                        "settings": provider["settings"],
                        "audio_transport": audio_inputs.transport_for(receipt, settings),
                        "requested_features": effective_features(
                            settings, FeatureRequest.model_validate(accepted["requested_features"])
                        ).model_dump(),
                        **({"followup": child} if child is not None else {}),
                        **(
                            {"native_allocation": copy.deepcopy(accepted["native_allocation"])}
                            if "native_allocation" in accepted
                            else {}
                        ),
                    }
                params.update(
                    provider_id=provider["id"],
                    provider_name=provider["name"],
                    revision=intent["baseline"]["revision"],
                )
            else:
                options = EnhancementOptions.model_validate(accepted["options"])
                extra = {}
                if options.execution == "auto":
                    options, extra, reason = routing.Router(
                        store, credentials, options, accepted=accepted["routing"]
                    ).decide(recording, options)
                    extra = {**extra, "routing": {"mode": "auto", "reason": reason}}
                elif options.execution == "local":
                    enhancement.require_local_runtime(options)
                else:
                    if options.allow_downloads:
                        raise AdmissionError(
                            "UCloud enhancement requires a prepared runtime and cached models."
                        )
                    chosen = accepted["routing"]["connection"]
                    if chosen is None:
                        raise AdmissionError("No UCloud connection was accepted for this request.")
                    options, extra = enhancement_api.freeze_profile(
                        store, credentials, chosen, [recording["project_id"]], options
                    )
                    _prepared_runtime(credentials, extra)
                enhancement.validate_input(
                    intent["source_snapshot"], options, recording["duration"]
                )
                if (
                    options.execution == "local"
                    and options.diarize
                    and options.allow_downloads
                    and not credentials.get(enhancement.HF_SCOPE)
                ):
                    raise AdmissionError(
                        "Add a Hugging Face read token before downloading speaker models."
                    )
                params = {
                    "enhancement": options.model_dump(),
                    "source_snapshot": intent["source_snapshot"],
                    "provider_name": ("UCloud" if options.execution == "ucloud" else "Local")
                    + " WhisperX enhancement",
                    **extra,
                }
            params["audio_input"] = receipt
            if provider is None and options.execution == "local":
                from .local_asr_capture import frozen_policy

                params = frozen_policy(params)
            from .runtime_capture import freeze as freeze_runtime_artifacts

            params = freeze_runtime_artifacts(params)
            from .provider_capture import freeze as freeze_provider_artifacts

            # Fresh API transcription only; native, Runtime and enhancement params are unchanged.
            params = freeze_provider_artifacts(params)
            from .native_capture import freeze as freeze_native_artifacts

            # Fresh native Transcriber requests only (ADR 0100).
            params = freeze_native_artifacts(params)
            _check(store, token, job["id"], cancel)
            outcomes[job["id"]] = {"params": params}
        except PreparationStopped:
            raise
        except StaleInput:
            raise
        except Cancelled:
            outcomes[member["id"]] = {"error": "cancelled"}
        except NeedsConfirmation:
            outcomes[member["id"]] = {"error": "needs_confirmation"}
        except (ValueError, OSError, KeyError) as error:
            code = next(
                (key for key, message in _ERRORS.items() if str(error) == message),
                "preparation_failed",
            )
            if (
                accepted is not None
                and _needs_preparation(accepted)
                and str(error) == enhancement_api.speaker_refusal(credentials)
            ):
                code = "needs_confirmation"
            outcomes[member["id"]] = {"error": code}
    return outcomes


def _check_params(job, params, receipt):
    accepted = job["intent"]["accepted"]
    if not isinstance(params, dict) or any(
        key in params
        for key in (
            "input_intent",
            "input_acceptance",
            "runtime_batch_id",
            "native_batch_id",
            "ucloud_run",
            "runtime_task_id",
        )
    ):
        _invalid()
    if _canonical(params.get("audio_input")) != _canonical(receipt):
        _invalid()
    provider = accepted["provider"]
    allowed = {"audio_input", "provider_name"}
    if provider is not None:
        allowed |= {"provider_id", "revision"}
        allowed |= (
            {
                "settings",
                "audio_transport",
                "requested_features",
                "followup",
                "provider_artifact_policy",
                "native_artifact_policy",
                "native_allocation",
            }
            if accepted["options"] is None
            else {"enhancement", "transcription", "ucloud_api", "runtime_artifact_policy"}
        )
    else:
        allowed |= {
            "enhancement",
            "source_snapshot",
            "ucloud_api",
            "routing",
            "producer_artifact_policy",
            "runtime_artifact_policy",
        }
    if set(params) - allowed:
        _invalid()
    from .runtime_capture import POLICY as RUNTIME_ARTIFACT_POLICY
    from .runtime_capture import eligible as runtime_artifact_eligible

    if "runtime_artifact_policy" in params and (
        params["runtime_artifact_policy"] != RUNTIME_ARTIFACT_POLICY
        or not runtime_artifact_eligible(params)
    ):
        _invalid()
    from . import provider_capture

    if "provider_artifact_policy" in params and (
        params["provider_artifact_policy"] != provider_capture.POLICY
        or not provider_capture.eligible(params)
    ):
        _invalid()
    from . import native_capture

    if "native_artifact_policy" in params and (
        params["native_artifact_policy"] != native_capture.POLICY
        or not native_capture.eligible(params)
    ):
        _invalid()
    if provider is not None:
        if (
            params.get("provider_id") != provider["id"]
            or params.get("provider_name") != provider["name"]
        ):
            _invalid()
        if accepted["options"] is None:
            if ("native_allocation" in params) != ("native_allocation" in accepted) or _canonical(
                params.get("native_allocation")
            ) != _canonical(accepted.get("native_allocation")):
                _invalid()
            if _canonical(params.get("settings")) != _canonical(provider["settings"]):
                _invalid()
            settings = providers.Settings.model_validate(provider["settings"])
            requested = FeatureRequest.model_validate(accepted["requested_features"])
            expected_features = effective_features(settings, requested).model_dump()
            if _canonical(params.get("requested_features")) != _canonical(
                expected_features
            ) or params.get("audio_transport") != audio_inputs.transport_for(receipt, settings):
                _invalid()
            from .features import native_features

            native = native_features(settings)
            align, diarize = (
                requested.word_timing and not native.word_timing,
                requested.speaker_labels and not native.speaker_labels,
            )
            child = params.get("followup")
            if bool(child) != bool(align or diarize):
                _invalid()
            if child is not None:
                if (
                    accepted["enhancement"] is None
                    or not isinstance(child, dict)
                    or set(child) != {"id", "params"}
                    or not _identifier(child["id"])
                    or not isinstance(child["params"], dict)
                ):
                    _invalid()
                values = child["params"]
                if set(values) - {
                    "enhancement",
                    "audio_input",
                    "provider_name",
                    "ucloud_api",
                    "routing",
                    "runtime_artifact_policy",
                } or _canonical(values.get("audio_input")) != _canonical(receipt):
                    _invalid()
                expected = copy.deepcopy(accepted["enhancement"])
                expected.update(align=align, diarize=diarize)
                if accepted["prepare_runtime"]:
                    expected["prepare_runtime"] = True
                _check_options(values.get("enhancement"), expected)
                if "runtime_artifact_policy" in values and (
                    values["runtime_artifact_policy"] != RUNTIME_ARTIFACT_POLICY
                    or not runtime_artifact_eligible(values)
                ):
                    _invalid()
        elif _canonical(params.get("enhancement")) != _canonical(accepted["options"]):
            _invalid()
    elif _canonical(params.get("source_snapshot")) != _canonical(job["intent"]["source_snapshot"]):
        _invalid()
    if accepted["options"] is not None:
        _check_options(params.get("enhancement"), accepted["options"])


def _check_options(value, expected):
    actual = EnhancementOptions.model_validate(value).model_dump()
    if _canonical(actual) != _canonical(value) or actual["execution"] == "auto":
        _invalid()
    if actual["execution"] == "local" and actual["device"] != expected["device"]:
        _invalid()
    if actual["execution"] == "ucloud" and actual["device"] != "cuda":
        _invalid()
    ignored = (
        {"execution", "device", "allow_downloads", "prepare_runtime"}
        if expected["execution"] == "auto"
        else {"device"}
    )
    if {key: value for key, value in actual.items() if key not in ignored} != {
        key: value for key, value in expected.items() if key not in ignored
    }:
        _invalid()
    if expected["execution"] == "auto" and (
        actual["prepare_runtime"] or actual["execution"] == "ucloud" and actual["allow_downloads"]
    ):
        _invalid()


def _used_profiles(db, recording, job, params):
    accepted = job["intent"]["accepted"]
    if accepted["provider"]:
        _profile(db, recording, accepted["provider"])
    candidates = [accepted["provider"], accepted["routing"]["connection"]]
    for extra in (params, params.get("followup", {}).get("params", {})):
        api = extra.get("ucloud_api")
        if api is None:
            continue
        chosen = next(
            (
                item
                for item in candidates
                if item is not None and item["id"] == api.get("provider_id")
            ),
            None,
        )
        if chosen is None or _canonical(api.get("settings")) != _canonical(chosen["settings"]):
            _invalid()
        _profile(db, recording, chosen)


def materialize(store, token, outcomes):
    """CAS the complete unit and its owner/membership rows in one local transaction."""
    from . import runtime_store

    original_inspection._lease(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        jobs = _current(db, token)
        if not isinstance(outcomes, dict) or set(outcomes) != {job["id"] for job in jobs}:
            _invalid()
        selected, failures = [], {}
        for job, member in zip(jobs, token["jobs"], strict=True):
            intent, result = job["intent"], outcomes[job["id"]]
            if intent["phase"] == "terminal":
                failures[job["id"]] = intent["error"]["code"]
                continue
            if intent["cancel_requested"]:
                failures[job["id"]] = "cancelled"
                continue
            if not isinstance(result, dict) or set(result) not in ({"params"}, {"error"}):
                _invalid()
            if "error" in result:
                if result["error"] not in tuple(_ERRORS):
                    _invalid()
                failures[job["id"]] = result["error"]
                continue
            params = copy.deepcopy(result["params"])
            _check_params(job, params, token["receipts"][job["id"]])
            recording = db.execute(
                "SELECT * FROM recordings WHERE id=?", (job["recording_id"],)
            ).fetchone()
            try:
                _source(db, job)
                _used_profiles(db, recording, job, params)
            except AdmissionError as error:
                failures[job["id"]] = (
                    "policy_denied"
                    if str(error) == _ERRORS["policy_denied"]
                    else "provider_changed"
                    if str(error) == _ERRORS["provider_changed"]
                    else "source_invalid"
                )
                continue
            params["input_acceptance"] = _acceptance(job, member)
            selected.append((job["id"], job["recording_id"], job["kind"], params))
        store._group_native_jobs(db, selected)
        for identifier, recording_id, kind, params in selected:
            recording = db.execute(
                "SELECT * FROM recordings WHERE id=?", (recording_id,)
            ).fetchone()
            runtime_store.attach(db, recording, identifier, kind, params)
            if params["input_acceptance"]["native_group"] is not None:
                params["input_acceptance"]["native_group"]["resolved"] = True
            db.execute(
                "UPDATE jobs SET params=?,state='queued',progress=0,"
                "message='Prepared; queued for processing.',updated=? WHERE id=?",
                (_canonical(params), now(), identifier),
            )
        for job in jobs:
            if job["id"] in failures:
                intent = job["intent"]
                _terminal(intent, failures[job["id"]])
                if intent["native_group"] is not None:
                    intent["native_group"]["resolved"] = True
                _write(db, job, intent)
    return [store.job(job["id"]) for job in jobs]

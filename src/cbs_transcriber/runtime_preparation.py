"""Explicit library-level CBS Runtime preparation; never owns recording inputs.

The SQLite owner is the submission authority. A lost POST reply requires reconciliation;
Retry creates a successor and cannot turn uncertainty into permission to submit again.
"""

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from contextlib import closing, contextmanager, nullcontext
from pathlib import PurePosixPath
from types import SimpleNamespace
from urllib.parse import quote

import httpx

from . import enhancement_api as api
from . import providers, runtime_compute, runtime_store
from . import runtime_preparation_observation as observation
from . import ucloud_native as native
from .enhancement import local_settings
from .store import new_id, now

POLICY = "cbs-runtime-preparation-v1"
MODEL_PLAN = "cbs-runtime-model-plan-v1"
MODEL_RECEIPT = "cbs-runtime-model-plan-receipt-v1"
MODEL_CHECK_SECONDS = 30
OBSERVATION_ATTEMPTS = 12
OBSERVATION_BACKOFF_SECONDS = 5
TERMINAL_SETTLE_SECONDS = 30
TERMINAL_SETTLE_ATTEMPTS = 12
_ABSENT = object()
PREFIX = "runtime-preparation:"
RECENT_ACTIVITY = 200
_CONTROL_LOCK = threading.Lock()
_CONTROLS = set()
# ADR 0176 Stage 2 (disabled): journal proof only the generation module's trusted writers
# may change on a generation-born owner. Generic/legacy writers can never inject, alter,
# re-type or remove it; legacy-born owners can never acquire the generation-only keys.
# The absence receipts are reserved too: name reconciliation never resolves a generation
# POST, so no generic writer may record one. Legacy rows keep their reconcile semantics.
GENERATION_ONLY = frozenset({"generation_folders", "generation_submission", "generation_promotion"})
GENERATION_RESERVED = GENERATION_ONLY | {
    "specification",
    "owned_folder",
    "job_id",
    "prepared",
    "doctor_verified",
    "model_plan_receipt",
    "runtime_product",
    "runtime_compute_policy",
    "reconciled_not_created",
    "no_post",
}
_IMMUTABLE_COLUMNS = frozenset({"id", "frozen", "connection_fingerprint", "archived", "created"})
DDL = """CREATE TABLE IF NOT EXISTS runtime_preparations (
    id TEXT PRIMARY KEY,
    connection_fingerprint TEXT NOT NULL,
    frozen TEXT NOT NULL,
    journal TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    epoch INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
    hidden_updated TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
)"""


class Conflict(ValueError):
    """An explicit action needs fresh saved settings or resolution of its prior owner."""


class ControlBusy(Conflict):
    """Another request owns this preparation's short-lived control slot."""


class Interrupted(Exception):
    pass


@contextmanager
def _control(identifier):
    """Logical same-owner control slot; never serialize unrelated network operations."""
    with _CONTROL_LOCK:
        if identifier in _CONTROLS:
            raise ControlBusy("Wait for this preparation's current control request.")
        _CONTROLS.add(identifier)
    try:
        yield
    finally:
        with _CONTROL_LOCK:
            _CONTROLS.remove(identifier)


def migrate(store):
    with store.library_lock(), store.connection() as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version >= 16:
            return
        if version != 15:
            raise RuntimeError("Runtime preparation must follow the schema15 migration.")
        if store.initial_version == 15:
            (store.root / "backups").mkdir(exist_ok=True)
            with closing(
                sqlite3.connect(store.root / "backups" / f"schema-15-{new_id()}.sqlite3")
            ) as copy:
                db.backup(copy)
        db.execute("BEGIN IMMEDIATE")
        db.execute(DDL)
        db.execute("PRAGMA user_version=16")


def fingerprint(chosen, *, model_plan=_ABSENT):
    return runtime_store._digest(_identity(chosen, model_plan=model_plan))


def _identity(chosen, *, model_plan=_ABSENT):
    # Capture public recipe bytes outside queue/SQLite writer locks.
    identity = {
        "policy": POLICY,
        **chosen,
        "runtime_id": api.runtime_id(),
        "recipe_sha256": recipe_hash(),
    }
    if (chosen["settings"].get("ucloud_transcriber", {}).get("product") or {}).get(
        "id"
    ) == native.MIG_PRODUCT["id"]:
        identity["connection_policy"] = "native-connection-v1"
    if model_plan is not _ABSENT:
        _validate_model_plan(model_plan)
        identity["model_plan"] = json.loads(runtime_store.canonical(model_plan))
    return identity


def _validate_model_plan(plan):
    if (
        type(plan) is not dict
        or set(plan) != {"policy", "profiles", "models", "model_revisions", "speaker_files"}
        or plan["policy"] != MODEL_PLAN
        or type(plan["profiles"]) is not list
        or not 1 <= len(plan["profiles"]) <= 100
        or type(plan["models"]) is not list
        or not 4 <= len(plan["models"]) <= 32
        or any(
            type(key) is not str or re.fullmatch(r"[a-z0-9-]{1,60}", key) is None
            for key in plan["models"]
        )
        or plan["models"] != sorted(set(plan["models"]))
        or not {"coarse", "da", "en", "speakers"} <= set(plan["models"])
        or type(plan["model_revisions"]) is not dict
        or set(plan["model_revisions"]) != set(plan["models"])
        or type(plan["speaker_files"]) is not dict
        or not 1 <= len(plan["speaker_files"]) <= 32
    ):
        raise Conflict("The configured Runtime model plan is invalid.")
    identifiers = set()
    for profile in plan["profiles"]:
        if (
            type(profile) is not dict
            or set(profile) != {"provider_id", "model", "cache_key"}
            or type(profile["provider_id"]) is not str
            or re.fullmatch(r"[a-f0-9]{32}", profile["provider_id"]) is None
            or profile["provider_id"] in identifiers
            or type(profile["model"]) is not str
            or not 1 <= len(profile["model"]) <= 200
            or profile["cache_key"] not in plan["models"]
        ):
            raise Conflict("The configured Runtime model plan is invalid.")
        identifiers.add(profile["provider_id"])
    for pin in plan["model_revisions"].values():
        if (
            type(pin) is not list
            or len(pin) != 2
            or type(pin[0]) is not str
            or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", pin[0]) is None
            or type(pin[1]) is not str
            or re.fullmatch(r"[a-f0-9]{40}", pin[1]) is None
        ):
            raise Conflict("The configured Runtime model pins are invalid.")
    for name, witness in plan["speaker_files"].items():
        if (
            type(name) is not str
            or re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", name) is None
            or any(part in {".", ".."} for part in name.split("/"))
            or type(witness) is not list
            or len(witness) != 2
            or type(witness[0]) is not int
            or not 0 < witness[0] < 2**40
            or type(witness[1]) is not str
            or re.fullmatch(r"[a-f0-9]{64}", witness[1]) is None
        ):
            raise Conflict("The configured Runtime speaker pins are invalid.")
    if len(runtime_store.canonical(plan).encode()) > 16 * 1024:
        raise Conflict("The configured Runtime model plan exceeds its bound.")


def huggingface_plan(store, credentials):
    """Offline configuration only: never inspects caches, calls providers or starts work."""
    from . import provider_connection

    try:
        if not local_settings(store).prepare_ucloud_models:
            return {"status": "disabled"}
        rows = store.all("SELECT id FROM providers ORDER BY rowid LIMIT 1001")
        if len(rows) > 1000:
            return {"status": "unavailable"}
        try:
            chosen = api.connection(store, credentials, [])
        except ValueError:
            return {"status": "needs_setup"}
        settings = providers.Settings.model_validate(chosen["settings"])
        key = credentials.get(providers.credential_scope(settings))
        with store.connection() as db:
            state = provider_connection.state(db, settings, key)
        if state != "ready":
            return {"status": "unavailable" if state == "unavailable" else "needs_setup"}
        profiles = []
        for row in rows:
            profile = providers.profile(store, row["id"])
            candidate = providers.Settings.model_validate(profile["settings"])
            if (
                candidate.enabled
                and provider_connection.identity(candidate)
                == provider_connection.identity(settings)
                and (choice := providers.whisperx_model(candidate)) in api.WHISPER_MODELS
            ):
                profiles.append(
                    {
                        "provider_id": profile["id"],
                        "model": candidate.model,
                        "cache_key": api.WHISPER_MODELS[choice],
                    }
                )
        if not profiles:
            return {"status": "needs_setup", "reason": "no_runtime_models"}
        models = sorted({"coarse", "da", "en", "speakers", *(p["cache_key"] for p in profiles)})
        plan = {
            "policy": MODEL_PLAN,
            "profiles": sorted(profiles, key=lambda p: p["provider_id"]),
            "models": models,
            "model_revisions": {name: list(api.MODELS[name]) for name in models},
            "speaker_files": {
                name: list(value) for name, value in sorted(api.SPEAKER_FILES.items())
            },
        }
        _validate_model_plan(plan)
        return {
            "status": "ready",
            "chosen": chosen,
            "model_plan": plan,
            "connection_fingerprint": fingerprint(chosen, model_plan=plan),
        }
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        OSError,
        RecursionError,
        sqlite3.Error,
    ):
        return {"status": "unavailable"}


def recipe_hash():
    return runtime_store._digest(
        {
            path.name: hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for path in (*api.WORKER_FILES, api.SUPERVISOR, api.REQUIREMENTS)
        }
    )


def _raw(db, identifier):
    row = db.execute("SELECT * FROM runtime_preparations WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise KeyError(identifier)
    return dict(row)


def _get(db, identifier, *, owned=False):
    value = _raw(db, identifier)
    if owned:
        _owned(value)  # Restored rows are inert even if their old evidence is unreadable.
    return _decode(value)


def _decode(raw):
    value = dict(raw)
    identifier = value["id"]
    try:
        if re.fullmatch(r"[a-f0-9]{32}", identifier) is None:
            raise ValueError()
        for field in ("frozen", "journal"):
            raw = value[field]
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
                raise ValueError()
            value[field] = json.loads(raw)
            if type(value[field]) is not dict:
                raise ValueError()
            runtime_store.canonical(value[field])  # Refuse nonfinite observations too.
        frozen, journal = value["frozen"], value["journal"]
        if (
            frozen.get("policy") != POLICY
            or type(frozen.get("settings")) is not dict
            or not isinstance(frozen.get("runtime_id"), str)
            or not 0 < len(frozen["runtime_id"]) <= 200
            or not isinstance(frozen.get("provider_id"), str)
            or not isinstance(frozen.get("recipe_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", frozen["recipe_sha256"])
            or journal.get("run_name") != run_name(frozen, identifier)
            or value["state"] not in runtime_store.STATES
            or type(value["epoch"]) is not int
            or value["epoch"] < 0
            or type(value["progress"]) not in (int, float)
            or not math.isfinite(value["progress"])
            or not 0 <= value["progress"] <= 1
        ):
            raise ValueError()
        settings = providers.Settings.model_validate(frozen["settings"])
        if "model_plan" in frozen:
            _validate_model_plan(frozen["model_plan"])
        if settings.provider_type != "ucloud" or settings.ucloud_transcriber is None:
            raise ValueError()
        if "connection_policy" in frozen:
            if frozen["connection_policy"] != "native-connection-v1":
                raise ValueError()
            native.require_connection(settings)
        elif (settings.ucloud_transcriber.product or {}).get("id") != native.COMPUTE_ID:
            raise ValueError()  # Preserve the exact pre-feature frozen preparation gate.
        _target(frozen)
        if journal.get("job_id") is not None and (
            not isinstance(journal["job_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", journal["job_id"])
        ):
            raise ValueError()
        if value["connection_fingerprint"] != runtime_store._digest(
            {
                key: frozen.get(key)
                for key in (
                    "policy",
                    "provider_id",
                    "settings",
                    "runtime_id",
                    "recipe_sha256",
                    *(["connection_policy"] if "connection_policy" in frozen else []),
                    *(["model_plan"] if "model_plan" in frozen else []),
                )
            }
        ):
            raise ValueError()
        if "model_plan_receipt" in journal:
            _validate_model_receipt(journal["model_plan_receipt"], frozen)
            if (
                journal.get("prepared") is not True
                or journal.get("doctor_verified") is not True
                or journal.get("remote_state") != "SUCCESS"
                or not journal.get("job_id")
            ):
                raise ValueError()
        if "generation" in frozen:
            # ADR 0176 Stage 2: only rows born with a generation reach this separately
            # disabled module. Legacy rows never import it.
            from . import runtime_generation_preparation

            runtime_generation_preparation.validate_owner(value)
        elif GENERATION_ONLY & set(journal):
            raise ValueError()
    except (ValueError, TypeError, KeyError, RecursionError):
        raise Conflict(
            "This preparation's frozen connection evidence is invalid. Preserve it."
        ) from None
    return value


def get(store, identifier, *, owned=False):
    with store.connection() as db:
        return _get(db, identifier, owned=owned)


def _owned(value):
    if value["archived"]:
        raise Conflict("This restored preparation is historical and cannot control UCloud.")


def unresolved(value):
    if value.get("unverified"):
        return not value["archived"]
    return not value["archived"] and (
        value["state"] in {"queued", "running"}
        or runtime_store._reserved(value["journal"])
        or value["journal"].get("cleanup_state") == "pending"
        or _uncertain_folder(value["journal"])
    )


def _uncertain_folder(journal):
    # A generation's unacknowledged exclusive create may have made a folder. Keep that
    # intent visible for explicit recovery; it is never adoption or retry authority.
    folders = journal.get("generation_folders")
    return type(folders) is dict and any(
        type(entry) is dict and entry.get("outcome") in {"pending", "uncertain"}
        for entry in folders.values()
    )


def obligations(store):
    with store.connection() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='runtime_preparations'"
        ).fetchone():
            return []
        values = []
        for row in db.execute("SELECT * FROM runtime_preparations WHERE archived=0"):
            try:
                value = _get(db, row["id"])
            except Conflict:
                value = {**dict(row), "unverified": True}
            if unresolved(value):
                values.append(value)
        return values


def _target(frozen):
    settings = frozen["settings"]
    setup = settings["ucloud_transcriber"]
    target = (
        settings["base_url"],
        setup["project_id"],
        setup["storage_path"],
        frozen["runtime_id"],
    )
    if "generation" in frozen:
        # A generation owns its own folder: never the legacy root or another generation,
        # so legacy deduplication/receipt reuse can never match it.
        return (*target, "generation", frozen["generation"]["token"])
    return target


def _quarantine(db, row):
    # Only operational state changes. Keep malformed frozen/journal bytes as evidence.
    db.execute(
        "UPDATE runtime_preparations SET state='failed',epoch=epoch+1,message=?,updated=? "
        "WHERE id=?",
        (
            "Preparation metadata is unverified. Preserve its evidence before further controls.",
            now(),
            row["id"],
        ),
    )


def _current(store, credentials, expected, *, model_plan=_ABSENT):
    if model_plan is not _ABSENT:
        current = huggingface_plan(store, credentials)
        if (
            current.get("status") != "ready"
            or current["model_plan"] != model_plan
            or current["connection_fingerprint"] != expected
        ):
            raise Conflict(
                "The configured Runtime model plan changed. Save Hugging Face settings again."
            )
        return current["chosen"]
    if not local_settings(store).prepare_ucloud_models:
        raise Conflict("Save automatic UCloud preparation as enabled before preparing the runtime.")
    chosen = api.connection(store, credentials, [])
    providers.require_enabled(providers.Settings.model_validate(chosen["settings"]))
    if fingerprint(chosen) != expected:
        raise Conflict(
            "The UCloud connection changed. Reopen UCloud settings to load the current "
            "UCloud connection, then check again."
        )
    return chosen


def run_name(frozen, identifier):
    # A second, independent birth anchor: stripping a generation from frozen alone fails.
    prefix = "CBS runtime generation " if "generation" in frozen else "CBS runtime preparation "
    return prefix + identifier


def _insert(db, identity, expected, *, retry_of=None, identifier=None):
    identifier, stamp = identifier or new_id(), now()
    frozen = {**identity, "retry_of": retry_of}
    journal = {"run_name": run_name(frozen, identifier)}
    db.execute(
        "INSERT INTO runtime_preparations(id,connection_fingerprint,frozen,journal,message,"
        "created,updated) VALUES (?,?,?,?,?,?,?)",
        (
            identifier,
            expected,
            runtime_store.canonical(frozen),
            runtime_store.canonical(journal),
            "Waiting to prepare the UCloud runtime…",
            stamp,
            stamp,
        ),
    )
    return _get(db, identifier)


def request(store, credentials, expected):
    """Only the explicit HTTP action calls this. Readiness errors create no operation."""
    chosen = _current(store, credentials, expected)
    settings = providers.Settings.model_validate(chosen["settings"])
    key = credentials.get(providers.credential_scope(settings))
    with native.client_for(settings, key) as client:
        prepared = api.stamped(client, settings)
    if prepared:
        return {
            "prepared": True,
            "runtime_id": api.runtime_id(),
            "operation": None,
            "deduplicated": False,
        }
    _current(store, credentials, expected)
    identity = _identity(chosen)
    return _admit(store, identity, expected)


def _admit(store, identity, expected, *, guard=None, insert=True):
    if guard is not None:
        guard()
    target, value, latest = _target(identity), None, None
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute(
            "SELECT * FROM runtime_preparations WHERE archived=0 ORDER BY created DESC,id DESC"
        ):
            try:
                candidate = _get(db, row["id"])
            except Conflict:
                # An unknown live owner cannot establish that another allocation is safe.
                raise Conflict(
                    "Resolve unverified preparation evidence in Activity "
                    "before starting another allocation."
                ) from None
            if _target(candidate["frozen"]) == target and (
                candidate["state"] in {"queued", "running"}
                or runtime_store._reserved(candidate["journal"])
            ):
                if "model_plan" in identity and (
                    candidate["frozen"].get("model_plan") != identity["model_plan"]
                    or candidate["frozen"].get("recipe_sha256") != identity["recipe_sha256"]
                ):
                    raise Conflict(
                        "Another preparation has an insufficient or unresolved model plan. "
                        "Resolve it in Activity first."
                    )
                value = candidate
                break
            if latest is None and candidate["connection_fingerprint"] == expected:
                latest = candidate
        if guard is not None and (value is not None or insert):
            # BEGIN may have waited for another writer. Validate the parent again at
            # the acceptance point; never do so after committing and lose its owner link.
            guard()
        deduplicated = value is not None
        if value is None:
            if not insert:
                return None
            # A fresh explicit click may replace a settled owner when the runtime is
            # missing again. Preserve all prior evidence and create a visible successor.
            value = _insert(db, identity, expected, retry_of=latest["id"] if latest else None)
    return {
        "prepared": False,
        "runtime_id": api.runtime_id(),
        "operation": card(value),
        "deduplicated": deduplicated,
    }


def request_huggingface(store, credentials, plan, guard):
    """Trusted HF-manager adapter after its fenced valid/allowed access stage.

    Acceptance freezes public files, never a token. Once a durable owner is accepted its
    exact link must return even if the parent is stopped or superseded during commit.
    """
    guard()
    if (
        type(plan) is not dict
        or plan != huggingface_plan(store, credentials)
        or plan.get("status") != "ready"
    ):
        raise Conflict(
            "The configured Runtime model plan changed. Save Hugging Face settings again."
        )
    model_plan, expected = plan["model_plan"], plan["connection_fingerprint"]
    api.local_speaker_files()  # Read-only hash validation; never fetch in this adapter.
    guard()
    chosen = _current(store, credentials, expected, model_plan=model_plan)
    identity = _identity(chosen, model_plan=model_plan)
    existing = _admit(store, identity, expected, guard=guard, insert=False)
    if existing is not None:
        return existing
    settings = providers.Settings.model_validate(chosen["settings"])
    key = credentials.get(providers.credential_scope(settings))
    with native.client_for(settings, key) as client:
        prepared = _model_prepared(store, client, settings, model_plan, identity)
    guard()
    _current(store, credentials, expected, model_plan=model_plan)
    if prepared:
        return {
            "prepared": True,
            "runtime_id": api.runtime_id(),
            "operation": None,
            "deduplicated": False,
        }
    return _admit(store, identity, expected, guard=guard)


def _model_bytes(client, settings, path, deadline, *, strict=False):
    """Small public receipt only, using the reviewed bounded control/transfer transport."""
    if strict:
        item = observation.metadata(client, path, deadline=deadline)
        if api._runtime_kind(item) != "FILE":
            raise observation.Refused()
        return observation.read_bytes(client, settings, path, deadline=deadline)
    item = api.runtime_metadata(client, path, deadline=deadline)
    if item is None or api._runtime_kind(item) != "FILE":
        return None
    try:
        ticket = api._runtime_request(
            client, "POST", "/api/files/download", deadline, json={"items": [{"id": path}]}
        )
        endpoint = native.transfer_url(settings.base_url, native.one_response(ticket)["endpoint"])
        data = bytearray()
        with httpx.Client(
            timeout=api._runtime_remaining(deadline), follow_redirects=False
        ) as transport:
            with transport.stream(
                "GET", endpoint, headers={"Accept-Encoding": "identity"}
            ) as reply:
                if (
                    not reply.is_success
                    or reply.headers.get("Content-Encoding", "identity") != "identity"
                ):
                    raise api.RuntimePreflightBlocked("runtime-readiness-unknown")
                api._runtime_remaining(deadline)
                for block in reply.iter_raw():
                    api._runtime_remaining(deadline)
                    if len(data) + len(block) > api.RUNTIME_METADATA_BYTES:
                        return None
                    data.extend(block)
                api._runtime_remaining(deadline)
        return bytes(data)
    except (
        native.UCloudAPIError,
        httpx.HTTPError,
        httpx.StreamError,
        ValueError,
        TypeError,
        KeyError,
    ):
        raise api.RuntimePreflightBlocked("runtime-readiness-unknown") from None


def _model_inventory(client, path, deadline, *, strict=False):
    folder = (observation.metadata if strict else api.runtime_metadata)(
        client, path, deadline=deadline
    )
    if folder is None or api._runtime_kind(folder) != "DIRECTORY":
        return None
    try:
        page = (
            observation.control(
                client,
                "GET",
                "/api/files/browse",
                deadline=deadline,
                params={"path": path, "itemsPerPage": 64},
            )
            if strict
            else api._runtime_request(
                client,
                "GET",
                "/api/files/browse",
                deadline,
                params={"path": path, "itemsPerPage": 64},
            )
        )
    except native.UCloudAPIError:
        raise api.RuntimePreflightBlocked("runtime-readiness-unknown") from None
    items = page.get("items") if type(page) is dict else None
    if type(items) is not list or len(items) > 64 or page.get("next"):
        raise api.RuntimePreflightBlocked("runtime-readiness-unknown")
    inventory, seen = [], set()
    for item in items:
        identifier = item.get("id") if type(item) is dict else None
        if (
            type(identifier) is not str
            or str(PurePosixPath(identifier)) != identifier
            or str(PurePosixPath(identifier).parent) != path
            or PurePosixPath(identifier).name in {".", ".."}
            or len(PurePosixPath(identifier).name) > 256
            or identifier in seen
        ):
            raise api.RuntimePreflightBlocked("runtime-readiness-unknown")
        seen.add(identifier)
        kind = api._runtime_kind(item)
        size = item.get("status", {}).get("sizeInBytes", item.get("sizeInBytes"))
        if kind == "DIRECTORY":
            size = None
        elif type(size) is not int or not 0 <= size < 2**50:
            raise api.RuntimePreflightBlocked("runtime-readiness-unknown")
        inventory.append({"name": PurePosixPath(identifier).name, "kind": kind, "bytes": size})
    return sorted(inventory, key=lambda item: item["name"]) if inventory else None


def _model_evidence(client, settings, model_plan, *, strict=False, deadline=None, root=None):
    """Fresh API metadata, not remote weight hashes or physical no-follow proof.

    HF snapshots ordinarily contain symlinks; their reported names/kinds/sizes are only
    inventory witnesses. A prior successful pinned prefetch is separately required.
    Absent ``root`` reads the legacy runtime; a frozen generation root is read exclusively.
    """
    deadline = time.monotonic() + MODEL_CHECK_SECONDS if deadline is None else deadline
    base = api.frozen_runtime_root(settings, root)
    read_options = {"strict": True} if strict else {}
    evidence = {
        "snapshots": {},
        "speakers": [],
        "speaker_marker": True,
        "punkt": {},
        "conversions": {},
    }
    for name in model_plan["models"]:
        folder = (
            api.model_folder(settings, name)
            if root is None
            else api.model_folder(settings, name, root=root)
        )
        if name == "speakers":
            for relative, (size, _) in model_plan["speaker_files"].items():
                item = (observation.metadata if strict else api.runtime_metadata)(
                    client, folder + "/" + relative, deadline=deadline
                )
                if item is None:
                    return None
                kind = api._runtime_kind(item)
                actual = item.get("status", {}).get("sizeInBytes", item.get("sizeInBytes"))
                if kind not in {"FILE", "SOFT_LINK"}:
                    return None
                if type(actual) is not int or actual < 0:
                    raise api.RuntimePreflightBlocked("runtime-readiness-unknown")
                if actual != size:
                    return None
                evidence["speakers"].append({"name": relative, "kind": kind, "bytes": size})
            repo, revision = model_plan["model_revisions"]["speakers"]
            if (
                _model_bytes(
                    client, settings, folder + "/" + api.SPEAKERS_COMPLETE, deadline, **read_options
                )
                != f"{repo}@{revision}\n".encode()
            ):
                return None
            continue
        inventory = _model_inventory(client, folder, deadline, **read_options)
        if not inventory or not any(
            item["kind"] != "DIRECTORY" and item["bytes"] > 0 for item in inventory
        ):
            return None
        evidence["snapshots"][name] = inventory
        if name in api.DANISH_CHOICES:
            helper = api.danish_module()
            raw = _model_bytes(
                client, settings, folder + "/" + helper.MARKER, deadline, **read_options
            )
            try:
                receipt = helper.validate_receipt(
                    json.loads(raw), choice=name, model=tuple(model_plan["model_revisions"][name])
                )
                manifest = runtime_store.canonical(
                    sorted(helper.models()[name]["source_files"], key=lambda item: item["name"])
                ).encode()
                if (
                    receipt["source"]["manifest_sha256"] != hashlib.sha256(manifest).hexdigest()
                    or receipt["recipe"]["contract_sha256"] != helper.RECIPE_CONTRACT_SHA256
                ):
                    return None
                expected = {item["name"]: item["bytes"] for item in receipt["files"]}
                if {item["name"] for item in inventory} != set(expected) | {helper.MARKER} or any(
                    item["bytes"] != expected[item["name"]]
                    for item in inventory
                    if item["name"] in expected
                ):
                    return None
                evidence["conversions"][name] = hashlib.sha256(raw).hexdigest()
            except (ValueError, TypeError, KeyError, RecursionError):
                return None
    for language in ("danish", "english"):
        inventory = _model_inventory(
            client,
            base + "/nltk/tokenizers/punkt_tab/" + language,
            deadline,
            **read_options,
        )
        if not inventory or not {
            "collocations.tab",
            "sent_starters.txt",
            "abbrev_types.txt",
            "ortho_context.tab",
        } <= {item["name"] for item in inventory if item["kind"] != "DIRECTORY"}:
            return None
        evidence["punkt"][language] = inventory
    if len(runtime_store.canonical(evidence).encode()) > 48 * 1024:
        raise api.RuntimePreflightBlocked("runtime-readiness-unknown")
    return evidence


def _model_prepared(store, client, settings, model_plan, identity):
    if not api.stamped(client, settings):
        return False
    receipt = None
    with store.connection() as db:
        rows = db.execute(
            "SELECT id FROM runtime_preparations WHERE archived=0 AND state='completed' "
            "ORDER BY updated DESC,id DESC LIMIT 201"
        ).fetchall()
        for row in rows:
            value = _get(db, row[0])
            frozen, journal = value["frozen"], value["journal"]
            if (
                frozen.get("model_plan") == model_plan
                and frozen.get("runtime_id") == identity["runtime_id"]
                and frozen.get("recipe_sha256") == identity["recipe_sha256"]
                and _target(frozen) == _target(identity)
                and journal.get("prepared") is True
                and journal.get("doctor_verified") is True
                and journal.get("model_plan_receipt")
            ):
                receipt = journal["model_plan_receipt"]
                _validate_model_receipt(receipt, frozen)
                break
        if receipt is None and len(rows) > 200:
            raise api.RuntimePreflightBlocked("runtime-readiness-unknown")
    return (
        receipt is not None and _model_evidence(client, settings, model_plan) == receipt["evidence"]
    )


def _validate_model_receipt(receipt, frozen):
    if (
        "model_plan" not in frozen
        or type(receipt) is not dict
        or set(receipt)
        != {"format", "model_plan_sha256", "runtime_id", "recipe_sha256", "evidence"}
        or receipt["format"] != MODEL_RECEIPT
        or receipt["model_plan_sha256"] != runtime_store._digest(frozen["model_plan"])
        or receipt["runtime_id"] != frozen["runtime_id"]
        or receipt["recipe_sha256"] != frozen["recipe_sha256"]
        or type(receipt["evidence"]) is not dict
        or set(receipt["evidence"])
        != {"snapshots", "speakers", "speaker_marker", "punkt", "conversions"}
        or len(runtime_store.canonical(receipt).encode()) > 48 * 1024
    ):
        raise Conflict("The Runtime model preparation receipt is invalid. Preserve its evidence.")
    evidence, plan = receipt["evidence"], frozen["model_plan"]
    if (
        type(evidence["snapshots"]) is not dict
        or set(evidence["snapshots"]) != set(plan["models"]) - {"speakers"}
        or any(not _valid_inventory(items) for items in evidence["snapshots"].values())
        or evidence["speaker_marker"] is not True
        or type(evidence["speakers"]) is not list
        or len(evidence["speakers"]) != len(plan["speaker_files"])
        or type(evidence["punkt"]) is not dict
        or set(evidence["punkt"]) != {"danish", "english"}
        or any(not _valid_inventory(items) for items in evidence["punkt"].values())
        or type(evidence["conversions"]) is not dict
        or set(evidence["conversions"]) != set(plan["models"]) & api.DANISH_CHOICES
        or any(
            type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None
            for value in evidence["conversions"].values()
        )
    ):
        raise Conflict("The Runtime model inventory receipt is invalid. Preserve its evidence.")
    names = set()
    for item in evidence["speakers"]:
        if (
            type(item) is not dict
            or set(item) != {"name", "kind", "bytes"}
            or type(item["name"]) is not str
            or item["name"] not in plan["speaker_files"]
            or item["name"] in names
            or item["kind"] not in ("FILE", "SOFT_LINK")
            or type(item["bytes"]) is not int
            or item["bytes"] != plan["speaker_files"][item["name"]][0]
        ):
            raise Conflict("The Runtime speaker receipt is invalid. Preserve its evidence.")
        names.add(item["name"])
    for items in evidence["punkt"].values():
        if not {
            "collocations.tab",
            "sent_starters.txt",
            "abbrev_types.txt",
            "ortho_context.tab",
        } <= {item["name"] for item in items if item["kind"] != "DIRECTORY"}:
            raise Conflict("The Runtime tokenizer receipt is invalid. Preserve its evidence.")


def _valid_inventory(items):
    if type(items) is not list or not 1 <= len(items) <= 64:
        return False
    names = set()
    for item in items:
        if (
            type(item) is not dict
            or set(item) != {"name", "kind", "bytes"}
            or type(item["name"]) is not str
            or not 1 <= len(item["name"]) <= 256
            or "/" in item["name"]
            or "\\" in item["name"]
            or item["name"] in {".", ".."}
            or item["name"] in names
            or item["kind"] not in ("FILE", "SOFT_LINK", "DIRECTORY")
            or (
                item["bytes"] is not None
                if item["kind"] == "DIRECTORY"
                else type(item["bytes"]) is not int or not 0 <= item["bytes"] < 2**50
            )
        ):
            return False
        names.add(item["name"])
    return items == sorted(items, key=lambda item: item["name"]) and any(
        item["kind"] != "DIRECTORY" and item["bytes"] > 0 for item in items
    )


def _same(left, right):
    """Type-strict, recursive equality of JSON values: their exact canonical stored bytes.

    Python ``==`` treats ``False == 0`` and ``1 == 1.0`` as equal; stored proof does not.
    Values that cannot be stored canonically are never the same as anything.
    """
    if left is _ABSENT or right is _ABSENT:
        return left is right
    try:
        return runtime_store.canonical(left) == runtime_store.canonical(right)
    except (ValueError, RecursionError):
        return False


def _birth(db, value, fields, journal, trusted):
    """Anchor the stored birth identity, never the caller's possibly stale/stripped copy."""
    if _IMMUTABLE_COLUMNS & set(fields):
        raise Conflict("A preparation's frozen identity is immutable. Preserve its evidence.")
    stored = _raw(db, value["id"])
    try:
        frozen, before = json.loads(stored["frozen"]), json.loads(stored["journal"])
        born = "generation" in frozen
    except (ValueError, TypeError, RecursionError):
        raise Conflict("Preparation metadata is unverified. Preserve its evidence.") from None
    after = {**value["journal"], **journal} if journal is not None else before
    if not born:
        if "generation" in value["frozen"] or GENERATION_ONLY & set(after):
            raise Conflict("A legacy preparation cannot acquire Runtime generation evidence.")
        return
    completes = fields.get("state") == "completed" and stored["state"] != "completed"
    # Type-strict: a re-typed value (False->0, 1->1.0) is a change to stored proof.
    changed = any(
        not _same(after.get(key, _ABSENT), before.get(key, _ABSENT)) for key in GENERATION_RESERVED
    )
    if not _same(value["frozen"], frozen) or (not trusted and (completes or changed)):
        raise Conflict("Runtime generation proof can change only through its own verified writers.")


def _update(db, value, *, journal=None, _generation_writer=False, **fields):
    _owned(value)
    _birth(db, value, fields, journal, _generation_writer)
    if journal is not None:
        value["journal"].update(journal)
        fields["journal"] = runtime_store.canonical(value["journal"])
    fields["updated"] = now()
    db.execute(
        "UPDATE runtime_preparations SET "
        + ",".join(key + "=?" for key in fields)
        + " WHERE id=? AND epoch=? AND archived=0",
        (*fields.values(), value["id"], value["epoch"]),
    )
    return _get(db, value["id"])


def checkpoint(store, identifier, epoch, *, journal=None, **fields):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        value = _get(db, identifier, owned=True)
        if value["epoch"] != epoch:
            raise Interrupted()
        if value["state"] in {"completed", "cancelled"} and fields.get("state") != value["state"]:
            fields = {
                key: item
                for key, item in fields.items()
                if key not in {"state", "message", "progress"}
            }
        if (
            journal
            and value["journal"].get("remote_state") in api.TERMINAL
            and journal.get("remote_state") not in api.TERMINAL
        ):
            journal = {key: item for key, item in journal.items() if key != "remote_state"}
        return _update(db, value, journal=journal, **fields)


def claim(store):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute(
            "SELECT * FROM runtime_preparations WHERE state='queued' AND archived=0 "
            "ORDER BY created,id"
        ).fetchall():
            with _CONTROL_LOCK:
                if row["id"] in _CONTROLS:
                    continue
            try:
                value = _get(db, row["id"])
            except Conflict:
                _quarantine(db, row)
                continue
            return _update(db, value, state="running", epoch=value["epoch"] + 1)
        return None


def recover(store):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for (identifier,) in db.execute(
            "SELECT id FROM runtime_preparations WHERE state='running' AND archived=0"
        ).fetchall():
            try:
                value = _get(db, identifier)
            except Conflict:
                _quarantine(db, _raw(db, identifier))
                continue
            # Only observation of a recorded allocation resumes automatically. Even a
            # pre-POST interruption needs another explicit Retry before a paid submission.
            state = "queued" if value["journal"].get("job_id") else "interrupted"
            _update(
                db,
                value,
                state=state,
                epoch=value["epoch"] + 1,
                message="Preparation was interrupted; its allocation evidence was retained.",
            )


def begin_submission(store, identifier, epoch, *, require=None):
    """Accept one POST. A generation owner's ``require`` proves its receipts in this CAS."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        value = _get(db, identifier)
        _owned(value)
        if value["epoch"] != epoch or value["state"] != "running":
            raise Interrupted()
        journal = value["journal"]
        if journal.get("stop_intent"):
            raise Interrupted()
        if journal.get("job_id") or runtime_store._uncertain(journal):
            raise Conflict("Reconcile the recorded allocation before another submission.")
        if ("generation" in value["frozen"]) != (require is not None):
            raise Conflict("Runtime generation submission needs its own verified receipts.")
        receipt = require(value) if require is not None else {}
        for row in db.execute(
            "SELECT id FROM runtime_preparations WHERE archived=0 AND id!=?", (identifier,)
        ):
            try:
                other = _get(db, row[0])
            except Conflict:
                raise Conflict(
                    "Resolve unverified preparation evidence before another allocation."
                ) from None
            if _target(other["frozen"]) == _target(value["frozen"]) and runtime_store._reserved(
                other["journal"]
            ):
                raise Conflict("Another preparation's allocation for this runtime is unresolved.")
        if runtime_store._capacity(db) >= runtime_store._settings(db)["max_allocations"]:
            return False
        _update(
            db,
            value,
            journal={
                "capacity_reserved": True,
                "submission_intent": True,
                "submission_attempted_at": now(),
                "reconciled_not_created": False,
                "no_post": False,
                **receipt,
            },
            _generation_writer=require is not None,
        )
        return True


def _release(journal):
    if journal.get("remote_state") in api.TERMINAL or (
        not journal.get("job_id") and not runtime_store._uncertain(journal)
    ):
        return {"capacity_reserved": False, "capacity_released_at": now()}
    return {}


def _generation_retry(value):
    """ADR 0176 Stage 2: only known-ID observation of the same owner, never a successor."""
    journal = value["journal"]
    return bool(
        journal.get("job_id")
        and not journal.get("stop_intent")
        and (
            journal.get("remote_state") not in api.TERMINAL or journal["remote_state"] == "SUCCESS"
        )
    )


def card(value):
    journal, archived = value["journal"], bool(value["archived"])
    uncertain = runtime_store._uncertain(journal)
    remote_open = bool(journal.get("job_id") and journal.get("remote_state") not in api.TERMINAL)
    active = not archived and value["state"] in {"queued", "running"}
    pending = unresolved(value)
    generation = "generation" in value["frozen"]
    return {
        "id": PREFIX + value["id"],
        "runtime_preparation_id": value["id"],
        "kind": "runtime-preparation",
        "recording_id": None,
        "project_id": None,
        "recording_name": "UCloud runtime preparation",
        "members": [],
        **{key: value[key] for key in ("state", "progress", "message", "created", "updated")},
        "archived": archived,
        "runtime_id": value["frozen"]["runtime_id"],
        "run_name": journal["run_name"],
        "can_cancel": not archived and (remote_open or (active and not journal.get("stop_intent"))),
        "can_retry": not archived
        and not active
        and not uncertain
        and value["state"] != "completed"
        and (not generation or _generation_retry(value)),
        # Name-based reconciliation never resolves a generation owner in this slice.
        "can_reconcile": not archived and not active and uncertain and not generation,
        "can_cleanup": not archived
        and not active
        and not uncertain
        and not remote_open
        and journal.get("cleanup_state") == "pending",
        "can_remove": not active and not pending,
        "needs_attention": not archived
        and (pending or value["state"] in {"failed", "interrupted"}),
        "ucloud_job_id": journal.get("job_id"),
        "ucloud_job_url": (
            value["frozen"]["settings"]["base_url"].rstrip("/")
            + "/app/jobs/properties/"
            + quote(journal["job_id"], safe="")
            if journal.get("job_id") and not archived
            else None
        ),
        "ucloud_state": journal.get("remote_state"),
        "ucloud_submission_uncertain": uncertain,
        "ucloud_cleanup_state": journal.get("cleanup_state"),
        "ucloud_stop_pending": bool(journal.get("stop_intent") and remote_open),
    }


def activity(store):
    from .jobs import activity_cleared_at

    cleared = activity_cleared_at(store)
    with store.connection() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='runtime_preparations'"
        ).fetchone():
            return []
        cards = []
        for index, row in enumerate(
            db.execute("SELECT * FROM runtime_preparations ORDER BY updated DESC,id DESC")
        ):
            raw = dict(row)
            if raw["archived"]:
                item, retained = _inert_card(raw), False
            else:
                try:
                    value = _decode(raw)
                    item, retained = card(value), unresolved(value)
                except Conflict:
                    item, retained = _inert_card(raw), True
            if retained or (
                index < RECENT_ACTIVITY
                and (
                    raw["hidden_updated"] != raw["updated"]
                    and (not cleared or raw["updated"] > cleared)
                )
            ):
                cards.append(item)
    return cards


def _inert_card(raw):
    """Do not decode historical or unverified proof just to render local Activity."""
    archived = bool(raw["archived"])
    return {
        "id": PREFIX + raw["id"],
        "runtime_preparation_id": raw["id"],
        "kind": "runtime-preparation",
        "recording_id": None,
        "project_id": None,
        "recording_name": "UCloud runtime preparation",
        "members": [],
        "state": "archived" if archived else "failed",
        "progress": 0,
        "message": (
            "Historical preparation; UCloud controls are disabled."
            if archived
            else "Preparation metadata is unverified. "
            "Preserve its evidence before further controls."
        ),
        "created": raw["created"],
        "updated": raw["updated"],
        "archived": archived,
        "needs_attention": not archived,
        "can_remove": archived,
        "can_cancel": False,
        "can_retry": False,
        "can_reconcile": False,
        "can_cleanup": False,
        "run_name": None,
        "runtime_id": None,
        "ucloud_job_id": None,
        "ucloud_job_url": None,
        "ucloud_state": None,
        "ucloud_submission_uncertain": False,
        "ucloud_cleanup_state": None,
        "preparation_unverified": not archived,
    }


def cancel(store, credentials, identifier):
    # Borrow the serving Store's lease; never re-enter its RLock from an HTTP/queue worker.
    with nullcontext() if store._lease_depth else store.library_lock(), _control(identifier):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            value = _get(db, identifier, owned=True)
            if not card(value)["can_cancel"]:
                if value["journal"].get("stop_intent"):
                    return card(value)
                raise Conflict("This preparation is no longer active. Refresh Activity.")
            journal = value["journal"]
            remote_open = bool(
                journal.get("job_id") and journal.get("remote_state") not in api.TERMINAL
            )
            state = (
                "queued"
                if remote_open
                else value["state"]
                if value["state"] == "running"
                else "cancelled"
            )
            value = _update(
                db,
                value,
                state=state,
                journal={"stop_intent": True},
                message="Stopping this preparation; remote confirmation may be pending…",
            )
        if not value["journal"].get("job_id"):
            return card(value)
        try:
            with _client(credentials, value) as (client, _):
                value = _observe_stop(
                    store,
                    client,
                    identifier,
                    value["epoch"],
                    value["journal"]["job_id"],
                    explicit=True,
                )
        except Exception:
            # A failed or lost control reply does not prove termination or a free slot.
            value = get(store, identifier, owned=True)
            if value["journal"].get("remote_state") not in api.TERMINAL:
                value = checkpoint(
                    store,
                    identifier,
                    value["epoch"],
                    message="Stop is pending. Check this recorded allocation again in Activity.",
                )
        return card(value)


def _observe_stop(store, client, identifier, epoch, remote_id, *, explicit=False):
    """Caller owns the same-owner control slot. Observe/retry only this recorded allocation."""
    value = get(store, identifier, owned=True)
    if value["epoch"] != epoch or value["journal"].get("job_id") != remote_id:
        raise Interrupted()
    journal = value["journal"]
    if not journal.get("stop_intent"):
        raise Conflict("This preparation has no Stop intent.")
    if journal.get("remote_state") in api.TERMINAL:
        return checkpoint(
            store,
            identifier,
            epoch,
            state="cancelled",
            journal=_release(journal),
            message="Preparation stopped; its recorded allocation has ended.",
        )
    state = api.remote_state(client, remote_id)
    value = checkpoint(
        store,
        identifier,
        epoch,
        journal={"remote_state": state, **_release({**journal, "remote_state": state})},
    )
    state = value["journal"]["remote_state"]
    if (
        state not in api.TERMINAL
        and not journal.get("termination_requested")
        and (not journal.get("termination_intent") or explicit)
    ):
        attempts = journal.get("termination_attempts", 0)
        if type(attempts) is not int or attempts < 0:
            raise Conflict("The preparation's Stop receipt is unverified; preserve its evidence.")
        value = checkpoint(
            store,
            identifier,
            epoch,
            journal={
                "termination_intent": True,
                "termination_job_id": remote_id,
                "termination_epoch": epoch,
                "termination_attempts": attempts + 1,
                "termination_response": "unknown",
                "termination_attempted_at": now(),
                "termination_uncertain_seen": bool(
                    journal.get("termination_uncertain_seen")
                    or (
                        journal.get("termination_intent")
                        and journal.get("termination_response") == "unknown"
                    )
                ),
            },
        )
        native.cloud_call(client.terminate_job, remote_id)
        value = checkpoint(
            store,
            identifier,
            epoch,
            journal={"termination_requested": True, "termination_response": "accepted"},
        )
        state = api.remote_state(client, remote_id)
    if state in api.TERMINAL:
        return checkpoint(
            store,
            identifier,
            epoch,
            state="cancelled",
            journal={
                "remote_state": state,
                **_release({**value["journal"], "remote_state": state}),
            },
            message="Preparation stopped; its recorded allocation has ended.",
        )
    return checkpoint(
        store, identifier, epoch, message="Stop requested; UCloud terminal confirmation is pending."
    )


def retry(store, credentials, identifier):
    with _control(identifier):
        value = get(store, identifier, owned=True)
        _owned(value)
        if not card(value)["can_retry"]:
            raise Conflict("Resolve this preparation's allocation before retrying.")
        if (
            value["journal"].get("job_id")
            and value["journal"].get("remote_state") not in api.TERMINAL
        ):
            return card(
                checkpoint(
                    store,
                    identifier,
                    value["epoch"],
                    state="queued",
                    message="Reconnecting to the same preparation allocation…",
                )
            )
        if "generation" in value["frozen"]:
            # can_retry admitted only a recorded SUCCESS awaiting verification. Observe the
            # same owner/allocation again; never create a successor or adopt a folder.
            return card(
                checkpoint(
                    store,
                    identifier,
                    value["epoch"],
                    state="queued",
                    message="Verifying the same Runtime generation allocation again…",
                )
            )
        _current(
            store,
            credentials,
            value["connection_fingerprint"],
            model_plan=value["frozen"].get("model_plan", _ABSENT),
        )
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            fresh = _get(db, identifier)
            if fresh != value:
                raise Conflict("Preparation changed. Refresh Activity before retrying.")
            existing = db.execute(
                "SELECT id FROM runtime_preparations WHERE archived=0 AND "
                "json_extract(CASE WHEN json_valid(frozen) THEN frozen END,'$.retry_of')=? "
                "ORDER BY created DESC LIMIT 1",
                (identifier,),
            ).fetchone()
            if not existing:
                for row in db.execute("SELECT id FROM runtime_preparations WHERE archived=0"):
                    try:
                        _get(db, row[0])
                    except Conflict:
                        raise Conflict(
                            "Resolve unverified preparation evidence before another allocation."
                        ) from None
            successor = (
                _update(db, _get(db, existing[0]))
                if existing
                else _insert(
                    db, value["frozen"], value["connection_fingerprint"], retry_of=identifier
                )
            )
        return card(successor)


@contextmanager
def _client(credentials, value):
    _owned(value)
    settings = providers.Settings.model_validate(value["frozen"]["settings"])
    key = credentials.get(providers.credential_scope(settings))
    native.require_setup(settings, key)
    with native.client_for(settings, key) as client:
        yield client, settings


def reconcile(store, credentials, identifier, *, confirm_not_created=False, fingerprint=None):
    with _control(identifier):
        value = get(store, identifier, owned=True)
        if "generation" in value["frozen"]:
            raise Conflict(
                "Runtime generation allocations are never reconciled by name. "
                "Their evidence is retained for explicit recovery."
            )
        if not card(value)["can_reconcile"]:
            raise Conflict("Only an inactive uncertain preparation can be reconciled.")
        matches, cursor = [], None
        with _client(credentials, value) as (client, _):
            for _ in range(100):
                page = native.cloud_call(client.browse_jobs, items_per_page=100, next_page=cursor)
                if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                    raise Conflict(
                        "UCloud job discovery was incomplete; allocation remains uncertain."
                    )
                matches += [
                    item
                    for item in page["items"]
                    if isinstance(item, dict)
                    and item.get("specification", {}).get("name") == value["journal"]["run_name"]
                ]
                cursor = page.get("next")
                if not cursor:
                    break
            else:
                raise Conflict("Job discovery exceeded its bound; allocation remains uncertain.")
            if len(matches) > 1:
                raise Conflict("Several allocations match this preparation; keep it unresolved.")
            expected = value["journal"].get("specification")
            if matches:
                remote = native.cloud_call(client.retrieve_job, matches[0]["id"])
                if not expected or remote.get("specification") != expected:
                    raise Conflict("The discovered allocation does not match this preparation.")
                update = {
                    "job_id": str(remote["id"]),
                    "remote_state": remote.get("status", {}).get("state"),
                }
                status = "found"
            else:
                observed = runtime_store._digest(
                    {"id": identifier, "epoch": value["epoch"], "journal": value["journal"]}
                )
                if not confirm_not_created:
                    return {
                        "status": "not-found",
                        "requires_confirmation": True,
                        "fingerprint": observed,
                        "operation": card(value),
                    }
                if fingerprint != observed:
                    raise Conflict("Absence confirmation is stale. Reconcile again.")
                update = {"reconciled_not_created": True, "no_post": True}
                status = "not-found"
        update.update(_release({**value["journal"], **update}))
        fresh = checkpoint(
            store,
            identifier,
            value["epoch"],
            journal=update,
            **({"state": "queued"} if status == "found" else {}),
        )
        return {"status": status, "requires_confirmation": False, "operation": card(fresh)}


def cleanup(store, credentials, identifier):
    with _control(identifier):
        value = get(store, identifier, owned=True)
        _owned(value)
        if not card(value)["can_cleanup"]:
            raise Conflict("Confirm this preparation's terminal state before cleanup.")
        with _client(credentials, value) as (client, settings):
            remote = api.run_folder(settings, identifier)
            if value["journal"].get("owned_folder") != remote:
                raise Conflict("This preparation has no exact owned run-folder receipt.")
            # The owner never publishes private inputs. Shared runtime/model roots are not
            # accepted here. Keep the obligation if terminal status or Trash cannot be proved.
            if value["journal"].get("job_id"):
                state = api.remote_state(client, value["journal"]["job_id"])
                if state not in api.TERMINAL:
                    raise Conflict("The preparation allocation is still active.")
            native.trash_owned_folder(client, remote)
        return card(
            checkpoint(store, identifier, value["epoch"], journal={"cleanup_state": "trashed"})
        )


def hide(store, identifier):
    with _control(identifier), store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        raw = _raw(db, identifier)
        if raw["archived"]:
            db.execute(
                "UPDATE runtime_preparations SET hidden_updated=updated WHERE id=?", (identifier,)
            )
            return {"hidden": True}
        value = _get(db, identifier, owned=True)
        if not card(value)["can_remove"]:
            raise Conflict("Unresolved preparation stays in Activity.")
        db.execute(
            "UPDATE runtime_preparations SET hidden_updated=updated WHERE id=?", (identifier,)
        )
    return {"hidden": True}


class Run:
    def __init__(self, worker, value):
        self.worker, self.store, self.value = worker, worker.store, value
        self.identifier, self.epoch = value["id"], value["epoch"]

    def checkpoint(self, **fields):
        self.value = checkpoint(self.store, self.identifier, self.epoch, **fields)
        return self.value

    def guard(self, *, submitting=False):
        if self.worker.stopping.is_set():
            raise Interrupted()
        fresh = get(self.store, self.identifier)
        _owned(fresh)
        if fresh["epoch"] != self.epoch:
            raise Interrupted()
        if fresh["journal"].get("stop_intent"):
            raise Interrupted()
        if submitting:
            _current(
                self.store,
                self.worker.credentials,
                fresh["connection_fingerprint"],
                model_plan=fresh["frozen"].get("model_plan", _ABSENT),
            )

    def execute(self):
        if "generation" in self.value["frozen"]:
            # ADR 0176: a generation owner branches before every legacy-root idle/stamp
            # shortcut or completion helper. Its own module refuses while disabled.
            from . import runtime_generation_preparation

            return runtime_generation_preparation.GenerationRun(self.worker, self.value).execute()
        return self._execute()

    def _legacy_only(self):
        if "generation" in self.value["frozen"]:
            raise Conflict("A Runtime generation owner never uses legacy runtime helpers.")

    def _execute(self):
        try:
            with _client(self.worker.credentials, self.value) as (self.client, self.settings):
                if not self.value["journal"].get("job_id"):
                    if (
                        self.value["frozen"].get("runtime_id") != api.runtime_id()
                        or self.value["frozen"].get("recipe_sha256") != recipe_hash()
                    ):
                        raise Conflict("The runtime recipe changed. Start a new preparation.")
                    if self.value["journal"].get("stop_intent"):
                        raise Interrupted()
                    self.submit()
                if self.value["state"] != "completed":
                    self.poll()
        except Interrupted:
            fresh = get(self.store, self.identifier)
            if fresh["epoch"] != self.epoch:
                return
            if fresh["state"] in {"completed", "cancelled"}:
                return
            stopping = self.worker.stopping.is_set()
            self.checkpoint(
                state="interrupted" if stopping else "cancelled",
                journal=_release(fresh["journal"]),
                message="Preparation stopped locally; recorded remote obligations are retained.",
            )
        except api.RuntimePreflightBlocked as exc:
            fresh = get(self.store, self.identifier)
            if fresh["state"] not in {"completed", "cancelled"}:
                self.checkpoint(
                    state="failed", message=str(exc), journal={"preflight_failure": exc.code}
                )
        except observation.Error as exc:
            fresh = get(self.store, self.identifier)
            if fresh["epoch"] == self.epoch and fresh["state"] not in {"completed", "cancelled"}:
                self.checkpoint(state="failed", message=str(exc))
        except Exception:
            fresh = get(self.store, self.identifier)
            if fresh["state"] in {"completed", "cancelled"}:
                return
            self.checkpoint(
                state="failed",
                message=(
                    "UCloud preparation failed. Its allocation evidence was kept; inspect Activity "
                    "and reconcile an uncertain submission before retrying."
                ),
            )

    def submit(self):
        self._legacy_only()
        self.guard(submitting=True)
        if runtime_store._uncertain(self.value["journal"]):
            raise Conflict("Submission outcome is uncertain; reconcile it before retrying.")
        plan = self.value["frozen"].get("model_plan", _ABSENT)
        ready = (
            api.stamped(self.client, self.settings)
            if plan is _ABSENT
            else _model_prepared(self.store, self.client, self.settings, plan, self.value["frozen"])
        )
        if ready:
            self.checkpoint(
                state="completed",
                progress=1,
                message="The base UCloud runtime is already prepared."
                if plan is _ABSENT
                else "The configured UCloud models are already prepared.",
            )
            return
        from . import runtime_batches

        runtime_batches.require_preparation_idle(self.store, self.settings, self.client)
        files_to_seed = None
        if plan is not _ABSENT:
            # Accepted task authority is the public manifest, never the parent's token.
            # Missing/drifted files fail; dispatch cannot fetch using another credential.
            files_to_seed = api.local_speaker_files()
            if {relative for _, relative in files_to_seed} != set(plan["speaker_files"]) or any(
                not api.verified_file(path, *plan["speaker_files"][relative])
                for path, relative in files_to_seed
            ):
                raise Conflict(
                    "The accepted speaker files changed. Save Hugging Face settings again."
                )
        compute = runtime_compute.freeze(self.settings, self.worker.credentials)
        product = runtime_compute.frozen_product(compute)
        if product != runtime_compute.MIG_PRODUCT:
            raise Conflict("Preparation requires the exact funded CBS Runtime MIG identity.")
        application = api.script_application(self.client)
        # The existing specification builder consumes only the allocation duration here;
        # no recording-level ASR/alignment request is manufactured.
        options = SimpleNamespace(timeout_minutes=1)
        specification = api.build_script_specification(
            self.settings,
            application,
            self.identifier,
            options,
            True,
            runtime_product=product,
        )
        specification["name"] = self.value["journal"]["run_name"]
        self.checkpoint(journal={"specification": specification, **compute})
        self.guard(submitting=True)
        api.ensure_folder(
            self.client, self.settings.ucloud_transcriber.storage_path + "/cbs-runtime"
        )
        api.ensure_folder(self.client, api.runtime_folder(self.settings))
        base = self.store.root / "runtime-preparations"
        base.mkdir(exist_ok=True)
        if base.is_symlink() or base.resolve() != self.store.root / "runtime-preparations":
            raise Conflict("The local preparation staging folder is not owned by this library.")
        folder = base / self.identifier
        folder.mkdir()  # New owner, new staging folder; never adopt or overwrite a collision.
        runtime_batches.ensure_use_lock(
            self.client, self.settings, folder, lambda: self.guard(submitting=True)
        )
        remote = api.run_folder(self.settings, self.identifier)
        self.guard(submitting=True)
        self.client.create_folder(remote)  # conflicts never establish ownership
        self.checkpoint(journal={"owned_folder": remote, "cleanup_state": "pending"})
        if files_to_seed is not None:
            self.guard(submitting=True)
            api.seed_speakers(
                self.client,
                self.settings.base_url,
                self.settings,
                files_to_seed,
                lambda: self.guard(submitting=True),
                folder / "speakers-complete.txt",
            )
        files = [(path, path.name) for path in api.WORKER_FILES]
        supervisor = folder / "supervisor.sh"
        supervisor.write_text(
            api.supervisor_script(
                self.settings,
                self.identifier,
                True,
                () if plan is _ABSENT else [name for name in plan["models"] if name != "speakers"],
                prepare_only=True,
            ),
            encoding="utf-8",
            newline="\n",
        )
        requirements = folder / "requirements.txt"
        requirements.write_bytes(api.requirements_bytes())
        files += [(supervisor, "supervisor.sh"), (requirements, "requirements.txt")]
        self.checkpoint(message="Uploading public runtime preparation scripts…", progress=0.1)
        for path, name in files:
            self.guard(submitting=True)
            native.upload(
                self.client,
                self.settings.base_url,
                path,
                remote + "/" + name,
                lambda: self.guard(submitting=True),
                lambda **_: None,
            )
        self.checkpoint(journal={"scripts_uploaded": True})
        while True:
            self.guard(submitting=True)
            if begin_submission(self.store, self.identifier, self.epoch):
                self.checkpoint(journal={"deadline_at": time.time() + api.PREPARE_SECONDS + 300})
                # Committing intent accepts this one request. Stop can record its intent
                # during the call; drain the reply and then stop only that recorded ID.
                remote_id = api.extract_job_id(
                    native.cloud_call(self.client.submit_job, specification)
                )
                self.checkpoint(journal={"job_id": remote_id})
                return
            self.checkpoint(
                message="Waiting for a free UCloud runtime allocation slot…", progress=0.15
            )
            self.worker.stopping.wait(api.POLL_SECONDS)

    def _wait(self, delay):
        self.worker.stopping.wait(max(0, delay))

    def _poll_owner(self):
        if self.worker.stopping.is_set():
            raise Interrupted()
        fresh = get(self.store, self.identifier, owned=True)
        if fresh["epoch"] != self.epoch or fresh["journal"].get("job_id") != self.value[
            "journal"
        ].get("job_id"):
            raise Interrupted()
        return fresh

    def _verification(self, remote, deadline):
        self._legacy_only()
        if self.value["frozen"].get("runtime_id") != api.runtime_id():
            raise observation.Refused()
        doctor = observation.read_json(
            self.client, self.settings, remote + "/doctor.json", deadline=deadline
        )
        if doctor.get("ready") is not True or doctor.get("version") != api.WHISPERX_VERSION:
            raise observation.Refused()
        runtime_compute.require_gpu_memory(doctor)
        observation.stamp(self.client, self.settings, deadline=deadline)
        verified = {"prepared": True, "doctor_verified": True}
        if "model_plan" in self.value["frozen"]:
            plan = self.value["frozen"]["model_plan"]
            cache = observation.read_json(
                self.client, self.settings, remote + "/cache.json", deadline=deadline
            )
            models = cache.get("models", {})
            if (
                type(models) is not dict
                or any(models.get(key) is not True for key in plan["models"])
                or cache.get("punkt_tab") is not True
            ):
                raise observation.Refused()
            evidence = _model_evidence(
                self.client, self.settings, plan, strict=True, deadline=deadline
            )
            if evidence is None:
                raise observation.Refused()
            verified["model_plan_receipt"] = {
                "format": MODEL_RECEIPT,
                "model_plan_sha256": runtime_store._digest(plan),
                "runtime_id": self.value["frozen"]["runtime_id"],
                "recipe_sha256": self.value["frozen"]["recipe_sha256"],
                "evidence": evidence,
            }
            _validate_model_receipt(verified["model_plan_receipt"], self.value["frozen"])
        return verified

    def _settle_success(self, remote, deadline):
        """Legacy completion; a generation owner overrides this with its sole promotion."""
        return self._complete_prepared(self._verification(remote, deadline), deadline)

    def _complete_prepared(self, verified, deadline):
        # BEGIN may wait for another writer. Read Stop/epoch only AFTER ownership of
        # that writer is obtained, so a late remote reply cannot publish Ready.
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            fresh = _get(db, self.identifier, owned=True)
            if "generation" in fresh["frozen"]:
                raise Conflict("A Runtime generation owner is completed only by its promotion.")
            if (
                fresh["epoch"] != self.epoch
                or fresh["journal"].get("job_id") != self.value["journal"].get("job_id")
                or self.worker.stopping.is_set()
            ):
                raise Interrupted()
            if fresh["state"] in {"completed", "cancelled"}:
                self.value = fresh
                return True
            if fresh["journal"].get("stop_intent"):
                return False
            if time.monotonic() >= deadline:
                raise observation.Refused()
            deadline = fresh["journal"].get("deadline_at")
            if type(deadline) in (int, float) and time.time() > deadline:
                self.value = _update(db, fresh, journal={"stop_intent": True})
                return False
            if fresh["journal"].get("remote_state") != "SUCCESS":
                raise observation.Refused()
            self.value = _update(
                db,
                fresh,
                state="completed",
                progress=1,
                journal=verified,
                message=(
                    "Base UCloud runtime prepared and CUDA checked; "
                    if "model_plan" not in fresh["frozen"]
                    else "Configured UCloud models prepared and caches checked; "
                )
                + "public run files await cleanup.",
            )
            return True

    def poll(self):
        if self.value["state"] == "completed":
            return
        remote = api.run_folder(self.settings, self.identifier)
        transient, pending, terminal_at = 0, 0, None
        while True:
            fresh = self._poll_owner()
            run, delay = fresh["journal"], api.POLL_SECONDS
            deadline = run.get("deadline_at")
            if type(deadline) in (int, float) and time.time() > deadline:
                fresh = self.checkpoint(
                    journal={"stop_intent": True},
                    message="Preparation exceeded its time limit; stopping its allocation…",
                )
                run = fresh["journal"]
            try:
                if run.get("stop_intent"):
                    with _control(self.identifier):
                        self.value = _observe_stop(
                            self.store, self.client, self.identifier, self.epoch, run["job_id"]
                        )
                    if self.value["state"] == "cancelled":
                        return
                else:
                    state = observation.state(
                        self.client,
                        run["job_id"],
                        run.get("specification"),
                        deadline=None
                        if terminal_at is None
                        else terminal_at + TERMINAL_SETTLE_SECONDS,
                    )
                    if run.get("remote_state") in api.TERMINAL and state != run["remote_state"]:
                        raise observation.Refused()
                    self.checkpoint(
                        journal={"remote_state": state, **_release({**run, "remote_state": state})}
                    )
                    if state == "SUCCESS" and terminal_at is None:
                        terminal_at = time.monotonic()
                    if state in api.TERMINAL and state != "SUCCESS":
                        raise observation.Refused()
                    status = observation.read_json(
                        self.client,
                        self.settings,
                        remote + "/supervisor.json",
                        deadline=None
                        if terminal_at is None
                        else terminal_at + TERMINAL_SETTLE_SECONDS,
                    )
                    phase = status.get("phase")
                    if status.get("generation") != self.identifier or phase not in {
                        "started",
                        "preparing",
                        "ready",
                        "running",
                        "finished",
                        "failed",
                    }:
                        raise observation.Refused()
                    if phase == "failed":
                        code = status.get("code")
                        if isinstance(code, str) and code in {
                            "runtime-busy",
                            "runtime-prepare",
                            "runtime",
                        }:
                            self.checkpoint(state="failed", message=api.FAILURES[code])
                            return
                        raise observation.Refused()
                    if state == "SUCCESS":
                        if phase != "finished":
                            raise observation.Pending()
                        settle_deadline = terminal_at + TERMINAL_SETTLE_SECONDS
                        if self._settle_success(remote, settle_deadline):
                            return
                        continue  # Durable Stop won; use its actual control path.
                    self.checkpoint(
                        message="Preparing the UCloud runtime (20+ minutes can be needed)…"
                        if phase == "preparing"
                        else "Waiting for UCloud preparation to finish…",
                        progress=0.5 if phase == "preparing" else 0.25,
                    )
                transient = 0
            except ControlBusy:
                pass
            except (observation.Error, api.Transient) as exc:
                current = self._poll_owner()
                if current["journal"].get("stop_intent") and not run.get("stop_intent"):
                    continue
                if isinstance(exc, (observation.Refused, api.RuntimePreflightBlocked)):
                    raise
                if isinstance(exc, observation.Pending):
                    if terminal_at is not None:
                        pending += 1
                        if pending > TERMINAL_SETTLE_ATTEMPTS:
                            raise observation.Refused() from None
                    self.checkpoint(message=str(exc))
                else:
                    transient += 1
                    if transient > OBSERVATION_ATTEMPTS:
                        raise observation.Refused() from None
                    self.checkpoint(
                        message="UCloud connection interrupted; reconnecting to the same "
                        "preparation allocation…"
                    )
                delay = OBSERVATION_BACKOFF_SECONDS
            if terminal_at is not None:
                remaining = TERMINAL_SETTLE_SECONDS - (time.monotonic() - terminal_at)
                if remaining <= 0:
                    raise observation.Refused()
                delay = min(delay, remaining)
            self._wait(delay)

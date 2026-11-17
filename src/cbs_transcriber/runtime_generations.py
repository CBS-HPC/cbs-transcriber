"""Disabled, local-only Runtime generation metadata (ADR 0176, Stages 1-2).

These codecs and candidate reservations establish neither preparation ownership,
readiness nor execution authority. Active-selection capture and remote operations
deliberately do not exist here. The private, separately disabled active-pointer writer is
the registry half of the Stage-2 owner promotion; only
``runtime_generation_preparation.verify_and_promote`` may call it, inside the same writer
transaction as the owner's completion. Callers own their SQLite transaction.
"""

import copy
import hashlib
import json
import re

from . import providers
from .ucloud_native import NativeSetup

SETTING = 11  # 9 is local concurrency; 10 is checked-provider receipts.
REGISTRY_POLICY = "cbs-runtime-generation-registry-v1"
TARGET_POLICY = "cbs-runtime-base-target-v1"
SELECTION_POLICY = "cbs-runtime-selection-v1"
GENERATION_POLICY = "cbs-runtime-generation-v1"
CANDIDATE_RESERVATION_ENABLED = False
# Stage 2: off means an occupied active pointer still refuses exactly as in Stage 1.
ACTIVE_POINTERS_ENABLED = False
MAX_BYTES = 256 * 1024
MAX_TARGETS = 128
_HEX32 = re.compile(r"[a-f0-9]{32}\Z")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_RUNTIME = re.compile(r"whisperx-([A-Za-z0-9_.]+)-(cu128|cpu)-([a-f0-9]{12})\Z")
_ABSENT = object()


class Conflict(ValueError):
    """Unknown, stale or unimplemented generation metadata grants no authority."""


def _refuse():
    raise Conflict("Runtime generation metadata is invalid, unsupported or stale.")


def _keys(value, keys):
    if type(value) is not dict or len(value) != len(keys) or set(value) != keys:
        _refuse()


def _text(value, maximum):
    if (
        type(value) is not str
        or not 0 < len(value) <= maximum
        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        _refuse()
    return value


def _hex(value, pattern):
    if type(value) is not str or not pattern.fullmatch(value):
        _refuse()
    return value


def _revision(value):
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        _refuse()
    return value


def _encoded(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError, RecursionError):
        _refuse()


def validate_compute(value):
    """Validate data shape only; funding, support and eligibility belong to admission."""
    _keys(value, {"product", "device", "flavor"})
    _keys(value["product"], {"id", "category", "provider"})
    for field in value["product"].values():
        _text(field, 200)
    if type(value["device"]) is not str or type(value["flavor"]) is not str:
        _refuse()
    if (value["device"], value["flavor"]) not in {("cpu", "cpu"), ("cuda", "cuda-cu128")}:
        _refuse()
    return copy.deepcopy(value)


def validate_target(value):
    """Check the stable environment target, separately from operation/product bindings."""
    _keys(
        value,
        {
            "policy",
            "server",
            "project_id",
            "storage_path",
            "runtime_id",
            "requirements_sha256",
            "device",
            "flavor",
            "target_sha256",
        },
    )
    if value["policy"] != TARGET_POLICY:
        _refuse()
    server = _text(value["server"], 2000)
    storage = _text(value["storage_path"], 2000)
    try:
        if providers.UCloudSetup.validate_server(server) != server:
            _refuse()
        NativeSetup.safe_path(storage)
    except ValueError:
        _refuse()
    _text(value["project_id"], 200)
    runtime = _RUNTIME.fullmatch(_text(value["runtime_id"], 200))
    digest = _hex(value["requirements_sha256"], _HEX64)
    if (
        runtime is None
        or runtime.group(3) != digest[:12]
        or type(value["device"]) is not str
        or type(value["flavor"]) is not str
        or (value["device"], value["flavor"], runtime.group(2))
        not in {("cpu", "cpu", "cpu"), ("cuda", "cuda-cu128", "cu128")}
    ):
        _refuse()
    content = {key: item for key, item in value.items() if key != "target_sha256"}
    expected = hashlib.sha256(_encoded(content).encode("utf-8")).hexdigest()
    if _hex(value["target_sha256"], _HEX64) != expected:
        _refuse()
    return copy.deepcopy(value)


def base_target(settings, *, runtime_id, requirements_sha256, compute):
    """Describe a base target without checking wallets, files, models or remote readiness."""
    settings = providers.Settings.model_validate(settings)
    choice = validate_compute(compute)
    if settings.provider_type != "ucloud" or settings.ucloud_transcriber is None:
        _refuse()
    setup = settings.ucloud_transcriber
    value = {
        "policy": TARGET_POLICY,
        "server": settings.base_url,
        "project_id": setup.project_id,
        "storage_path": setup.storage_path,
        "runtime_id": runtime_id,
        "requirements_sha256": requirements_sha256,
        "device": choice["device"],
        "flavor": choice["flavor"],
    }
    # The exact product belongs to each operation. Mutable wallet observations do not
    # belong to this stable base-target hash, and this descriptor cannot grant funding.
    value["target_sha256"] = hashlib.sha256(_encoded(value).encode("utf-8")).hexdigest()
    return validate_target(value)


def _matching_compute(value, target):
    choice = validate_compute(value)
    if choice["device"] != target["device"] or choice["flavor"] != target["flavor"]:
        _refuse()
    return choice


def validate_selection(value, *, target=None, compute=None):
    """Validate frozen data only. This is not host capture or active-selection authority."""
    _keys(value, {"policy", "target", "compute", "revision", "generation"})
    if value["policy"] != SELECTION_POLICY:
        _refuse()
    base = validate_target(value["target"])
    choice = _matching_compute(value["compute"], base)
    revision = _revision(value["revision"])
    if target is not None and base != validate_target(target):
        _refuse()
    if compute is not None and choice != validate_compute(compute):
        _refuse()
    selected = value["generation"]
    if selected is not None:
        _keys(selected, {"policy", "token", "owner_id"})
        if selected["policy"] != GENERATION_POLICY or revision == 0:
            _refuse()
        _hex(selected["token"], _HEX32)
        _hex(selected["owner_id"], _HEX32)
    return copy.deepcopy(value)


def runtime_path(settings, *, runtime_id, selection=_ABSENT):
    """Derive a path; absent policy keeps the exact legacy concatenation.

    A derived path grants no permission to create, mount, prepare or use it.
    Present-null/unknown policy refuses instead of becoming legacy.
    """
    settings = providers.Settings.model_validate(settings)
    if settings.provider_type != "ucloud" or settings.ucloud_transcriber is None:
        _refuse()
    if not _RUNTIME.fullmatch(_text(runtime_id, 200)):
        _refuse()
    suffix = ""
    if selection is not _ABSENT:
        value = validate_selection(selection)
        base = value["target"]
        setup = settings.ucloud_transcriber
        if (
            base["server"] != settings.base_url
            or base["project_id"] != setup.project_id
            or base["storage_path"] != setup.storage_path
            or base["runtime_id"] != runtime_id
        ):
            _refuse()
        if value["generation"] is not None:
            suffix = "-g-" + value["generation"]["token"]
    return settings.ucloud_transcriber.storage_path + "/cbs-runtime/" + runtime_id + suffix


def generation_root(settings, target, token):
    """Pure candidate/promoted generation folder; grants no create/mount/use permission."""
    settings = providers.Settings.model_validate(settings)
    base = validate_target(target)
    setup = settings.ucloud_transcriber
    if (
        settings.provider_type != "ucloud"
        or setup is None
        or base["server"] != settings.base_url
        or base["project_id"] != setup.project_id
        or base["storage_path"] != setup.storage_path
    ):
        _refuse()
    _hex(token, _HEX32)
    return setup.storage_path + "/cbs-runtime/" + base["runtime_id"] + "-g-" + token


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _refuse()
        value[key] = item
    return value


def _candidate(value, target, revision):
    _keys(value, {"token", "owner_id", "expected_revision", "operation_sha256", "compute"})
    _hex(value["token"], _HEX32)
    _hex(value["owner_id"], _HEX32)
    _hex(value["operation_sha256"], _HEX64)
    if _revision(value["expected_revision"]) != revision:
        _refuse()
    _matching_compute(value["compute"], target)


def _active(value, target, revision):
    # Shape only. Readers must still verify the durable owner's promotion receipt.
    _keys(value, {"token", "owner_id", "operation_sha256", "compute", "promoted_revision"})
    _hex(value["token"], _HEX32)
    _hex(value["owner_id"], _HEX32)
    _hex(value["operation_sha256"], _HEX64)
    # Only promotion changes the revision, so a published pointer names the current one.
    if _revision(value["promoted_revision"]) != revision or revision == 0:
        _refuse()
    _matching_compute(value["compute"], target)


def _validate_registry(value):
    _keys(value, {"policy", "targets"})
    targets = value["targets"]
    if (
        value["policy"] != REGISTRY_POLICY
        or type(targets) is not dict
        or len(targets) > MAX_TARGETS
    ):
        _refuse()
    tokens, owners = set(), set()
    for identifier, entry in targets.items():
        _keys(entry, {"target", "revision", "active", "candidate"})
        base = validate_target(entry["target"])
        if _hex(identifier, _HEX64) != base["target_sha256"]:
            _refuse()
        revision = _revision(entry["revision"])
        # Stage 1 had no promotion. While Stage 2 stays disabled, an asserted active
        # pointer still refuses; enabled, it is shape only and never owner proof.
        active = entry["active"]
        if active is not None:
            if not ACTIVE_POINTERS_ENABLED:
                _refuse()
            _active(active, base, revision)
        candidate = entry["candidate"]
        if candidate is not None:
            _candidate(candidate, base, revision)
        for item in (active, candidate):
            if item is not None:
                # One token/owner names exactly one generation across the whole registry.
                if item["token"] in tokens or item["owner_id"] in owners:
                    _refuse()
                tokens.add(item["token"])
                owners.add(item["owner_id"])
    return value


def _registry(db):
    row = db.execute(
        "SELECT CASE WHEN length(CAST(value AS BLOB))<=? THEN value END FROM settings WHERE id=?",
        (MAX_BYTES, SETTING),
    ).fetchone()
    if row is None:
        return {"policy": REGISTRY_POLICY, "targets": {}}, None
    raw = row[0]
    if type(raw) is not str:
        _refuse()
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _refuse())
    except (ValueError, TypeError, RecursionError):
        _refuse()
    return _validate_registry(value), raw


def _view(target, entry):
    return copy.deepcopy({"target_sha256": target["target_sha256"], **entry})


def snapshot(db, target):
    """Read metadata only; absent entries do not create or select a generation."""
    base = validate_target(target)
    registry, _ = _registry(db)
    entry = registry["targets"].get(base["target_sha256"])
    if entry is None:
        entry = {"revision": 0, "active": None, "candidate": None}
    else:
        entry = {key: item for key, item in entry.items() if key != "target"}
    return _view(base, entry)


def reserve_candidate(db, target, *, expected_revision, owner_id, token, operation_sha256, compute):
    """Disabled candidate bookkeeping, never a preparation/remote-ownership receipt.

    The future preparation owner must be accepted in the same caller-held writer
    transaction. Stage 1 does not expose release, promotion or activation.
    """
    if not CANDIDATE_RESERVATION_ENABLED:
        raise Conflict("Runtime generation candidate reservation is disabled.")
    if not db.in_transaction:
        raise Conflict("Runtime generation reservation requires a writer transaction.")
    base = validate_target(target)
    expected = _revision(expected_revision)
    candidate = {
        "owner_id": owner_id,
        "token": token,
        "expected_revision": expected,
        "operation_sha256": operation_sha256,
        "compute": validate_compute(compute),
    }
    _candidate(candidate, base, expected)
    registry, previous = _registry(db)
    identifier = base["target_sha256"]
    entry = registry["targets"].get(identifier)
    if entry is None:
        entry = {"target": base, "revision": 0, "active": None, "candidate": None}
    if entry["revision"] != expected:
        _refuse()
    if entry["candidate"] is not None:
        if entry["candidate"] == candidate:
            return snapshot(db, base)
        raise Conflict("Another Runtime generation candidate is unresolved.")
    entry["candidate"] = candidate
    registry["targets"][identifier] = entry
    _validate_registry(registry)
    encoded = _encoded(registry)
    if len(encoded.encode("utf-8")) > MAX_BYTES:
        _refuse()
    if previous is None:
        changed = db.execute(
            "INSERT OR IGNORE INTO settings(id,value) VALUES (?,?)", (SETTING, encoded)
        ).rowcount
    else:
        changed = db.execute(
            "UPDATE settings SET value=? WHERE id=? AND value=?", (encoded, SETTING, previous)
        ).rowcount
    if changed != 1:
        _refuse()
    return snapshot(db, base)


def _publish_active(db, target, *, owner_id, token, operation_sha256, compute):
    """Registry half of Stage-2 promotion; never call it outside the owner-completion CAS.

    The exact reserved candidate becomes the active pointer and the revision advances.
    Nothing is deleted; an earlier active generation simply stops being selected for new
    acceptance. Repeating the same acknowledgment is idempotent.
    """
    if not ACTIVE_POINTERS_ENABLED:
        raise Conflict("Runtime generation promotion is disabled.")
    if not db.in_transaction:
        raise Conflict("Runtime generation promotion requires a writer transaction.")
    base = validate_target(target)
    choice = _matching_compute(compute, base)
    registry, previous = _registry(db)
    entry = registry["targets"].get(base["target_sha256"])
    if entry is None or previous is None:
        _refuse()
    expected = {
        "token": _hex(token, _HEX32),
        "owner_id": _hex(owner_id, _HEX32),
        "expected_revision": entry["revision"],
        "operation_sha256": _hex(operation_sha256, _HEX64),
        "compute": choice,
    }
    if entry["candidate"] is None:
        active = entry["active"]
        if active is not None and {
            key: active[key] for key in ("token", "owner_id", "operation_sha256", "compute")
        } == {key: expected[key] for key in ("token", "owner_id", "operation_sha256", "compute")}:
            return snapshot(db, base)
        _refuse()
    if entry["candidate"] != expected:
        _refuse()
    revision = _revision(entry["revision"] + 1)
    entry["revision"] = revision
    entry["candidate"] = None
    entry["active"] = {
        "token": token,
        "owner_id": owner_id,
        "operation_sha256": operation_sha256,
        "compute": choice,
        "promoted_revision": revision,
    }
    _validate_registry(registry)
    encoded = _encoded(registry)
    if len(encoded.encode("utf-8")) > MAX_BYTES:
        _refuse()
    changed = db.execute(
        "UPDATE settings SET value=? WHERE id=? AND value=?", (encoded, SETTING, previous)
    ).rowcount
    if changed != 1:
        _refuse()
    return snapshot(db, base)

"""Disabled Runtime generation owner/folder/promotion slice (ADR 0176, Stage 2).

A fresh generation is prepared by the existing durable preparation owner (its Stop,
capacity, epoch, submission and Activity rules), never by a second remote-job manager.
Nothing here is reachable from admission, carry, batching/execution, selected repair,
provenance/capture or restore: every entry point and the worker refuse unless ``ENABLED``
and the Stage-1 candidate and active-pointer gates are all on; each stays independently
off. The legacy decoder and worker import this module only for a row already born with a
generation.

A failed candidate no longer blocks its target forever: ``preview_supersession`` and
``supersede`` replace one exact, provably settled candidate with a fresh owner under a new
fingerprint-bound confirmation. The old owner keeps every record and obligation and gains a
permanent receipt; nothing remote is contacted, adopted, released or deleted.

Not implemented (refused): namespace creation, CPU recipes, base-only generations,
no-ID Retry or successors, name-based reconciliation, superseding an active, uncertain or
allocation-holding candidate, environment cleanup, active-selection capture and any public
API/UI.
"""

import copy
import math
import re
import secrets
import time
from types import SimpleNamespace

from ucloud_workflow import UCloudAPIError

from . import enhancement_api as api
from . import providers, runtime_compute, runtime_store
from . import runtime_generations as generations
from . import runtime_preparation as preparation
from . import runtime_preparation_observation as observation
from . import ucloud_native as native
from .store import new_id

ENABLED = False
POLICY = "cbs-runtime-generation-preparation-v1"
OPERATION_POLICY = "cbs-runtime-generation-operation-v1"
CONFIRMATION_POLICY = "cbs-runtime-generation-confirmation-v1"
PROMOTION_FORMAT = "cbs-runtime-generation-promotion-v1"
SUPERSESSION_POLICY = "cbs-runtime-generation-supersession-v1"
SUPERSESSION_FORMAT = "cbs-runtime-generation-supersession-receipt-v1"
# A candidate owner in one of these states has no worker; "settled" also needs no recorded
# allocation, uncertain POST or reserved capacity (runtime_store._reserved).
SETTLED = frozenset({"failed", "cancelled", "interrupted"})
FOLDERS = ("environment", "run")
OUTCOMES = {"pending", "acknowledged", "conflict", "uncertain"}
# What a finished PREPARE_ONLY supervisor leaves in a fresh root; anything else (for
# example a retained .prepare-lock, stamp.pending or uv download) refuses promotion.
ROOT_REQUIRED = {
    ".in-use": "FILE",
    "hf": "DIRECTORY",
    "nltk": "DIRECTORY",
    "stamp.json": "FILE",
    "venv": "DIRECTORY",
}
ROOT_OPTIONAL = {"bin": "DIRECTORY", "python": "DIRECTORY", "requirements.txt": "FILE"}
MAX_SPECIFICATION_BYTES = 16 * 1024
DISABLED = "Runtime generation preparation is disabled in this version. Nothing was started."
_HEX32 = re.compile(r"[a-f0-9]{32}\Z")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_GENERATION_KEYS = {
    "policy",
    "target",
    "token",
    "owner_id",
    "expected_revision",
    "operation",
    "operation_sha256",
    "confirmation_sha256",
}
_OPERATION_KEYS = {
    "policy",
    "connection_fingerprint",
    "target_sha256",
    "runtime_id",
    "recipe_sha256",
    "model_plan_sha256",
    "application",
    "compute",
    "compute_policy",
    "namespace",
    "allocation_hours",
    "prepare_only",
}
_CONFIRMATION_KEYS = {
    "policy",
    "connection_fingerprint",
    "target",
    "operation",
    "operation_sha256",
    "expected_revision",
    "confirmation_sha256",
}
_PROMOTION_KEYS = {
    "format",
    "token",
    "owner_id",
    "target_sha256",
    "operation_sha256",
    "job_id",
    "specification_sha256",
    "folders_sha256",
    "evidence_sha256",
    "registry_revision",
}
_SUPERSESSION_KEYS = {"policy", "confirmation", "superseded", "evidence", "supersession_sha256"}
_SUPERSESSION_RECEIPT_KEYS = {
    "format",
    "successor_owner_id",
    "successor_token",
    "supersession_sha256",
    "observed_sha256",
    "registry_revision",
}


class Conflict(preparation.Conflict):
    """Disabled, stale, unverified or unimplemented generation work grants no authority."""


def _enabled():
    # All three switches: with only some on, a paid allocation could run whose candidate
    # could never be reserved or whose promotion could never be published.
    return bool(
        ENABLED
        and generations.CANDIDATE_RESERVATION_ENABLED
        and generations.ACTIVE_POINTERS_ENABLED
    )


def _require_enabled():
    if not _enabled():
        raise Conflict(DISABLED)


def _bad():
    raise ValueError("Runtime generation owner evidence is invalid.")


def _digest(value):
    return runtime_store._digest(value)


def _hex(value, pattern):
    if type(value) is not str or pattern.fullmatch(value) is None:
        _bad()
    return value


def _hours():
    # The existing builder's PREPARE allocation for its one-minute preparation request.
    return max(1, math.ceil((1 + api.PREPARE_SECONDS // 60) / 60))


def operation_compute(frozen):
    """Map the existing trusted funded-MIG decision. CPU has no qualified recipe yet."""
    if frozen != {
        "runtime_product": runtime_compute.MIG_PRODUCT,
        "runtime_compute_policy": runtime_compute.POLICY,
    }:
        raise Conflict(
            "Runtime generation preparation requires the exact funded CBS Runtime MIG (CUDA) "
            "identity. CPU preparation has no qualified recipe and is refused."
        )
    return generations.validate_compute(
        {"product": dict(runtime_compute.MIG_PRODUCT), "device": "cuda", "flavor": "cuda-cu128"}
    )


def _cuda(compute):
    # Data-only: historical rows stay readable even if a later version changes products.
    choice = generations.validate_compute(compute)
    if (choice["device"], choice["flavor"]) != ("cuda", "cuda-cu128"):
        _bad()
    return choice


def _label(value):
    return (
        type(value) is str and 0 < len(value) <= 200 and not any(ord(char) < 32 for char in value)
    )


def _application(value):
    """Shape and type only, so a frozen choice stays readable after flavours change."""
    if (
        type(value) is not dict
        or set(value) != {"name", "version"}
        or not _label(value["name"])
        or not _label(value["version"])
    ):
        _bad()
    return dict(value)


def _supported_application(value):
    """Fresh confirmation/submission only: one of this version's Terminal batch flavours."""
    if _application(value)["name"] not in api.SCRIPT_FLAVOURS:
        _bad()
    return dict(value)


def _operation(settings, target, application, compute, connection_fingerprint, model_plan):
    return {
        "policy": OPERATION_POLICY,
        "connection_fingerprint": connection_fingerprint,
        "target_sha256": target["target_sha256"],
        "runtime_id": target["runtime_id"],
        "recipe_sha256": preparation.recipe_hash(),
        "model_plan_sha256": _digest(model_plan),
        "application": _supported_application(application),
        "compute": _cuda(compute),
        "compute_policy": runtime_compute.POLICY,
        "namespace": settings.ucloud_transcriber.storage_path + "/cbs-runtime",
        "allocation_hours": _hours(),
        "prepare_only": True,
    }


def _confirmation(connection_fingerprint, target, operation, operation_sha256, revision):
    value = {
        "policy": CONFIRMATION_POLICY,
        "connection_fingerprint": connection_fingerprint,
        "target": target,
        "operation": operation,
        "operation_sha256": operation_sha256,
        "expected_revision": revision,
    }
    return {**value, "confirmation_sha256": _digest(value)}


def _freeze(store, credentials):
    """Read-only: HF plan, trusted funded MIG, Terminal app and registry revision."""
    plan = preparation.huggingface_plan(store, credentials)
    if plan.get("status") != "ready":
        raise Conflict(
            "Save a ready Hugging Face Runtime model plan first. A base-only generation could "
            "never gain models later, so it is refused."
        )
    chosen, model_plan = plan["chosen"], plan["model_plan"]
    settings = providers.Settings.model_validate(chosen["settings"])
    compute = operation_compute(runtime_compute.freeze(settings, credentials))
    key = credentials.get(providers.credential_scope(settings))
    with native.client_for(settings, key) as client:
        application = api.script_application(client)
    target = generations.base_target(
        settings,
        runtime_id=api.runtime_id(),
        requirements_sha256=api.requirements_hash(),
        compute=compute,
    )
    try:
        operation = _operation(
            settings, target, application, compute, plan["connection_fingerprint"], model_plan
        )
    except ValueError:
        raise Conflict(
            "The UCloud Terminal application is not a supported batch flavour."
        ) from None
    with store.connection() as db:
        entry = generations.snapshot(db, target)
    confirmation = _confirmation(
        plan["connection_fingerprint"], target, operation, _digest(operation), entry["revision"]
    )
    return confirmation, chosen, model_plan, entry


def preview(store, credentials):
    """Freeze exact choices before any confirmation; creates and reserves nothing."""
    _require_enabled()
    confirmation, _, _, entry = _freeze(store, credentials)
    if entry["candidate"] is not None:
        raise Conflict(
            "Another Runtime generation candidate is unresolved. Only an explicitly confirmed "
            "supersession of a settled candidate can replace it; its evidence is retained."
        )
    return confirmation


def _accepted_files(plan):
    # Exact-file acceptance: read-only local hashes. Nothing is fetched or credentialed.
    files = api.local_speaker_files()
    if {relative for _, relative in files} != set(plan["speaker_files"]) or any(
        not api.verified_file(path, *plan["speaker_files"][relative]) for path, relative in files
    ):
        raise Conflict("The accepted speaker files changed. Save Hugging Face settings again.")
    return files


def _deduplicated(db, candidate, confirmation):
    if candidate["operation_sha256"] != confirmation["operation_sha256"]:
        return None
    try:
        owner = preparation._get(db, candidate["owner_id"])
    except (KeyError, preparation.Conflict):
        return None
    generation = owner["frozen"].get("generation") or {}
    if (
        owner["archived"]
        or generation.get("token") != candidate["token"]
        or generation.get("confirmation_sha256") != confirmation["confirmation_sha256"]
    ):
        return None
    return owner


def _require_decodable(db):
    # An unknown live owner cannot establish that another allocation is safe.
    for (identifier,) in db.execute(
        "SELECT id FROM runtime_preparations WHERE archived=0"
    ).fetchall():
        try:
            preparation._get(db, identifier)
        except preparation.Conflict:
            raise Conflict(
                "Resolve unverified preparation evidence in Activity "
                "before starting another allocation."
            ) from None


def admit(store, credentials, confirmation, guard):
    """Insert the durable owner and its registry candidate in one writer transaction.

    ``guard`` is the fresh Hugging Face access/acceptance fence of the explicit caller;
    a previous task's authority is never copied. Nothing remote is created here.
    """
    _require_enabled()
    if not callable(guard):
        raise Conflict("Runtime generation acceptance requires its fresh access guard.")
    if type(confirmation) is not dict or set(confirmation) != _CONFIRMATION_KEYS:
        raise Conflict("Review and confirm the Runtime generation request again.")
    guard()
    fresh, chosen, model_plan, _ = _freeze(store, credentials)
    if not preparation._same(fresh, confirmation):  # Type-strict: 1.0 is not revision 1.
        raise Conflict("The Runtime generation request changed. Review and confirm it again.")
    _accepted_files(model_plan)
    guard()
    identity = preparation._identity(chosen, model_plan=model_plan)
    if (
        preparation.fingerprint(chosen, model_plan=model_plan)
        != confirmation["connection_fingerprint"]
    ):
        raise Conflict("The Runtime generation request changed. Review and confirm it again.")
    target, operation = confirmation["target"], confirmation["operation"]
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        guard()  # BEGIN may have waited; the fence must still hold at acceptance.
        _require_decodable(db)
        entry = generations.snapshot(db, target)
        if entry["candidate"] is not None:
            existing = _deduplicated(db, entry["candidate"], confirmation)
            if existing is None:
                raise Conflict("Another Runtime generation candidate is unresolved.")
            return {
                "prepared": False,
                "runtime_id": api.runtime_id(),
                "operation": preparation.card(existing),
                "deduplicated": True,
            }
        if entry["revision"] != confirmation["expected_revision"]:
            raise Conflict("The Runtime generation registry changed. Review it again.")
        identifier, token = new_id(), secrets.token_hex(16)
        generation = {
            "policy": POLICY,
            "target": target,
            "token": token,
            "owner_id": identifier,
            "expected_revision": confirmation["expected_revision"],
            "operation": operation,
            "operation_sha256": confirmation["operation_sha256"],
            "confirmation_sha256": confirmation["confirmation_sha256"],
        }
        value = preparation._insert(
            db,
            {**identity, "generation": generation},
            confirmation["connection_fingerprint"],
            identifier=identifier,
        )
        generations.reserve_candidate(
            db,
            target,
            expected_revision=confirmation["expected_revision"],
            owner_id=identifier,
            token=token,
            operation_sha256=confirmation["operation_sha256"],
            compute=operation["compute"],
        )
    return {
        "prepared": False,
        "runtime_id": api.runtime_id(),
        "operation": preparation.card(value),
        "deduplicated": False,
    }


def _settled(db, target, candidate):
    """The exact candidate's own owner, provably settled and never promoted.

    Settled: no worker (``SETTLED`` state) and no recorded open allocation, uncertain POST
    or reserved capacity. An uncertain POST may be a running paid allocation and stays a
    blocker until exact recovery exists. Uncertain/conflicting folder intents and a pending
    run-folder cleanup concern only this owner's own random paths; they stay on it as
    visible obligations and never block, transfer or get adopted.
    """
    try:
        owner = preparation._get(db, candidate["owner_id"], owned=True)
    except KeyError:
        raise Conflict(
            "The Runtime generation candidate has no owner record. Preserve its evidence."
        ) from None
    except preparation.Conflict:
        raise Conflict(
            "The Runtime generation candidate's owner is historical or unverified. "
            "Preserve its evidence."
        ) from None
    generation, journal = owner["frozen"].get("generation"), owner["journal"]
    if (
        generation is None
        or not preparation._same(generation["target"], target)
        or generation["token"] != candidate["token"]
        or generation["operation_sha256"] != candidate["operation_sha256"]
        or not preparation._same(generation["expected_revision"], candidate["expected_revision"])
        or not preparation._same(generation["operation"]["compute"], candidate["compute"])
        or "generation_supersession" in journal
        or "generation_promotion" in journal
    ):
        raise Conflict(
            "The Runtime generation candidate does not match its owner. Preserve its evidence."
        )
    if owner["state"] not in SETTLED:
        raise Conflict(
            "The Runtime generation candidate is still active. Stop it and wait for its "
            "outcome before superseding it."
        )
    if runtime_store._uncertain(journal):
        raise Conflict(
            "The Runtime generation candidate's submission outcome is uncertain. A possibly "
            "running allocation is never superseded; its evidence is retained."
        )
    if runtime_store._reserved(journal):
        raise Conflict(
            "The Runtime generation candidate still holds a recorded allocation or capacity. "
            "Stop it and wait for UCloud's terminal state before superseding it."
        )
    return owner


def _evidence(owner):
    """What the user confirms is retained on the superseded owner, bound to its exact row."""
    journal = owner["journal"]
    folders = journal.get("generation_folders") or {}
    return {
        "owner_id": owner["id"],
        "state": owner["state"],
        "job_id": journal.get("job_id"),
        "remote_state": journal.get("remote_state"),
        "folders": {kind: folders[kind]["outcome"] for kind in FOLDERS if kind in folders},
        "cleanup_state": journal.get("cleanup_state"),
        "observed_sha256": _digest(
            {key: owner[key] for key in ("id", "epoch", "state", "journal")}
        ),
    }


def _supersession(confirmation, candidate, evidence):
    value = {
        "policy": SUPERSESSION_POLICY,
        "confirmation": confirmation,
        "superseded": candidate,
        "evidence": evidence,
    }
    return {**value, "supersession_sha256": _digest(value)}


def preview_supersession(store, credentials):
    """Freeze a fresh request plus the exact settled candidate it would replace.

    Read-only, like ``preview``: nothing is created, reserved, released or adopted.
    """
    _require_enabled()
    confirmation, _, _, _ = _freeze(store, credentials)
    target = confirmation["target"]
    with store.connection() as db:  # One read: the candidate and its owner agree.
        entry = generations.snapshot(db, target)
        if entry["revision"] != confirmation["expected_revision"]:
            raise Conflict("The Runtime generation registry changed. Review it again.")
        if entry["candidate"] is None:
            raise Conflict(
                "No Runtime generation candidate is unresolved. Review a fresh request instead."
            )
        owner = _settled(db, target, entry["candidate"])
    return _supersession(confirmation, entry["candidate"], _evidence(owner))


def _superseded_by(db, old, supersession):
    """A repeated identical supersession returns its recorded successor, never a second one.

    Only while its frozen request is still current: after the successor's promotion the
    registry revision moved on, so ``supersede`` refuses the stale request before this.
    """
    try:
        owner = preparation._get(db, old)
    except (KeyError, preparation.Conflict):
        return None
    receipt = owner["journal"].get("generation_supersession")
    if type(receipt) is not dict or receipt["supersession_sha256"] != supersession.get(
        "supersession_sha256"
    ):
        return None
    try:
        return owner, preparation._get(db, receipt["successor_owner_id"])
    except (KeyError, preparation.Conflict):
        raise Conflict(
            "The recorded Runtime generation successor is unverified. Preserve its evidence."
        ) from None


def supersede(store, credentials, supersession, guard):
    """Replace one exact settled candidate: one writer transaction, nothing remote.

    Records the old owner's permanent receipt (advancing its epoch to fence any stale
    writer), inserts the new durable owner and swaps the registry candidate atomically.
    The registry revision and active pointer are unchanged. The old owner keeps every
    folder intent, allocation record and cleanup obligation; its random paths are never
    reused, adopted, released or deleted. ``guard`` is the caller's fresh access fence.
    """
    _require_enabled()
    if not callable(guard):
        raise Conflict("Runtime generation supersession requires its fresh access guard.")
    if (
        type(supersession) is not dict
        or set(supersession) != _SUPERSESSION_KEYS
        or type(supersession["confirmation"]) is not dict
        or set(supersession["confirmation"]) != _CONFIRMATION_KEYS
        or type(supersession["superseded"]) is not dict
        or type(supersession["superseded"].get("owner_id")) is not str
        or _HEX32.fullmatch(supersession["superseded"]["owner_id"]) is None
    ):
        raise Conflict("Review and confirm the Runtime generation supersession again.")
    confirmation, old = supersession["confirmation"], supersession["superseded"]["owner_id"]
    guard()
    fresh, chosen, model_plan, _ = _freeze(store, credentials)
    if not preparation._same(fresh, confirmation):
        raise Conflict("The Runtime generation request changed. Review and confirm it again.")
    _accepted_files(model_plan)
    guard()
    identity = preparation._identity(chosen, model_plan=model_plan)
    if (
        preparation.fingerprint(chosen, model_plan=model_plan)
        != confirmation["connection_fingerprint"]
    ):
        raise Conflict("The Runtime generation request changed. Review and confirm it again.")
    target, operation = confirmation["target"], confirmation["operation"]
    # The old owner's control slot excludes a concurrent Stop/Retry/cleanup on it.
    with preparation._control(old), store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        guard()  # BEGIN may have waited; the fence must still hold at acceptance.
        _require_decodable(db)
        repeated = _superseded_by(db, old, supersession)
        if repeated is not None:
            retired, successor = repeated
            return {
                "prepared": False,
                "runtime_id": api.runtime_id(),
                "operation": preparation.card(successor),
                "superseded": preparation.card(retired),
                "deduplicated": True,
            }
        entry = generations.snapshot(db, target)
        candidate = entry["candidate"]
        if (
            entry["revision"] != confirmation["expected_revision"]
            or candidate is None
            or not preparation._same(candidate, supersession["superseded"])
        ):
            raise Conflict("The Runtime generation candidate changed. Review it again.")
        owner = _settled(db, target, candidate)
        if not preparation._same(
            _supersession(confirmation, candidate, _evidence(owner)), supersession
        ):
            raise Conflict("The superseded Runtime generation changed. Review it again.")
        identifier, token = new_id(), secrets.token_hex(16)
        generation = {
            "policy": POLICY,
            "target": target,
            "token": token,
            "owner_id": identifier,
            "expected_revision": confirmation["expected_revision"],
            "operation": operation,
            "operation_sha256": confirmation["operation_sha256"],
            "confirmation_sha256": confirmation["confirmation_sha256"],
        }
        value = preparation._insert(
            db,
            {**identity, "generation": generation},
            confirmation["connection_fingerprint"],
            identifier=identifier,
        )
        retired = preparation._update(
            db,
            owner,
            epoch=owner["epoch"] + 1,
            message="Superseded by a newer Runtime generation request. Its evidence and "
            "obligations are retained.",
            journal={
                "generation_supersession": {
                    "format": SUPERSESSION_FORMAT,
                    "successor_owner_id": identifier,
                    "successor_token": token,
                    "supersession_sha256": supersession["supersession_sha256"],
                    "observed_sha256": supersession["evidence"]["observed_sha256"],
                    "registry_revision": entry["revision"],
                }
            },
            _generation_writer=True,
        )
        generations.supersede_candidate(
            db,
            target,
            superseded=candidate,
            owner_id=identifier,
            token=token,
            operation_sha256=confirmation["operation_sha256"],
            compute=operation["compute"],
        )
    return {
        "prepared": False,
        "runtime_id": api.runtime_id(),
        "operation": preparation.card(value),
        "superseded": preparation.card(retired),
        "deduplicated": False,
    }


def _owner(value):
    """Data-only frozen binding; never current-version constants that would orphan history.

    Current support (compute policy, Terminal flavours, script parameter, recipe) is refused
    only at the specific fresh action that depends on it, never by making a record unreadable.
    """
    frozen, identifier = value["frozen"], value["id"]
    generation = frozen["generation"]
    if (
        type(generation) is not dict
        or set(generation) != _GENERATION_KEYS
        or generation["policy"] != POLICY
        or frozen.get("retry_of") is not None
        or "model_plan" not in frozen
        or generation["owner_id"] != identifier
    ):
        _bad()
    settings = providers.Settings.model_validate(frozen["settings"])
    target = generations.validate_target(generation["target"])
    root = generations.generation_root(settings, target, generation["token"])
    revision = generation["expected_revision"]
    operation = generation["operation"]
    if (
        target["runtime_id"] != frozen["runtime_id"]
        or target["device"] != "cuda"
        or type(revision) is not int
        or not 0 <= revision <= 2**53 - 1
        or type(operation) is not dict
        or set(operation) != _OPERATION_KEYS
        or operation["policy"] != OPERATION_POLICY
        or operation["connection_fingerprint"] != value["connection_fingerprint"]
        or operation["target_sha256"] != target["target_sha256"]
        or operation["runtime_id"] != frozen["runtime_id"]
        or operation["recipe_sha256"] != frozen["recipe_sha256"]
        or operation["model_plan_sha256"] != _digest(frozen["model_plan"])
        or not _label(operation["compute_policy"])
        or operation["namespace"] != settings.ucloud_transcriber.storage_path + "/cbs-runtime"
        or type(operation["allocation_hours"]) is not int
        or not 1 <= operation["allocation_hours"] <= 48
        or operation["prepare_only"] is not True
        or generation["operation_sha256"] != _digest(operation)
    ):
        _bad()
    _application(operation["application"])
    if _cuda(operation["compute"])["device"] != target["device"]:
        _bad()
    expected = _confirmation(
        value["connection_fingerprint"],
        target,
        operation,
        generation["operation_sha256"],
        revision,
    )
    if generation["confirmation_sha256"] != expected["confirmation_sha256"]:
        _bad()
    return settings, generation, root


def _bound_specification(specification, settings, generation, root, identifier, *, parameter=None):
    """The recorded specification may mount only this owner's run folder and generation.

    Comparisons are type-strict (canonical bytes). Decoding (``parameter=None``) checks the
    stored script-parameter name for shape only; a fresh submission passes this version's
    name, so a later rename keeps recorded history readable.
    """
    run = api.run_folder(settings, identifier)
    operation = generation["operation"]
    if (
        type(specification) is not dict
        or len(runtime_store.canonical(specification).encode()) > MAX_SPECIFICATION_BYTES
    ):
        _bad()
    parameters = specification.get("parameters")
    if type(parameters) is not dict or len(parameters) != 1:
        _bad()
    ((name, script),) = parameters.items()
    expected = {
        "application": operation["application"],
        "product": operation["compute"]["product"],
        "resources": [
            {"type": "file", "path": run, "readOnly": False},
            {"type": "file", "path": root, "readOnly": False},
        ],
        "timeAllocation": {"hours": operation["allocation_hours"], "minutes": 0, "seconds": 0},
        "sshEnabled": False,
    }
    if (
        specification.get("name") != "CBS runtime generation " + identifier
        or not _label(name)
        or (parameter is not None and name != parameter)
        or not preparation._same(script, {"type": "file", "path": run + "/supervisor.sh"})
        or any(
            not preparation._same(specification.get(key, preparation._ABSENT), item)
            for key, item in expected.items()
        )
    ):
        _bad()
    return specification


def _validate_folders(value, folders, paths):
    if type(folders) is not dict or not folders or not set(folders) <= set(FOLDERS):
        _bad()
    if "run" in folders and (folders.get("environment") or {}).get("outcome") != "acknowledged":
        _bad()
    nonces = set()
    for kind, entry in folders.items():
        if (
            type(entry) is not dict
            or set(entry) != {"path", "nonce", "epoch", "outcome"}
            or entry["path"] != paths[kind]
            or _hex(entry["nonce"], _HEX32) in nonces
            or type(entry["epoch"]) is not int
            or not 0 <= entry["epoch"] <= value["epoch"]
            or entry["outcome"] not in OUTCOMES
        ):
            _bad()
        nonces.add(entry["nonce"])


def validate_owner(value):
    """Decode-time consistency for a generation-born owner; no network, registry or clock."""
    settings, generation, root = _owner(value)
    journal, identifier = value["journal"], value["id"]
    paths = {"environment": root, "run": api.run_folder(settings, identifier)}
    folders = journal.get("generation_folders")
    if folders is not None:
        _validate_folders(value, folders, paths)

    def acknowledged(kind):
        return ((folders or {}).get(kind) or {}).get("outcome") == "acknowledged"

    if ("owned_folder" in journal) != acknowledged("run") or journal.get(
        "owned_folder", paths["run"]
    ) != paths["run"]:
        _bad()
    if "specification" in journal:
        _bound_specification(journal["specification"], settings, generation, root, identifier)
    submission = journal.get("generation_submission")
    if submission is not None and (
        type(submission) is not dict
        or set(submission) != {"epoch", "specification_sha256", "folders_sha256"}
        or type(submission["epoch"]) is not int  # Type-strict: 1.0 is not epoch 1.
        or not all(acknowledged(kind) for kind in FOLDERS)
        or any(folders[kind]["epoch"] != submission["epoch"] for kind in FOLDERS)
        or "specification" not in journal
        or submission["specification_sha256"] != _digest(journal["specification"])
        or submission["folders_sha256"] != _digest(folders)
        or journal.get("submission_intent") is not True
    ):
        _bad()
    if submission is None and (journal.get("submission_intent") or "job_id" in journal):
        _bad()  # Only the receipt-proving submission writer may accept a POST.
    if any(journal.get(key, False) is not False for key in ("reconciled_not_created", "no_post")):
        _bad()  # Name reconciliation never resolves a generation POST; no absence receipt.
    if {"runtime_product", "runtime_compute_policy"} & set(journal):
        _bad()  # Compute is frozen in the operation, never journaled at submit.
    promotion = journal.get("generation_promotion")
    proof = {"prepared", "doctor_verified", "model_plan_receipt"} & set(journal)
    if (value["state"] == "completed") != (promotion is not None) or (proof and promotion is None):
        _bad()
    if promotion is not None and (
        type(promotion) is not dict
        or set(promotion) != _PROMOTION_KEYS
        or promotion["format"] != PROMOTION_FORMAT
        or promotion["token"] != generation["token"]
        or promotion["owner_id"] != identifier
        or promotion["target_sha256"] != generation["target"]["target_sha256"]
        or promotion["operation_sha256"] != generation["operation_sha256"]
        or promotion["job_id"] != journal.get("job_id")
        or promotion["specification_sha256"] != submission["specification_sha256"]
        or promotion["folders_sha256"] != submission["folders_sha256"]
        or _hex(promotion["evidence_sha256"], _HEX64) is None
        or type(promotion["registry_revision"]) is not int
        or not 1 <= promotion["registry_revision"] <= 2**53 - 1
        or journal.get("prepared") is not True
        or journal.get("doctor_verified") is not True
        or "model_plan_receipt" not in journal
        or journal.get("remote_state") != "SUCCESS"
    ):
        _bad()
    if "generation_supersession" in journal:
        # Permanently settled: any later write that would requeue, reserve or promote this
        # owner fails decoding and rolls back with it.
        receipt = journal["generation_supersession"]
        if (
            type(receipt) is not dict
            or set(receipt) != _SUPERSESSION_RECEIPT_KEYS
            or receipt["format"] != SUPERSESSION_FORMAT
            or _hex(receipt["successor_owner_id"], _HEX32) == identifier
            or _hex(receipt["successor_token"], _HEX32) == generation["token"]
            or _hex(receipt["supersession_sha256"], _HEX64) is None
            or _hex(receipt["observed_sha256"], _HEX64) is None
            or not preparation._same(receipt["registry_revision"], generation["expected_revision"])
            or value["state"] not in SETTLED
            or promotion is not None
            or runtime_store._reserved(journal)
        ):
            _bad()


def _write(store, identifier, epoch, change, *, drain=False):
    """Trusted generation journal writer. ``drain`` records accepted replies after Stop."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        value = preparation._get(db, identifier, owned=True)
        if value["epoch"] != epoch:
            raise preparation.Interrupted()
        if "generation" not in value["frozen"]:
            raise Conflict("Only a Runtime generation owner has generation evidence.")
        if not drain and (value["state"] != "running" or value["journal"].get("stop_intent")):
            raise preparation.Interrupted()
        # Decoding after the write validates every transition before this transaction ends.
        return preparation._update(db, value, journal=change(value), _generation_writer=True)


def _intent(kind, path, nonce):
    def change(value):
        folders = copy.deepcopy(value["journal"].get("generation_folders") or {})
        if kind in folders or (
            kind == "run" and (folders.get("environment") or {}).get("outcome") != "acknowledged"
        ):
            raise Conflict("An exclusive Runtime generation folder is never created again.")
        folders[kind] = {
            "path": path,
            "nonce": nonce,
            "epoch": value["epoch"],
            "outcome": "pending",
        }
        return {"generation_folders": folders}

    return change


def _outcome(kind, nonce, outcome):
    def change(value):
        folders = copy.deepcopy(value["journal"].get("generation_folders") or {})
        entry = folders.get(kind)
        if entry is None or entry["nonce"] != nonce or entry["outcome"] != "pending":
            raise Conflict("This Runtime generation folder intent changed. Preserve it.")
        entry["outcome"] = outcome
        update = {"generation_folders": folders}
        if kind == "run" and outcome == "acknowledged":
            update.update(owned_folder=entry["path"], cleanup_state="pending")
        return update

    return change


def _acknowledged(reply, path):
    """Accept only an empty or exactly one-item create response for this path."""
    if reply == {}:
        return True
    if type(reply) is not dict or set(reply) != {"responses"}:
        return False
    items = reply["responses"]
    return (
        type(items) is list
        and len(items) == 1
        and (items[0] is None or (type(items[0]) is dict and items[0].get("id", path) == path))
    )


FOLDER_FAILURES = {
    "conflict": "A Runtime generation folder already existed. It was not adopted and nothing "
    "was submitted; the evidence is retained.",
    "uncertain": "Creating a Runtime generation folder had no exact acknowledgment. It is "
    "retained for explicit recovery; nothing was retried or submitted.",
}


class GenerationRun(preparation.Run):
    """The existing durable owner, with generation folders, mounts and sole promotion."""

    def execute(self):
        if not _enabled():
            # No credentials, client, legacy-root check or remote request while disabled.
            try:
                self.checkpoint(state="failed", message=DISABLED)
            except preparation.Interrupted:
                pass
            return None
        return self._execute()

    def _write(self, change, *, drain=False):
        self.value = _write(self.store, self.identifier, self.epoch, change, drain=drain)
        return self.value

    def _namespace(self):
        namespace = self.settings.ucloud_transcriber.storage_path + "/cbs-runtime"
        try:
            item = observation.metadata(self.client, namespace)
        except observation.Pending:
            raise Conflict(
                "The app-owned UCloud Runtime folder was not found. Creating it is a separate "
                "unimplemented step; nothing was created."
            ) from None
        if api._runtime_kind(item) != "DIRECTORY":
            raise Conflict("The app-owned UCloud Runtime folder could not be verified.")

    def _create(self, kind, path):
        nonce = secrets.token_hex(16)
        self.guard(submitting=True)
        self._write(_intent(kind, path, nonce))
        try:
            reply = self.client.create_folder(path)  # REJECT on conflict; never adopted.
        except UCloudAPIError as exc:
            outcome = "conflict" if exc.status_code == 409 else "uncertain"
        except Exception:  # A lost reply may still have created it.
            outcome = "uncertain"
        else:
            outcome = "acknowledged" if _acknowledged(reply, path) else "uncertain"
        # Drain the accepted reply even after Stop. Only the next guard stops later POSTs.
        self._write(_outcome(kind, nonce, outcome), drain=True)
        if outcome != "acknowledged":
            raise Conflict(FOLDER_FAILURES[outcome])
        self.guard(submitting=True)

    def _receipts(self, value, posted):
        """Prove folders and that the recorded specification is exactly the one to POST."""
        settings, generation, root = _owner(value)
        journal = value["journal"]
        folders = journal.get("generation_folders") or {}
        if (
            any(
                (folders.get(kind) or {}).get("outcome") != "acknowledged"
                or folders[kind]["epoch"] != value["epoch"]
                for kind in FOLDERS
            )
            or journal.get("owned_folder") != api.run_folder(settings, value["id"])
            or "specification" not in journal
            or not preparation._same(journal["specification"], posted)
        ):
            raise Conflict(
                "Runtime generation folders are not acknowledged for this owner. "
                "Nothing was submitted."
            )
        return {
            "generation_submission": {
                "epoch": value["epoch"],
                "specification_sha256": _digest(journal["specification"]),
                "folders_sha256": _digest(folders),
            }
        }

    def submit(self):
        self.guard(submitting=True)
        journal = self.value["journal"]
        if (
            journal.get("generation_folders")
            or "specification" in journal
            or journal.get("submission_intent")
            or journal.get("job_id")
        ):
            raise Conflict(
                "This Runtime generation already recorded folder or submission evidence. It is "
                "retained for explicit recovery; nothing is created or submitted again."
            )
        settings, generation, root = _owner(self.value)
        operation = generation["operation"]
        frozen = self.value["frozen"]
        if (
            operation["recipe_sha256"] != preparation.recipe_hash()
            or operation["allocation_hours"] != _hours()
        ):
            raise Conflict("The runtime recipe changed. This generation cannot be prepared.")
        # Current support is checked here, at the fresh submission, never while decoding:
        # a later version keeps the frozen record readable but never submits its old choices.
        if operation["compute_policy"] != runtime_compute.POLICY:
            raise Conflict(
                "The Runtime compute policy changed. This generation cannot be prepared."
            )
        try:
            _supported_application(operation["application"])
        except ValueError:
            raise Conflict(
                "The frozen UCloud Terminal application is no longer a supported batch flavour. "
                "This generation cannot be prepared."
            ) from None
        # Refusal-only: later funding changes never retarget the frozen product.
        current = operation_compute(runtime_compute.freeze(self.settings, self.worker.credentials))
        if current != operation["compute"]:
            raise Conflict("The frozen Runtime generation compute is no longer verified.")
        files = _accepted_files(frozen["model_plan"])
        specification = api.build_script_specification(
            self.settings,
            operation["application"],
            self.identifier,
            SimpleNamespace(timeout_minutes=1),
            True,
            runtime_product=operation["compute"]["product"],
            runtime=root,
        )
        specification["name"] = journal["run_name"]
        try:
            _bound_specification(
                specification,
                settings,
                generation,
                root,
                self.identifier,
                parameter=api.SCRIPT_PARAMETER,
            )
        except ValueError:
            raise Conflict("The Runtime generation specification is not bound.") from None
        self._write(lambda _: {"specification": specification})
        self.guard(submitting=True)
        self._namespace()
        staging = self.store.root / "runtime-preparations"
        staging.mkdir(exist_ok=True)
        if staging.is_symlink() or staging.resolve() != self.store.root / "runtime-preparations":
            raise Conflict("The local preparation staging folder is not owned by this library.")
        folder = staging / self.identifier
        folder.mkdir()  # New owner, new staging folder; never adopt a collision.
        self._create("environment", root)
        remote = api.run_folder(self.settings, self.identifier)
        self._create("run", remote)
        api.seed_speakers(
            self.client,
            self.settings.base_url,
            self.settings,
            files,
            lambda: self.guard(submitting=True),
            folder / "speakers-complete.txt",
            root=root,
        )
        supervisor = folder / "supervisor.sh"
        supervisor.write_text(
            api.supervisor_script(
                self.settings,
                self.identifier,
                True,
                [name for name in frozen["model_plan"]["models"] if name != "speakers"],
                prepare_only=True,
                runtime=root,
            ),
            encoding="utf-8",
            newline="\n",
        )
        requirements = folder / "requirements.txt"
        requirements.write_bytes(api.requirements_bytes())
        files = [(path, path.name) for path in api.WORKER_FILES]
        files += [(supervisor, "supervisor.sh"), (requirements, "requirements.txt")]
        self.checkpoint(message="Uploading public runtime generation scripts…", progress=0.1)
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
            if preparation.begin_submission(
                self.store,
                self.identifier,
                self.epoch,
                require=lambda value: self._receipts(value, specification),
            ):
                self.checkpoint(journal={"deadline_at": time.time() + api.PREPARE_SECONDS + 300})
                remote_id = api.extract_job_id(
                    native.cloud_call(self.client.submit_job, specification)
                )
                self._write(_job(remote_id), drain=True)
                return
            self.checkpoint(
                message="Waiting for a free UCloud runtime allocation slot…", progress=0.15
            )
            self.worker.stopping.wait(api.POLL_SECONDS)

    def _settle_success(self, remote, deadline):
        promoted = verify_and_promote(
            self.store,
            self.identifier,
            self.epoch,
            self.client,
            deadline,
            stopping=self.worker.stopping,
        )
        self.value = preparation.get(self.store, self.identifier, owned=True)
        return promoted


def _job(remote_id):
    def change(value):
        journal = value["journal"]
        if (
            journal.get("job_id")
            or "generation_submission" not in journal
            or journal.get("submission_intent") is not True
            or type(remote_id) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]{1,200}", remote_id) is None
        ):
            raise Conflict("The Runtime generation submission reply is not recordable.")
        return {"job_id": remote_id}

    return change


def _root_inventory(client, root, deadline):
    inventory = preparation._model_inventory(client, root, deadline, strict=True)
    if inventory is None:
        raise observation.Refused()
    kinds = {item["name"]: item["kind"] for item in inventory}
    allowed = {**ROOT_REQUIRED, **ROOT_OPTIONAL}
    if not set(ROOT_REQUIRED) <= set(kinds) or any(
        allowed.get(name) != kind for name, kind in kinds.items()
    ):
        raise observation.Refused()
    return inventory


def verify_and_promote(store, owner, epoch, client, deadline, *, stopping=None):
    """The sole promotion: fresh exact-ID allocation and generation-root evidence only.

    Accepts no caller readiness payload. The writer rechecks the observed specification,
    folders, owner epoch/Stop and registry candidate, then completes the owner and publishes
    the active pointer in one transaction. Returns False when a durable Stop wins.
    """
    _require_enabled()
    value = preparation.get(store, owner, owned=True)
    if "generation" not in value["frozen"]:
        raise Conflict("Only a Runtime generation owner can be promoted.")
    if value["epoch"] != epoch:
        raise preparation.Interrupted()
    if value["state"] == "completed":
        return True  # Repeated acknowledgment; decoding validated its promotion receipt.
    journal, frozen = value["journal"], value["frozen"]
    if "generation_supersession" in journal:
        raise Conflict("A superseded Runtime generation is never promoted. Preserve its evidence.")
    if journal.get("stop_intent"):
        return False
    settings, generation, root = _owner(value)
    specification, job_id = journal.get("specification"), journal.get("job_id")
    if (
        not job_id
        or "generation_submission" not in journal
        or frozen["runtime_id"] != api.runtime_id()
        or generation["operation"]["recipe_sha256"] != preparation.recipe_hash()
    ):
        raise observation.Refused()
    run = api.run_folder(settings, owner)
    if observation.state(client, job_id, specification, deadline=deadline) != "SUCCESS":
        raise observation.Refused()
    status = observation.read_json(client, settings, run + "/supervisor.json", deadline=deadline)
    if status.get("phase") != "finished" or status.get("generation") != owner:
        raise observation.Refused()
    doctor = observation.read_json(client, settings, run + "/doctor.json", deadline=deadline)
    if doctor.get("ready") is not True or doctor.get("version") != api.WHISPERX_VERSION:
        raise observation.Refused()
    try:
        runtime_compute.require_gpu_memory(doctor)
    except ValueError:
        raise observation.Refused() from None
    plan = frozen["model_plan"]
    cache = observation.read_json(client, settings, run + "/cache.json", deadline=deadline)
    models = cache.get("models")
    if (
        type(models) is not dict
        or any(models.get(name) is not True for name in plan["models"])
        or cache.get("punkt_tab") is not True
    ):
        raise observation.Refused()
    inventory = _root_inventory(client, root, deadline)
    observation.stamp(client, settings, deadline=deadline, root=root)
    evidence = preparation._model_evidence(
        client, settings, plan, strict=True, deadline=deadline, root=root
    )
    if evidence is None:
        raise observation.Refused()
    receipt = {
        "format": preparation.MODEL_RECEIPT,
        "model_plan_sha256": _digest(plan),
        "runtime_id": frozen["runtime_id"],
        "recipe_sha256": frozen["recipe_sha256"],
        "evidence": evidence,
    }
    try:
        preparation._validate_model_receipt(receipt, frozen)
    except preparation.Conflict:
        raise observation.Refused() from None
    observed = {
        key: journal.get(key)
        for key in (
            "job_id",
            "specification",
            "generation_folders",
            "generation_submission",
            "owned_folder",
        )
    }
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        # BEGIN may wait for another writer: read Stop/epoch only after owning it.
        fresh = preparation._get(db, owner, owned=True)
        if fresh["epoch"] != epoch or (stopping is not None and stopping.is_set()):
            raise preparation.Interrupted()
        if not preparation._same(fresh["frozen"], frozen):
            raise Conflict("The Runtime generation owner changed. Preserve its evidence.")
        if fresh["state"] == "completed":
            return True
        if fresh["journal"].get("stop_intent"):
            return False
        if time.monotonic() >= deadline:
            raise observation.Refused()
        wall = fresh["journal"].get("deadline_at")
        if type(wall) in (int, float) and time.time() > wall:
            preparation._update(db, fresh, journal={"stop_intent": True})
            return False
        if fresh["state"] != "running" or any(
            not preparation._same(fresh["journal"].get(key), item) for key, item in observed.items()
        ):
            raise observation.Refused()
        submission = fresh["journal"]["generation_submission"]
        registry = generations._publish_active(
            db,
            generation["target"],
            owner_id=owner,
            token=generation["token"],
            operation_sha256=generation["operation_sha256"],
            compute=generation["operation"]["compute"],
        )
        promotion = {
            "format": PROMOTION_FORMAT,
            "token": generation["token"],
            "owner_id": owner,
            "target_sha256": generation["target"]["target_sha256"],
            "operation_sha256": generation["operation_sha256"],
            "job_id": job_id,
            "specification_sha256": submission["specification_sha256"],
            "folders_sha256": submission["folders_sha256"],
            "evidence_sha256": _digest(
                {"root": inventory, "doctor": doctor, "cache": cache, "models": receipt}
            ),
            "registry_revision": registry["revision"],
        }
        promoted = preparation._update(
            db,
            fresh,
            state="completed",
            progress=1,
            message="Runtime generation prepared, CUDA and caches checked; "
            "public run files await cleanup.",
            journal={
                "remote_state": "SUCCESS",
                "prepared": True,
                "doctor_verified": True,
                "model_plan_receipt": receipt,
                "generation_promotion": promotion,
                **preparation._release({**fresh["journal"], "remote_state": "SUCCESS"}),
            },
            _generation_writer=True,
        )
        if promoted["state"] != "completed":
            raise Conflict("The Runtime generation owner changed. Preserve its evidence.")
    return True


def active(store, target):
    """Verified active pointer for later stages; data only, never execution authority."""
    _require_enabled()
    base = generations.validate_target(target)
    with store.connection() as db:
        entry = generations.snapshot(db, base)
        pointer = entry["active"]
        if pointer is None:
            return None
        try:
            owner = preparation._get(db, pointer["owner_id"])
        except (KeyError, preparation.Conflict):
            owner = None
        generation = (owner or {}).get("frozen", {}).get("generation")
        promotion = (owner or {}).get("journal", {}).get("generation_promotion")
        if (
            owner is None
            or owner["archived"]
            or owner["state"] != "completed"
            or generation is None
            or promotion is None
            or generation["target"] != base
            or generation["token"] != pointer["token"]
            or generation["operation_sha256"] != pointer["operation_sha256"]
            or generation["operation"]["compute"] != pointer["compute"]
            or promotion["registry_revision"] != pointer["promoted_revision"]
        ):
            raise Conflict(
                "The active Runtime generation pointer has no matching promoted owner. "
                "Preserve its evidence."
            )
        settings = providers.Settings.model_validate(owner["frozen"]["settings"])
    return {
        "target_sha256": base["target_sha256"],
        "revision": entry["revision"],
        "token": pointer["token"],
        "owner_id": pointer["owner_id"],
        "operation_sha256": pointer["operation_sha256"],
        "root": generations.generation_root(settings, base, pointer["token"]),
    }

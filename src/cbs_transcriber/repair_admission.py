"""Selected-window source proof for older recordings, before any cloud allocation."""

import copy
import json
from pathlib import Path

from . import audio_inputs
from .media import Cancelled

POLICY = "selected-original-v1"


def choice(recording, units, anchor=None, *, epoch=1):
    """Metadata only. The worker anchors bytes before its bounded timeline probe."""
    extension = Path(recording["filename"]).suffix.lower().lstrip(".")
    if extension not in audio_inputs._FORMATS:
        raise ValueError("Selected original repair requires a supported recording format.")
    return validate(
        {
            "policy": POLICY,
            "recording_id": recording["id"],
            "format": extension,
            "duration": recording["duration"],
            "windows": copy.deepcopy(units),
            "anchor": copy.deepcopy(anchor),
            "epoch": epoch,
        }
    )


def validate(value):
    if (
        not isinstance(value, dict)
        or set(value)
        != {"policy", "recording_id", "format", "duration", "windows", "anchor", "epoch"}
        or value["policy"] != POLICY
        or not isinstance(value["recording_id"], str)
        or not audio_inputs._ID.fullmatch(value["recording_id"])
        or not isinstance(value["format"], str)
        or value["format"] not in audio_inputs._FORMATS
        or not audio_inputs._number(value["duration"])
        or value["duration"] <= 0
        or not isinstance(value["windows"], list)
        or not 1 <= len(value["windows"]) <= 200
        or type(value["epoch"]) is not int
        or not 1 <= value["epoch"] <= 2**53
    ):
        raise ValueError("The selected original repair admission is invalid.")
    ids = []
    for window in value["windows"]:
        if (
            not isinstance(window, dict)
            or set(window) != {"ids", "start", "end"}
            or not isinstance(window["ids"], list)
            or not window["ids"]
            or any(not isinstance(i, str) or not 0 < len(i) <= 100 for i in window["ids"])
            or not all(audio_inputs._number(window[k]) for k in ("start", "end"))
            or not 0 <= window["start"] < window["end"] <= value["duration"]
            or not 0 < round(window["end"] * 16000) - round(window["start"] * 16000) <= 960000
        ):
            raise ValueError("The selected original repair window is invalid.")
        ids.extend(window["ids"])
    if len(ids) > 1000 or len(ids) != len(set(ids)):
        raise ValueError("The selected original repair passages are ambiguous.")
    anchor = value["anchor"]
    if anchor is not None and (
        not isinstance(anchor, dict)
        or set(anchor) != {"sha256", "bytes", "format"}
        or not audio_inputs._sha(anchor["sha256"])
        or type(anchor["bytes"]) is not int
        or not 0 < anchor["bytes"] <= audio_inputs.MAX_BYTES
        or anchor["format"] != value["format"]
    ):
        raise ValueError("The selected original repair byte anchor is invalid.")
    return copy.deepcopy(value)


def require_snapshot(params):
    """Scoped proof grants only these selected clips; it never becomes source-ready cache."""
    accepted = validate(params["repair_source"])
    frozen = audio_inputs.validate(params["audio_input"])
    if (
        frozen["duration_basis"] != "container_observation"
        or frozen["recording_id"] != accepted["recording_id"]
        or frozen["duration"] != accepted["duration"]
        or accepted["anchor"] != {k: frozen[k] for k in ("sha256", "bytes", "format")}
        or params.get("timing_units") != accepted["windows"]
        or params.get("realign") is not True
        or params.get("passage_timing") is not True
        or (params.get("segment_ids") or [params.get("segment_id")])
        != [identifier for unit in accepted["windows"] for identifier in unit["ids"]]
    ):
        raise ValueError("The bounded source proof differs from this repair's accepted selection.")
    return frozen


def prepare(worker, job, recording):
    """Hash/probe outside locks, preserving the first byte anchor across failures and Stop."""
    from . import repair_cloud, restored_owners

    restored_owners.require_job(worker.store, job["id"])

    accepted = validate(job["params"]["repair_source"])
    if "audio_input" in job["params"]:
        require_snapshot(job["params"])
        return job
    if (
        choice(recording, accepted["windows"], accepted["anchor"], epoch=accepted["epoch"])
        != accepted
    ):
        raise ValueError("The accepted selected repair recording metadata changed.")

    def publish(*, anchor=None, snapshot=None):
        nonlocal accepted
        with worker.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            restored_owners.require_owned(db, "job", job["id"])
            row = db.execute("SELECT state,params FROM jobs WHERE id=?", (job["id"],)).fetchone()
            params = json.loads(row["params"])
            if (
                row["state"] not in ({"running", "cancelled"} if anchor else {"running"})
                or repair_cloud.identity(params) != repair_cloud.identity(job["params"])
                or params.get("ucloud_run")
                or params.get("repair_source") != accepted
                or "audio_input" in params
            ):
                raise ValueError("The selected repair changed while its original was checked.")
            if anchor is not None:
                if accepted["anchor"] is not None and accepted["anchor"] != anchor:
                    raise ValueError("The selected repair original changed after its byte anchor.")
                accepted = {**accepted, "anchor": copy.deepcopy(anchor)}
                params["repair_source"] = accepted
            else:
                params["audio_input"] = snapshot
                require_snapshot(params)
            db.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))
            job["params"] = params

    snapshot = audio_inputs.freeze(
        worker.store,
        recording,
        worker.cancel_event,
        scoped=True,
        identity_observer=lambda anchor: publish(anchor=anchor),
    )
    if worker.cancel_event.is_set():
        raise Cancelled()
    publish(snapshot=snapshot)
    return worker.store.job(job["id"])

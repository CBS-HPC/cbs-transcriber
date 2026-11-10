"""Independent local media/API workers with durable part checkpoints."""

import itertools
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path

from . import (
    audio_inputs,
    input_admission,
    local_concurrency,
    media,
    original_inspection,
    provider_capture,
    providers,
)
from .store import now
from .transcripts import combine_chunks

# Settings rows 1-6 hold provider, local enhancement, cloud, routing and editing settings.
ACTIVITY_SETTING = 7
# An idle Runtime lane marks its brief capacity observation, which may write one old owner's
# receipt outside the claim lock. Captures pause new observations and wait for this one.
CAPACITY_OBSERVATION = "runtime-capacity-observation"
OBSERVATION_SETTLE_SECONDS = 45
# Queue worker kinds whose new claims follow Settings → Local Processing (ADR 0146).
LOCAL_LANES = {kind: lane for lane, (kind, _) in local_concurrency.LANES.items()}


class JobControlConflict(ValueError):
    """An earlier Stop/Retry still owns its local or remote control request."""


@contextmanager
def preparation_slot(gate, guard):
    """Waiting for another file's local I/O must remain cancellable."""
    if gate is None:
        guard()
        yield
        return
    while not gate.acquire(timeout=0.2):
        guard()
    try:
        guard()
        yield
    finally:
        gate.release()


def _activity_value(db):
    row = db.execute("SELECT value FROM settings WHERE id=?", (ACTIVITY_SETTING,)).fetchone()
    try:
        value = json.loads(row["value"]) if row else {}
    except ValueError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    hidden = value.get("hidden")
    value["hidden"] = (
        {key: stamp for key, stamp in hidden.items() if isinstance(stamp, str)}
        if isinstance(hidden, dict)
        else {}
    )
    return value


def _write_activity(db, value):
    db.execute(
        "INSERT INTO settings(id,value) VALUES (?,?) "
        "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
        (ACTIVITY_SETTING, json.dumps(value)),
    )


def activity_state(store):
    """Activity view state: Clear finished time (V105) and per-card removals (V162)."""
    with store.connection() as db:
        value = _activity_value(db)
    return {"cleared_at": value.get("cleared_at"), "hidden": value["hidden"]}


def activity_cleared_at(store):
    """When Activity was last cleared; it hides finished jobs only, never deletes (V105)."""
    return activity_state(store)["cleared_at"]


def clear_activity(store):
    # One writer transaction keeps per-card removals made concurrently (V162).
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        value = _activity_value(db)
        value["cleared_at"] = now()
        _write_activity(db, value)
    return activity_cleared_at(store)


# Per-card removals are bounded view state; the oldest removals are forgotten first.
ACTIVITY_HIDDEN_LIMIT = 1000
ACTIVITY_GROUP_LIMIT = 1000
# Group identities come from durable admission/import receipts. Follow-ups join only
# through their exact producing result, never names, timestamps or current settings.
_ACTIVITY_CTE = """
WITH fields AS (
 SELECT j.id,j.recording_id,j.kind,j.state,j.updated,r.project_id,
 json_extract(CASE WHEN json_valid(j.params) THEN j.params END,'$.upload_batch') AS upload_batch,
 json_extract(CASE WHEN json_valid(j.params) THEN j.params END,
 '$.input_intent.request_id') AS intent,
 CASE WHEN json_extract(CASE WHEN json_valid(j.params) THEN j.params END,
 '$.input_acceptance.version')=1 THEN
 json_extract(j.params,'$.input_acceptance.request_id') END AS accepted,
 json_extract(CASE WHEN json_valid(j.params) THEN j.params END,
 '$.parent_transcription_id') AS parent,
 json_extract(CASE WHEN json_valid(j.params) THEN j.params END,'$.source_snapshot.id') AS source,
 COALESCE(json_extract(CASE WHEN json_valid(j.params) THEN j.params END,'$.native_batch_id'),
 CASE WHEN j.kind='transcribe' AND json_type(CASE WHEN json_valid(j.params) THEN j.params END,
 '$.input_intent') IS NULL AND json_extract(CASE WHEN json_valid(j.params) THEN j.params END,
 '$.settings.provider_type')='ucloud' THEN j.id END) AS native_owner,
 json_extract(CASE WHEN json_valid(j.params) THEN j.params END,
 '$.runtime_batch_id') AS runtime_owner,
 CASE WHEN json_valid(r.original_input) THEN
 json_extract(r.original_input,'$.inspection_job_id') END AS inspection_job_id
 FROM jobs j JOIN recordings r ON r.id=j.recording_id
), identities AS MATERIALIZED (
 SELECT f.*,COALESCE(intent,accepted,(SELECT p.accepted FROM fields p
 JOIN transcript_results t ON t.job_id=p.id AND t.id=f.source AND t.recording_id=f.recording_id
 WHERE p.id=f.parent AND p.kind='transcribe' AND p.recording_id=f.recording_id)) AS request_id,
 CASE WHEN f.kind='source-inspect' AND f.id=f.inspection_job_id AND
 (SELECT count(*) FROM fields m WHERE m.kind='media' AND m.recording_id=f.recording_id)=1
 THEN (SELECT m.upload_batch FROM fields m WHERE m.kind='media'
 AND m.recording_id=f.recording_id LIMIT 1) END AS inspection_upload_batch
 FROM fields f
)
"""
_ACTIVITY_SQL = _ACTIVITY_CTE + "SELECT * FROM identities"


def _activity_key(row):
    if row["kind"] == "media":
        kind, identifier = "upload", row["upload_batch"]
    elif row["kind"] == "source-inspect":
        kind, identifier = "inspection", row["inspection_upload_batch"]
    elif row["native_owner"] or row["runtime_owner"]:
        return None
    elif row["kind"] not in {"transcribe", "enhance"}:
        return None
    else:
        kind, identifier = "request", row["request_id"]
    if isinstance(identifier, str) and re.fullmatch(r"[0-9a-f]{32}", identifier):
        return kind, row["project_id"], identifier
    return None


def _activity_members(store, key):
    """Bound display/authority together; oversized imports remain explicitly incomplete."""
    from . import restored_owners

    kind, project_id, identifier = key
    field = {
        "upload": "upload_batch",
        "inspection": "inspection_upload_batch",
        "request": "request_id",
    }[kind]
    extra = " AND kind='media'" if kind == "upload" else ""
    if kind == "request":
        extra = (
            " AND kind IN ('transcribe','enhance')"
            " AND native_owner IS NULL AND runtime_owner IS NULL"
        )
    with store.connection() as db:
        exclusion = restored_owners.sql_exclusion(db, "job", "identities.id")
        rows = db.execute(
            _ACTIVITY_SQL
            + f" WHERE project_id=? AND {field}=?"
            + extra
            + f" AND {exclusion} ORDER BY id LIMIT ?",
            (project_id, identifier, ACTIVITY_GROUP_LIMIT + 1),
        ).fetchall()
    return [dict(row) for row in rows]


def activity_listing(store, rows, *, connection=None):
    """Expand selected cards before the recent-row cutoff, retaining every visible sibling."""
    from . import restored_owners

    identifiers = {row["id"] for row in rows}
    metadata, groups = {}, {}
    with store.connection() if connection is None else nullcontext(connection) as db:
        if connection is None:
            db.execute("BEGIN")
        exclusion = restored_owners.sql_exclusion(db, "job", "identities.id")
        candidates = db.execute(
            _ACTIVITY_SQL + " WHERE id IN (SELECT value FROM json_each(?)) " + f"AND {exclusion}",
            (json.dumps(list(identifiers)),),
        ).fetchall()
        for row in candidates:
            key = _activity_key(row)
            if key:
                groups.setdefault(key, []).append(dict(row))
        if not groups:
            return [], {}
        exclusion = restored_owners.sql_exclusion(db, "job", "i.id")
        # One scan for the selected groups, rather than re-reading the full job history
        # once per card on every poll. Exclude archival rows before ranking/bounding.
        members = db.execute(
            _ACTIVITY_CTE
            + """, wanted AS MATERIALIZED (
                SELECT json_extract(value,'$[0]') AS kind,
                json_extract(value,'$[1]') AS project_id,
                json_extract(value,'$[2]') AS group_id FROM json_each(?)
            ), scopes AS MATERIALIZED (
                SELECT i.*,CASE WHEN kind='media' THEN 'upload'
                WHEN kind='source-inspect' THEN 'inspection'
                WHEN kind IN ('transcribe','enhance') AND native_owner IS NULL
                    AND runtime_owner IS NULL THEN 'request' END AS group_kind,
                CASE WHEN kind='media' THEN upload_batch WHEN kind='source-inspect'
                THEN inspection_upload_batch ELSE request_id END AS group_id
                FROM identities i
            ), ranked AS (
                SELECT i.*,
                row_number() OVER (PARTITION BY w.kind,w.project_id,w.group_id ORDER BY i.id)
                AS group_ordinal FROM scopes i JOIN wanted w ON i.project_id=w.project_id
                AND i.group_kind=w.kind AND i.group_id=w.group_id
                WHERE """
            + exclusion
            + ") SELECT * FROM ranked WHERE group_ordinal<=?",
            (json.dumps(list(groups)), ACTIVITY_GROUP_LIMIT + 1),
        ).fetchall()
    observed = dict.fromkeys(groups)
    for row in members:
        key = row["group_kind"], row["project_id"], row["group_id"]
        if observed[key] is None:
            observed[key] = []
        observed[key].append(dict(row))
    extra_ids = set()
    for key, members in observed.items():
        members = members or []
        complete = len(members) <= ACTIVITY_GROUP_LIMIT
        for row in members[:ACTIVITY_GROUP_LIMIT]:
            metadata[row["id"]] = {
                "activity_membership_complete": complete,
                "activity_group_limit": ACTIVITY_GROUP_LIMIT,
            }
            if key[0] == "request":
                metadata[row["id"]].update(
                    activity_request_id=key[2], activity_parent_job_id=row["parent"]
                )
            if row["id"] not in identifiers:
                extra_ids.add(row["id"])
        # A selected row beyond the display bound must also carry the refusal marker.
        for row in groups[key]:
            metadata.setdefault(row["id"], {}).update(
                activity_membership_complete=complete,
                activity_group_limit=ACTIVITY_GROUP_LIMIT,
            )
            if key[0] == "request":
                metadata[row["id"]].update(
                    activity_request_id=key[2], activity_parent_job_id=row["parent"]
                )
    return sorted(extra_ids), metadata


def activity_inspection_links(db, rows, preparations):
    """Project current, routine inspections into their exact consuming request.

    This is display metadata only: inspection lifecycle and control tokens remain independent.
    Invalid/restored evidence and upload-group inspections retain their own Activity cards.
    """
    from . import restored_owners

    candidates = [row for row in rows if row["kind"] in {"transcribe", "enhance"}]
    if not candidates:
        return [], {}
    by_id = {row["id"]: row for row in candidates}
    identifiers = list(by_id)
    safe = "CASE WHEN json_valid(params) THEN params ELSE 'null' END"
    observed = db.execute(
        "SELECT id,recording_id,"
        f"json_type({safe},'$.input_intent') AS intent_type,"
        f"json_type({safe},'$.input_acceptance') AS accepted_type,"
        f"json_type({safe},'$.input_acceptance.version') AS version_type,"
        f"json_extract({safe},'$.input_acceptance.version') AS accepted_version,"
        f"json_extract({safe},'$.input_acceptance.generation') AS generation,"
        f"json_extract({safe},'$.input_acceptance.request_id') AS request_id,"
        f"json_extract({safe},'$.input_acceptance.receipt_sha256') AS receipt_sha256,"
        f"json_type({safe},'$.audio_input') AS audio_type,"
        f"CASE WHEN length(CAST(json_extract({safe},'$.audio_input') AS BLOB))<=65536 "
        f"THEN json_extract({safe},'$.audio_input') END AS audio_input "
        "FROM jobs WHERE id IN (SELECT value FROM json_each(?)) "
        f"AND (json_type({safe},'$.input_intent') IS NOT NULL OR "
        f"json_type({safe},'$.input_acceptance') IS NOT NULL)",
        (json.dumps(identifiers),),
    ).fetchall()
    caches, shared, links, by_request = {}, {}, {}, {}
    for row in observed:
        if restored_owners.is_restored(db, "job", row["id"]):
            continue
        try:
            if row["recording_id"] not in caches:
                caches[row["recording_id"]] = original_inspection._load(db, row["recording_id"])
            cache = caches[row["recording_id"]]
            if not cache or cache["state"] not in {"pending", "running", "ready"}:
                continue
            if restored_owners.is_restored(db, "job", cache["inspection_job_id"]):
                continue
            if row["intent_type"] is not None:
                if preparations.get(row["id"]) is None:
                    continue
                intent = input_admission._load(db, row["id"])["intent"]
                generation, request_id = intent["generation"], intent["request_id"]
            else:
                if (
                    row["accepted_type"] != "object"
                    or row["version_type"] != "integer"
                    or row["accepted_version"] != 1
                    or cache["state"] != "ready"
                    or row["receipt_sha256"] != cache["receipt_sha256"]
                    or row["audio_type"] != "object"
                    or not isinstance(row["audio_input"], str)
                ):
                    continue
                frozen = audio_inputs.validate(json.loads(row["audio_input"]))
                if input_admission._canonical(frozen) != input_admission._canonical(
                    cache["receipt"]
                ):
                    continue
                generation, request_id = row["generation"], row["request_id"]
            if (
                generation != cache["generation"]
                or not input_admission._identifier(request_id)
                or not input_admission._identifier(generation)
            ):
                continue
            uploads = db.execute(
                "SELECT id,CASE WHEN json_valid(params) "
                "THEN json_extract(params,'$.upload_batch') END AS upload_batch "
                "FROM jobs WHERE kind='media' AND recording_id=? LIMIT 2",
                (row["recording_id"],),
            ).fetchall()
            if len(uploads) > 1 or (
                uploads
                and isinstance(uploads[0]["upload_batch"], str)
                and re.fullmatch(r"[0-9a-f]{32}", uploads[0]["upload_batch"])
            ):
                continue
            if row["recording_id"] not in shared:
                # Inspect all same-source consumers, including hidden and older-than-200 jobs.
                # If the history is too large or malformed, leave its inspector independent.
                owner_safe = "CASE WHEN json_valid(j.params) THEN j.params ELSE 'null' END"
                exclusion = restored_owners.sql_exclusion(db, "job", "j.id")
                refs = db.execute(
                    "SELECT j.id,j.state,json_valid(j.params) AS valid,"
                    f"json_type({owner_safe},'$.input_intent') AS intent_type,"
                    f"json_type({owner_safe},'$.input_acceptance') AS accepted_type,"
                    f"COALESCE(json_extract({owner_safe},'$.input_intent.generation'),"
                    f"json_extract({owner_safe},'$.input_acceptance.generation')) AS generation,"
                    f"COALESCE(json_extract({owner_safe},'$.input_intent.request_id'),"
                    f"json_extract({owner_safe},'$.input_acceptance.request_id')) AS request_id "
                    "FROM jobs j WHERE j.recording_id=? AND j.kind IN ('transcribe','enhance') "
                    f"AND {exclusion} AND ("
                    "(NOT json_valid(j.params) AND j.state IN ('queued','running')) OR "
                    f"json_type({owner_safe},'$.input_intent') IS NOT NULL OR "
                    f"json_type({owner_safe},'$.input_acceptance') IS NOT NULL) LIMIT ?",
                    (row["recording_id"], ACTIVITY_GROUP_LIMIT + 1),
                ).fetchall()
                requests, unknown = set(), len(refs) > ACTIVITY_GROUP_LIMIT
                for ref in refs[:ACTIVITY_GROUP_LIMIT]:
                    if not ref["valid"]:
                        unknown = True
                    elif ref["generation"] == cache["generation"]:
                        if input_admission._identifier(ref["request_id"]):
                            requests.add(ref["request_id"])
                        else:
                            unknown = True
                    elif ref["state"] in {"queued", "running"} and (
                        ref["intent_type"] not in (None, "object")
                        or ref["accepted_type"] not in (None, "object")
                        or not input_admission._identifier(ref["generation"])
                    ):
                        unknown = True
                shared[row["recording_id"]] = unknown or len(requests) > 1
            links[row["id"]] = {
                "job_id": cache["inspection_job_id"],
                "generation": generation,
                "request_id": request_id,
                "shared": shared[row["recording_id"]],
            }
            by_request.setdefault((by_id[row["id"]]["project_id"], request_id), set()).add(
                row["recording_id"]
            )
        except (ValueError, KeyError, TypeError, RecursionError, OverflowError):
            continue
    # Only a multi-recording request can own a combined batch presentation.
    links = {
        identifier: link
        for identifier, link in links.items()
        if len(by_request.get((by_id[identifier]["project_id"], link["request_id"]), ())) > 1
    }
    return sorted({link["job_id"] for link in links.values()}), links


def _request_entries(store, key):
    members = _activity_members(store, key)
    if len(members) > ACTIVITY_GROUP_LIMIT:
        raise ActivityRetained(
            "This request exceeds the Activity display limit; use individual file controls."
        )
    entries = {}
    for row in members:
        # Disable regrouping while retaining every member's existing obligation checks.
        entries.update(_job_card_entries(store, row["id"], grouped=False))
    return entries


RUNTIME_CARD_PREFIX = "runtime-batch-"
_STILL_WORKING = "Wait until this work finishes before removing it from Activity."
_REMOTE_OPEN = (
    "This job still has UCloud work to resolve (stop, reconnect or remote cleanup), "
    "so it stays in Activity."
)
_UNVERIFIED = "Preparation evidence could not be verified, so this item stays in Activity."
_SELECTION_OPEN = (
    "This file belongs to a UCloud selection that is not resolved yet, so it stays in Activity."
)


class ActivityRetained(ValueError):
    """The card still has active work or an unresolved obligation, so it stays visible."""


def _remote_open(run, state):
    """Mirror the /api/jobs retention conditions for one job's own UCloud journal."""
    from .native_batch import TERMINAL, uncertain

    if not isinstance(run, dict):
        return False
    return run.get("cleanup_state") == "pending" or (
        state != "completed"
        and (
            uncertain(run)
            or (
                run.get("job_id") is not None
                and not run.get("termination_requested")
                and not run.get("cancellation_requested")
                and run.get("remote_state") not in TERMINAL
            )
        )
    )


def _runtime_card_entries(store, identifier):
    from . import restored_owners, runtime_store

    batch_id = identifier.removeprefix(RUNTIME_CARD_PREFIX)
    with store.connection() as db:
        # Read the update time first: any later change makes this removal stale (shown again).
        row = db.execute(
            "SELECT project_id,state,updated FROM runtime_batch_runs WHERE id=?", (batch_id,)
        ).fetchone()
        if row is None:
            raise KeyError(identifier)
        if restored_owners.is_restored(db, "runtime_batch", batch_id):
            return {identifier: str(row["updated"])[:80]}
    if row["state"] in {"queued", "running"}:
        raise ActivityRetained(_STILL_WORKING)
    if any(b["id"] == batch_id for b in runtime_store.obligations(store, row["project_id"])):
        raise ActivityRetained(_REMOTE_OPEN)
    return {identifier: row["updated"]}


def _upload_entries(store, upload_batch, project_id):
    """Files added together are one Media preparation card; it hides only as a whole."""

    rows = _activity_members(store, ("upload", project_id, upload_batch))
    if len(rows) > ACTIVITY_GROUP_LIMIT:
        raise ActivityRetained(
            "This upload exceeds the Activity display limit; use individual file controls."
        )
    if any(row["state"] in {"queued", "running"} for row in rows):
        raise ActivityRetained(_STILL_WORKING)
    return {row["id"]: row["updated"] for row in rows}


def inspection_upload_group(store, recording_id):
    """Use only the recording's unique owned upload receipt, never inspection times."""
    from . import restored_owners

    with store.connection() as db:
        rows = db.execute(
            "SELECT j.id,r.project_id,CASE WHEN json_valid(j.params) THEN "
            "json_extract(j.params,'$.upload_batch') END AS upload_batch "
            "FROM jobs j JOIN recordings r ON r.id=j.recording_id "
            "WHERE j.kind='media' AND j.recording_id=? LIMIT 2",
            (recording_id,),
        ).fetchall()
        if len(rows) != 1 or restored_owners.is_restored(db, "job", rows[0]["id"]):
            return None
    batch = rows[0]["upload_batch"]
    if not isinstance(batch, str) or not re.fullmatch(r"[0-9a-f]{32}", batch):
        return None
    return rows[0]["project_id"], batch


def _inspection_entries(store, recording_id, cache):
    """One upload's current verified inspections hide together, retaining each generation."""
    group = inspection_upload_group(store, recording_id)
    recording_ids = [recording_id]
    if group:
        members = _activity_members(store, ("inspection", *group))
        if len(members) > ACTIVITY_GROUP_LIMIT:
            raise ActivityRetained("This inspection group exceeds the Activity display limit.")
        recording_ids = [
            row["recording_id"]
            for row in members
            if inspection_upload_group(store, row["recording_id"]) == group
        ]
    entries = {}
    for member_id in recording_ids:
        try:
            member = (
                cache if member_id == recording_id else original_inspection.read(store, member_id)
            )
        except (ValueError, KeyError):
            # Unverifiable inspections stay as separate, non-removable cards.
            continue
        if member is None:
            continue
        if member["state"] in {"pending", "running"}:
            raise ActivityRetained(_STILL_WORKING)
        row = store.one("SELECT updated FROM jobs WHERE id=?", (member["inspection_job_id"],))
        entries[member["inspection_job_id"]] = row["updated"]
    return entries


def _job_card_entries(store, identifier, *, grouped=True):
    from . import native_batch, restored_owners, runtime_store

    with store.connection() as db:
        row = db.execute(
            "SELECT id,recording_id,kind,state,updated,"
            "CASE WHEN json_valid(params) THEN params END AS params FROM jobs WHERE id=?",
            (identifier,),
        ).fetchone()
        if row is None:
            raise KeyError(identifier)
        if restored_owners.is_restored(db, "job", identifier):
            # Imported history is inert and shown read-only; it owns no allocation.
            return {identifier: str(row["updated"])[:80]}
    if row["state"] in {"queued", "running"}:
        raise ActivityRetained(_STILL_WORKING)
    params = json.loads(row["params"]) if row["params"] is not None else None
    if not isinstance(params, dict):
        raise ActivityRetained(_UNVERIFIED)
    if grouped:
        observed = store.all(_ACTIVITY_SQL + " WHERE id=?", (identifier,))
        key = _activity_key(observed[0]) if observed else None
        if key and key[0] == "request":
            return _request_entries(store, key)
    if "input_intent" in params:
        try:
            projection = input_admission.projection(store, identifier)
        except (ValueError, KeyError):
            raise ActivityRetained(_UNVERIFIED) from None
        if projection is None:
            raise ActivityRetained("This preparation changed. Refresh Activity and try again.")
        if projection["phase"] != "terminal":
            raise ActivityRetained(_STILL_WORKING)
        if not projection["native_group_resolved"]:
            raise ActivityRetained(_SELECTION_OPEN)
        return {identifier: row["updated"]}
    if row["kind"] == "source-inspect":
        try:
            cache = original_inspection.read(store, row["recording_id"])
        except (ValueError, KeyError):
            cache = None
        if cache is None or cache["inspection_job_id"] != identifier:
            raise ActivityRetained(_UNVERIFIED)
        if cache["state"] in {"pending", "running"}:
            raise ActivityRetained(_STILL_WORKING)
        return _inspection_entries(store, row["recording_id"], cache)
    if row["kind"] == "media" and isinstance(params.get("upload_batch"), str):
        project_id = store.recording(row["recording_id"])["project_id"]
        return _upload_entries(store, params["upload_batch"], project_id)
    if _remote_open(params.get("ucloud_run"), row["state"]):
        raise ActivityRetained(_REMOTE_OPEN)
    job = {**dict(row), "params": params}
    if params.get("native_batch_id"):
        # One card per shared allocation: it hides only as a whole, keyed by the batch time
        # that card shows, recorded for every member so any member may represent it.
        batch = native_batch.batch_for_job(store, job)
        if native_batch.unresolved(batch):
            raise ActivityRetained(_REMOTE_OPEN)
        members = [member["job_id"] for member in batch["members"]]
        busy = store.all(
            "SELECT id FROM jobs WHERE state IN ('queued','running') AND id IN ("
            + ",".join("?" for _ in members)
            + ")",
            members,
        )
        if busy:
            raise ActivityRetained(_STILL_WORKING)
        return dict.fromkeys(members, batch["updated"])
    runtime = runtime_store.for_job(store, identifier)
    if runtime and any(
        b["id"] == runtime["id"]
        for b in runtime_store.obligations(store, recording_ids=[row["recording_id"]])
    ):
        raise ActivityRetained(_REMOTE_OPEN)
    return {identifier: row["updated"]}


def hide_activity(store, identifier):
    """Remove one finished card from the Activity list; no job, journal or result changes.

    Each hidden job keeps the update time it had, so any later change shows it again (V162).
    """
    entries = (
        _runtime_card_entries(store, identifier)
        if identifier.startswith(RUNTIME_CARD_PREFIX)
        else _job_card_entries(store, identifier)
    )
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        value = _activity_value(db)
        hidden = {key: stamp for key, stamp in value["hidden"].items() if key not in entries}
        hidden.update(entries)
        present = {
            row[0]
            for row in db.execute(
                "SELECT k.value FROM json_each(?) k WHERE k.value IN (SELECT id FROM jobs) "
                "OR k.value IN (SELECT ? || id FROM runtime_batch_runs)",
                (json.dumps(list(hidden)), RUNTIME_CARD_PREFIX),
            )
        }
        kept = [key for key in hidden if key in present][-ACTIVITY_HIDDEN_LIMIT:]
        value["hidden"] = {key: hidden[key] for key in kept}
        _write_activity(db, value)
    return {"cleared_at": value.get("cleared_at"), "hidden": value["hidden"]}


def completed_audio_plan(job):
    """Accepted complete API checkpoints need no source, decoder or new upload permission."""
    params = job["params"]
    if "audio_input" not in params or not job["chunks"]:
        return None
    receipts = params.get("audio_preparations")
    if not isinstance(receipts, list) or len(receipts) != 1:
        raise ValueError("The completed audio parts have no unique frozen preparation receipt.")
    plan = audio_inputs.validate_evidence(receipts[0], params["audio_input"])
    if "chunks" not in plan or plan["transport"] != params.get("audio_transport"):
        raise ValueError("The completed audio parts differ from the frozen transport.")
    found = set()
    for part in job["chunks"]:
        index = part.get("index")
        if (
            type(index) is not int
            or not 0 <= index < len(plan["chunks"])
            or index in found
            or type(part.get("offset")) not in (int, float)
            or part["offset"] != plan["chunks"][index]["offset"]
            or not isinstance(part.get("segments"), list)
        ):
            raise ValueError("The saved audio part does not match its frozen preparation scope.")
        found.add(index)
    return plan["chunks"] if found == set(range(len(plan["chunks"]))) else None


class JobQueue:
    def __init__(self, store, credentials, *, kind=None, lock=None, concurrency=None):
        self.store, self.credentials = store, credentials
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self.cancel_event = threading.Event()
        self.active_id = None
        self.active_batch_id = None
        self.active_ids = []
        self.capture_paused = False
        self.lock = lock or threading.RLock()
        self.thread = None
        # A standalone worker of kind "transcribe" claims every API and native job.
        self.kind = kind or "transcribe-api"
        self.workers = [self]
        self.coordinator = self
        self.control_job_ids = set()
        self.runtime_preparation_gate = threading.BoundedSemaphore(2)
        self.runtime_transfer_gate = threading.BoundedSemaphore(2)
        # Coordinator-owned claim state (ADR 0146), read and written under the claim lock.
        self.lane_limits = {}
        self.lane_polls = {}
        self._notices = itertools.count(1)
        self.notice = 0
        if kind is None:
            # Each local lane starts its maximum number of threads; its saved (or automatic)
            # limit gates new claims only, so Settings changes apply without a restart.
            # Native UCloud Transcriber keeps two concurrent allocations. UCloud enhancement
            # mostly waits on remote work, so it has its own lane and never holds up local
            # enhancement or passage repairs (ADR 0049). CBS Runtime capacity belongs to
            # durable owners, independently of its four coordinator threads (ADR 0065).
            if concurrency is None:
                concurrency = local_concurrency.saved(store)
            self.lane_limits = {
                local_concurrency.LANES[lane][0]: count
                for lane, count in local_concurrency.effective(concurrency).items()
            }
            maximum = {kind: limit for kind, limit in local_concurrency.LANES.values()}
            self.workers.extend(
                JobQueue(store, credentials, kind=worker_kind, lock=self.lock)
                for worker_kind in (
                    ["transcribe-api"] * (maximum["transcribe-api"] - 1)
                    + ["transcribe-native"] * local_concurrency.NATIVE_WORKERS
                    + ["media"] * maximum["media"]
                    + ["enhance", "enhance-remote", "runtime-prepare"]
                    + ["source-inspect"] * maximum["source-inspect"]
                    + ["input-admission"] * maximum["input-admission"]
                    + ["runtime-batch"] * 4
                )
            )
            for worker in self.workers:
                worker.coordinator = self
                worker.runtime_preparation_gate = self.runtime_preparation_gate
                worker.runtime_transfer_gate = self.runtime_transfer_gate

    def start(self):
        with self.lock:
            if any(worker.thread and worker.thread.is_alive() for worker in self.workers):
                raise RuntimeError("Processing workers are already running.")
            # Failed startup drains every lane. A deferred-recovery retry may safely
            # start that same queue again only after all earlier threads have stopped.
            for worker in self.workers:
                worker.stopping.clear()
                worker.cancel_event.clear()
        for row in self.store.all(
            "SELECT id,kind,json_type("
            "CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
            "'$.input_intent') AS intent FROM jobs WHERE state='running'"
        ):
            try:
                if row["kind"] == "source-inspect":
                    original_inspection.recover(self.store, job_ids=[row["id"]])
                elif row["intent"] is not None:
                    input_admission.recover(self.store, job_ids=[row["id"]])
            except (ValueError, KeyError):
                pass  # Preserve invalid proof; do not strand healthy siblings behind it.
        self.store.recover_jobs()
        from . import runtime_preparation

        runtime_preparation.recover(self.store)
        try:
            for worker in self.workers:
                worker._start()
        except BaseException as error:
            # Partial startup still owns accepted workers and their borrowed library lease.
            try:
                self.stop()
            except BaseException as cleanup:
                raise error from cleanup
            raise

    def _start(self):
        self.thread = threading.Thread(target=self.run, name=self.kind + "-queue", daemon=True)
        self.thread.start()

    def stop(self):
        with self.lock:
            for worker in self.workers:
                worker.stopping.set()
                worker.cancel_event.set()
                worker.wakeup.set()
        for worker in self.workers:
            if worker.thread and worker.thread.ident is not None:
                # Keep the lease until every accepted request has checkpointed or failed.
                # Thread.start() may have refused a later worker before it was accepted.
                worker.thread.join()
        # A runtime-close failure must not skip another worker's join or runtime cleanup.
        with ExitStack() as runtimes:
            for worker in self.workers:
                if getattr(worker, "passage_runtime", None):
                    runtimes.callback(worker.passage_runtime.close)

    @contextmanager
    def pause_admission(self):
        coordinator = self.coordinator
        with self.lock:
            if coordinator.capture_paused or any(
                worker.active_id
                or worker.active_batch_id
                or any(item != CAPACITY_OBSERVATION for item in worker.active_ids)
                for worker in coordinator.workers
            ):
                raise ValueError("Wait for active work to finish before capturing the library.")
            coordinator.capture_paused = True
        try:
            # An otherwise idle library is not refused because a Runtime lane happened to be
            # observing capacity. The pause prevents new observations and claims; the capture
            # only starts after every current observation has left its unlocked section.
            deadline = time.monotonic() + OBSERVATION_SETTLE_SECONDS
            while True:
                with self.lock:
                    observing = any(
                        CAPACITY_OBSERVATION in worker.active_ids for worker in coordinator.workers
                    )
                if not observing:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "Runtime capacity is still being checked. Try the backup again shortly."
                    )
                time.sleep(0.05)
            yield
        finally:
            with self.lock:
                coordinator.capture_paused = False
            coordinator.notify()

    def notify(self):
        # A new notice invalidates every lane's coalesced empty poll (ADR 0146).
        self.notice = next(self._notices)
        for worker in self.workers:
            worker.wakeup.set()

    def set_lane_limits(self, limits):
        """Apply Settings → Local Processing limits to new claims; running work continues."""
        coordinator = self.coordinator
        limits = local_concurrency.effective(limits)
        with coordinator.lock:
            coordinator.lane_limits.update(
                {local_concurrency.LANES[lane][0]: count for lane, count in limits.items()}
            )
        coordinator.notify()
        return self.lane_limit_settings()

    def lane_limit_settings(self):
        limits = self.coordinator.lane_limits
        return {lane: limits.get(kind) for lane, (kind, _) in local_concurrency.LANES.items()}

    def _claimed(self):
        return bool(self.active_id or self.active_batch_id or self.active_ids)

    def _claim_due(self):
        """Caller holds the claim lock: may this worker query for a new job now?

        A configured lane admits a new claim only below its limit; lowering a limit never stops
        running work. Idle siblings coalesce polling: after one worker found its lane empty,
        the others skip the database until that poll's interval passes or a notice arrives.
        """
        coordinator = self.coordinator
        limit = coordinator.lane_limits.get(self.kind)
        if limit is not None and limit <= sum(
            worker.kind == self.kind and worker._claimed() for worker in coordinator.workers
        ):
            return False
        polled = coordinator.lane_polls.get(self.kind)
        return (
            polled is None
            or polled[2] is self
            or polled[1] != coordinator.notice
            or time.monotonic() >= polled[0]
        )

    def _polled_empty(self, notice, interval):
        """Caller holds the claim lock and observed no claimable work after ``notice``."""
        self.coordinator.lane_polls[self.kind] = (time.monotonic() + interval, notice, self)

    def busy(self, recording_id):
        return bool(
            [
                row
                for row in self.store.all(
                    "SELECT id FROM jobs WHERE recording_id=? AND state IN ('queued','running')",
                    (recording_id,),
                )
                if self._operational(row["id"])
            ]
        )

    def _operational(self, identifier):
        from . import restored_owners

        with self.store.connection() as db:
            return not restored_owners.is_restored(db, "job", identifier)

    @contextmanager
    def control_owner(self, kind, identifier):
        """Reserve an allocation even after its deleted members have no local job rows."""
        from . import restored_owners, runtime_store

        with self.coordinator.lock:
            restored_owners.require_owner(self.store, kind, identifier)
            if kind == "runtime_batch":
                batch = runtime_store.get(self.store, identifier)
            elif kind == "native_batch":
                batch = self.store.native_batch(identifier)
            else:
                raise ValueError("Unknown allocation kind.")
            identifiers = {member["job_id"] for member in batch["members"]}
            if identifiers & self.coordinator.control_job_ids:
                raise JobControlConflict("Wait for this allocation's current control request.")
            self.coordinator.control_job_ids.update(identifiers)
        try:
            yield
        finally:
            with self.coordinator.lock:
                self.coordinator.control_job_ids.difference_update(identifiers)

    @contextmanager
    def control_jobs(self, identifiers):
        """Fence HTTP control requests without holding the claim lock during their I/O."""
        from . import restored_owners

        coordinator = self.coordinator
        identifiers = set(identifiers)
        with coordinator.lock:
            # Member-level Stop/Retry acts on a whole allocation. Reserve its current
            # membership too; durable owner epochs still guard later admission changes.
            for identifier in tuple(identifiers):
                restored_owners.require_job(self.store, identifier)
                row = self.store.one("SELECT params FROM jobs WHERE id=?", (identifier,))
                params = json.loads(row["params"])
                if not isinstance(params, dict):
                    raise ValueError("This job has invalid control metadata. Keep its recording.")
                if params.get("native_batch_id"):
                    batch = self.store.native_batch(params["native_batch_id"])
                    identifiers.update(member["job_id"] for member in batch["members"])
                elif params.get("runtime_batch_id"):
                    from . import runtime_store

                    batch = runtime_store.get(self.store, params["runtime_batch_id"])
                    identifiers.update(member["job_id"] for member in batch["members"])
            if identifiers & coordinator.control_job_ids:
                raise JobControlConflict(
                    "Wait for this job's current Stop or Retry request to finish."
                )
            coordinator.control_job_ids.update(identifiers)
        try:
            yield
        finally:
            with coordinator.lock:
                coordinator.control_job_ids.difference_update(identifiers)

    def cancel(self, identifier, *, allowed_job_ids=None):
        from . import restored_owners

        restored_owners.require_job(self.store, identifier)
        with self.control_jobs([identifier]):
            return self._cancel(identifier, allowed_job_ids=allowed_job_ids)

    def _cancel(self, identifier, *, allowed_job_ids=None):
        """Freeze local cancellation under the claim lock; remote I/O runs after release."""
        allowed = frozenset(allowed_job_ids) if allowed_job_ids is not None else None
        child_id = None
        runtime_id = native_id = None
        with self.lock:
            if allowed is not None and identifier not in allowed:
                raise ValueError("Cancellation scope changed; review the affected jobs again.")
            job = self.store.job(identifier)
            if job["kind"] == "source-inspect":
                raise ValueError("Source inspection uses its internal inspection worker.")
            if "input_intent" in job["params"]:
                raise ValueError("Input preparation needs its phase-bound cancellation action.")
            runtime_id = job["params"].get("runtime_batch_id")
            native_id = job["params"].get("native_batch_id")
            if native_id:
                from .native_batch import batch_for_job

                batch = batch_for_job(self.store, job)
                if allowed is not None and any(
                    m["job_id"] not in allowed for m in batch["members"]
                ):
                    raise ValueError("Cancellation scope includes other UCloud batch jobs.")
            if not runtime_id and not native_id:
                if job["state"] == "completed":
                    return None
                followup = job["params"].get("followup")
                if job["kind"] == "transcribe" and followup:
                    children = self.store.all(
                        "SELECT id FROM jobs WHERE id=? AND recording_id=? AND kind='enhance' "
                        "AND json_extract(CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
                        "'$.parent_transcription_id')=?",
                        (followup["id"], job["recording_id"], identifier),
                    )
                    if children:
                        child_id = children[0]["id"]
                        if allowed is not None and child_id not in allowed:
                            raise ValueError("Cancellation scope changed to include a follow-up.")
                for worker in self.coordinator.workers:
                    if worker.active_id == identifier or identifier in worker.active_ids:
                        worker.cancel_event.set()
                job = self.store.request_job_cancellation(identifier)
                if job["state"] == "completed":
                    return None
        if runtime_id:
            from .runtime_batches import cancel

            result = cancel(self.store, self.credentials, runtime_id, allowed_job_ids=allowed)
            self.coordinator.notify()
            return result
        if native_id:
            from .native_batch import cancel_batch

            return cancel_batch(self.coordinator, job)
        if child_id:
            self.coordinator.cancel(child_id, allowed_job_ids=allowed)
        # A live worker owns its Stop, checkpoint and accepted-request drain.
        if job["state"] == "running":
            return None
        run = job["params"].get("ucloud_run", {})
        remote_id = run.get("job_id")
        if not remote_id or run.get("termination_requested"):
            return None
        if job["kind"] == "enhance" and "ucloud_api" in job["params"]:
            from . import repair_cloud
            from .enhancement_api import terminate_owned

            if job["params"].get("repair_cloud", {}).get("protocol") == repair_cloud.PROTOCOL:
                repair_cloud.stop_owned(self.store, self.credentials, job)
                return None
            terminate_owned(job, self.credentials)
        elif job["kind"] == "enhance":
            from .enhancement_cloud import CloudEnhancementSettings, terminate_owned

            terminate_owned(
                CloudEnhancementSettings.model_validate(job["params"]["cloud_settings"]),
                self.credentials,
                remote_id,
            )
        else:
            from .ucloud_native import terminate_owned

            settings = providers.Settings.model_validate(job["params"]["settings"])
            if settings.provider_type != "ucloud":
                return None
            terminate_owned(settings, self.credentials, remote_id)
        self.store.mark_job_termination_requested(identifier, remote_id)

    def run(self):
        if self.kind == "runtime-prepare":
            return self.run_runtime_preparation()
        if self.kind in {"source-inspect", "input-admission"}:
            return self.run_input_preparation()
        if self.kind == "runtime-batch":
            return self.run_runtime_batches()
        while not self.stopping.is_set():
            with self.lock:
                if self.stopping.is_set():
                    break
                claimed = None
                notice = self.coordinator.notice
                if not self.coordinator.capture_paused and self._claim_due():
                    try:
                        claimed = self.store.claim_job(self.kind)
                    except (ValueError, sqlite3.Error):
                        # A busy database or malformed claim must not end this worker thread.
                        pass
                    else:
                        if not claimed:
                            self._polled_empty(notice, 0.5)
                if claimed:
                    self.active_id = claimed["id"]
                    self.active_batch_id = claimed["params"].get("native_batch_id")
                    self.cancel_event = threading.Event()
            if not claimed:
                self.wakeup.wait(0.5)
                self.wakeup.clear()
                continue
            identifier = claimed["id"]

            def finish(state, message, identifier=identifier, **fields):
                if self.active_batch_id:
                    self.store.finish_native_batch(self.active_batch_id, state, message)
                elif state == "completed" or self.store.job(identifier)["state"] != "completed":
                    # A committed result is the receipt; later stops/errors never downgrade it.
                    self.store.update_job(identifier, state=state, message=message, **fields)

            try:
                self.process(identifier)
                if self.cancel_event.is_set():
                    raise media.Cancelled()
                if not self.active_batch_id:
                    finish("completed", "Complete", progress=1)
            except media.Cancelled:
                finish(
                    "interrupted" if self.stopping.is_set() else "cancelled",
                    (
                        "Stopped. Completed enhancement stages are retained; retry explicitly."
                        if self.kind.startswith("enhance")
                        else "Stopped. Completed chunks are retained; retry explicitly to continue."
                    ),
                )
                job = self.store.job(identifier)
                if job["kind"] == "media":
                    self.store.execute(
                        "UPDATE recordings SET status='error' WHERE id=?", (job["recording_id"],)
                    )
            except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
                from .native_batch import MonitoringInterrupted

                # Known errors have curated messages; filesystem paths are not sent to the UI.
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else (
                        "Local file operation failed. Check free space and that files still exist."
                    )
                )
                finish(
                    "interrupted"
                    if self.active_batch_id and isinstance(exc, MonitoringInterrupted)
                    else "failed",
                    message[:700],
                )
                job = self.store.job(identifier)
                if job["kind"] == "media":
                    self.store.execute(
                        "UPDATE recordings SET status='error' WHERE id=?", (job["recording_id"],)
                    )
            except Exception:
                finish("failed", "Unexpected processing error. Retry this job.")
            finally:
                with self.lock:
                    self.active_id = None
                    self.active_batch_id = None

    def run_runtime_preparation(self):
        from . import runtime_preparation

        while not self.stopping.is_set():
            with self.lock:
                value = None
                if not self.stopping.is_set() and not self.coordinator.capture_paused:
                    try:
                        value = runtime_preparation.claim(self.store)
                    except (ValueError, sqlite3.Error):
                        pass
                if value:
                    self.active_ids = ["runtime-preparation:" + value["id"]]
            if value is None:
                self.wakeup.wait(0.5)
                self.wakeup.clear()
                continue
            try:
                runtime_preparation.Run(self, value).execute()
            except Exception:
                # A failed checkpoint must not end the lane or replay its submitted work.
                # The running owner is recovered conservatively on the next app start.
                pass
            finally:
                with self.lock:
                    self.active_ids = []

    def run_runtime_batches(self):
        """Capacity belongs to durable owners, independently of the number of file workers."""
        from . import runtime_batches, runtime_store

        while not self.stopping.is_set():
            with self.lock:
                if self.stopping.is_set():
                    break
                paused = self.coordinator.capture_paused
                batch = None
                if not paused:
                    try:
                        batch = runtime_store.claim(self.store)
                    except (ValueError, sqlite3.Error):
                        pass
                if not batch and not paused:
                    self.active_ids = [CAPACITY_OBSERVATION]
                if batch:
                    self.active_batch_id = batch["id"]
                    self.cancel_event = threading.Event()
            if not batch:
                from .runtime_capacity import refresh_blocker

                try:
                    # Acknowledged Stop is not terminal proof. Refresh one old singleton
                    # receipt outside the claim lock so stale legacy rows cannot fill every slot.
                    if not paused:
                        refresh_blocker(self.store, self.credentials)
                        from . import remote_release

                        if remote_release.enabled():
                            # Old retained obligations release themselves, one owner per
                            # idle pass, outside the claim lock (ADR 0101).
                            remote_release.tick(self.store, self.credentials, self.coordinator)
                except (ValueError, OSError, sqlite3.Error):
                    pass
                finally:
                    with self.lock:
                        self.active_ids = []
                self.wakeup.wait(0.5)
                self.wakeup.clear()
                continue
            try:
                runtime_batches.run(self, batch)
            except Exception as error:
                # Preserve ownership and task receipts for explicit recovery. No new POST,
                # cleanup, or inference is inferred from a local coordinator failure.
                try:
                    runtime_store.interrupt(
                        self.store,
                        batch["id"],
                        batch["epoch"],
                        runtime_batches._local_failure("coordinator", error)
                        + " Reconnect to recover the same allocation.",
                    )
                except (ValueError, sqlite3.Error):
                    # A newer epoch may already own recovery. Never overwrite that owner
                    # or lose this lane because a stale coordinator tried to report failure.
                    pass
            finally:
                with self.lock:
                    self.active_batch_id = None

    def run_input_preparation(self):
        """One source lane and one admission lane; neither uses the producer finisher."""
        worker = self

        class StopGuard:
            def is_set(self):
                if worker.stopping.is_set():
                    raise input_admission.PreparationStopped()
                return False

            def wait(self, timeout):
                worker.stopping.wait(timeout)
                return self.is_set()

        while not self.stopping.is_set():
            claimed = None
            with self.lock:
                if self.stopping.is_set():
                    break
                notice, polled = self.coordinator.notice, False
                try:
                    if self.coordinator.capture_paused or not self._claim_due():
                        pass
                    elif self.kind == "source-inspect":
                        from . import restored_owners

                        polled = True
                        with self.store.connection() as db:
                            operational = restored_owners.sql_exclusion(db, "job", "jobs.id")
                        for row in self.store.all(
                            "SELECT id FROM jobs WHERE kind='source-inspect' "
                            "AND state='queued' AND " + operational + " ORDER BY created,id"
                        ):
                            try:
                                claimed = original_inspection.claim(self.store, row["id"])
                            except (ValueError, KeyError):
                                continue
                            if claimed is not None:
                                break
                    else:
                        polled = True
                        claimed = input_admission.claim(self.store)
                except (ValueError, sqlite3.Error):
                    pass
                else:
                    if polled and claimed is None:
                        self._polled_empty(notice, 0.2)
                if claimed:
                    self.active_ids = (
                        [claimed["job_id"]]
                        if self.kind == "source-inspect"
                        else [item["id"] for item in claimed["jobs"]]
                    )
                    self.active_id = self.active_ids[0]
            if claimed is None:
                self.wakeup.wait(0.2)
                self.wakeup.clear()
                continue
            try:
                if self.kind == "source-inspect":
                    original_inspection.run(self.store, claimed, StopGuard())
                else:
                    outcomes = input_admission.prepare(
                        self.store, self.credentials, claimed, StopGuard()
                    )
                    with self.lock:
                        if not self.stopping.is_set():
                            input_admission.materialize(self.store, claimed, outcomes)
                self.coordinator.notify()
            except (input_admission.PreparationStopped, input_admission.StaleInput):
                pass
            except Exception:
                if not self.stopping.is_set():
                    try:
                        with self.lock:
                            if self.kind == "source-inspect":
                                original_inspection.fail(self.store, claimed)
                            else:
                                input_admission.materialize(
                                    self.store,
                                    claimed,
                                    {
                                        item["id"]: {"error": "preparation_failed"}
                                        for item in claimed["jobs"]
                                    },
                                )
                    except (ValueError, OSError, sqlite3.Error, KeyError):
                        pass  # Contradictory proof remains owned; never use generic writes.
            finally:
                with self.lock:
                    self.active_id, self.active_ids = None, []

    def process(self, identifier):
        from . import restored_owners

        restored_owners.require_job(self.store, identifier)
        job = self.store.job(identifier)
        if job["kind"] == "source-inspect":
            raise ValueError("Source inspection uses its internal inspection worker.")
        if "input_intent" in job["params"]:
            raise ValueError("Input preparation must finish before a producer can run.")
        recording = self.store.recording(job["recording_id"])
        directory = self.store.directory(recording["id"])
        if job["kind"] == "media":
            self.store.update_job(identifier, message="Preparing browser playback…", progress=0.1)
            metadata = media.prepare_media(
                directory, Path(recording["filename"]), self.cancel_event
            )
            self.store.execute(
                "UPDATE recordings SET kind=?,duration=COALESCE(duration,?),"
                "status='ready' WHERE id=?",
                (metadata["kind"], metadata["duration"], recording["id"]),
            )
            self.store.snapshot_project(recording["project_id"])
            return
        if recording["status"] != "ready" and not input_admission.uses_original(job, recording):
            raise ValueError("Media is not ready. Finish or retry media preparation first.")
        recording = audio_inputs.effective_recording(job, recording)
        if job["kind"] == "enhance":
            from . import repair_cloud
            from .enhancement import run_enhancement

            if job["params"].get("repair_cloud", {}).get("protocol") == repair_cloud.PROTOCOL:
                repair_cloud.prepare_and_run(self, job, recording)
                return
            run_enhancement(self, job, recording)
            return
        settings = providers.Settings.model_validate(job["params"]["settings"])
        cached_plan = completed_audio_plan(job)
        key = ""
        if cached_plan is None:
            providers.require_approval(
                self.store,
                recording["project_id"],
                job["params"].get("provider_id"),
                settings,
            )
            key = self.credentials.get(providers.credential_scope(settings))
            providers.require_key(settings, key)
        if settings.provider_type == "ucloud" and cached_plan is None:
            if job["params"].get("native_batch_id"):
                from .native_batch import run_batch

                run_batch(self, job)
                return
            from .ucloud_native import run_transcription

            run_transcription(self, job, recording, settings, key)
            return
        if cached_plan is not None:
            folder, plan = directory / "chunks" / identifier, cached_plan
        else:
            self.store.update_job(identifier, message="Preparing compressed audio…", progress=0.02)
            folder, plan = media.prepare_chunks(
                directory,
                identifier,
                recording["duration"],
                settings.max_upload_mb,
                self.cancel_event,
                **(
                    {
                        "audio_input": job["params"]["audio_input"],
                        "transport": job["params"]["audio_transport"],
                    }
                    if "audio_input" in job["params"]
                    else {}
                ),
            )
        if "audio_input" in job["params"] and cached_plan is None:
            self.store.record_audio_preparation(
                identifier, audio_inputs.prepared_evidence(folder, job["params"]["audio_input"])
            )
        # Legacy checkpoints are a prefix; parallel checkpoints carry their explicit index.
        completed = {part.get("index", index): part for index, part in enumerate(job["chunks"])}
        # Only a policy frozen at fresh admission records descriptors and output observations
        # (ADR 0097). Legacy and unmarked jobs keep their exact earlier request behavior.
        capture = provider_capture.enabled(job)

        def transcribe_part(index, descriptor):
            chunk = plan[index]
            raw_path = folder / f"response-{index:04d}.json"
            observed = None
            if raw_path.exists():
                payload = json.loads(raw_path.read_text(encoding="utf-8"))
            else:
                path = (
                    audio_inputs.chunk_path(directory, folder, chunk, job["params"]["audio_input"])
                    if "audio_input" in job["params"]
                    else folder / chunk["file"]
                )
                with self.lock:
                    if self.cancel_event.is_set():
                        raise media.Cancelled()
                    providers.require_approval(
                        self.store,
                        recording["project_id"],
                        job["params"].get("provider_id"),
                        settings,
                    )
                if descriptor is None or descriptor["parameters"] == providers.transcription_fields(
                    settings
                ):
                    # The process catalog is fixed, so this derives the recorded fields exactly.
                    payload = providers.transcribe_file(path, settings, key)
                else:
                    # A restarted app with another catalog replays the first frozen fields.
                    payload = providers.transcribe_file(
                        path, settings, key, request_fields=descriptor["parameters"]
                    )
                if "audio_input" in job["params"]:
                    audio_inputs.chunk_path(directory, folder, chunk, job["params"]["audio_input"])
                providers.preserve_response(raw_path, payload)
                observed = provider_capture.observe_response(directory, job, index, descriptor)
            part = {
                "index": index,
                "offset": chunk["offset"],
                "segments": providers.normalize_response(payload),
            }
            # A preserved file without this run's request is a cached gap, never backfilled.
            return part, descriptor if observed is not None else None, observed

        pending = iter(index for index in range(len(plan)) if index not in completed)
        active = {}
        failure = None
        with ThreadPoolExecutor(
            max_workers=settings.concurrent_chunks, thread_name_prefix="transcription-part"
        ) as executor:
            while True:
                while (
                    failure is None
                    and not self.cancel_event.is_set()
                    and len(active) < settings.concurrent_chunks
                ):
                    index = next(pending, None)
                    if index is None:
                        break
                    descriptor = None
                    if capture and not (folder / f"response-{index:04d}.json").exists():
                        try:
                            # Recorded before any audio for this part can be uploaded.
                            descriptor = provider_capture.begin_request(self.store, job, index)
                        except Exception as exc:
                            failure = exc
                            break
                    active[executor.submit(transcribe_part, index, descriptor)] = index
                if not active:
                    break
                self.store.update_job(
                    identifier,
                    message=f"Transcribing {len(active)} part(s) · "
                    f"{len(completed)} of {len(plan)} complete…",
                    progress=0.1 + 0.85 * len(completed) / len(plan),
                )
                finished, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in finished:
                    index = active.pop(future)
                    try:
                        part, descriptor, observed = future.result()
                        if capture:
                            # The part and its observation (or explicit gap) commit together.
                            provider_capture.record_response(
                                self.store, job, index, descriptor, observed, part
                            )
                            completed[index] = part
                        else:
                            completed[index] = part
                            self.store.update_job(
                                identifier, chunks=[completed[i] for i in sorted(completed)]
                            )
                    except Exception as exc:
                        # Stop scheduling; settle and retain all other accepted requests.
                        if failure is None:
                            failure = exc
        if self.cancel_event.is_set():
            raise media.Cancelled()
        if failure is not None:
            raise failure
        parts = [completed[i] for i in sorted(completed)]
        # The exact assembly is recorded outside the queue lock; Store binds it on creation.
        evidence = provider_capture.finish(self.store, job, parts) if capture else None
        with self.lock:
            if self.cancel_event.is_set():
                raise media.Cancelled()
            self.store.add_result(
                recording["id"],
                combine_chunks(parts),
                {},
                job["params"].get("provider_name", settings.model),
                {
                    "kind": "provider",
                    "provider_id": job["params"].get("provider_id"),
                    "provider_name": job["params"].get("provider_name", settings.model),
                    **settings.model_dump(),
                },
                job_id=identifier,
                provider_artifacts=evidence,
            )
        self.store.update_job(identifier, message=f"Saved {len(plan)} part(s) at {now()}.")

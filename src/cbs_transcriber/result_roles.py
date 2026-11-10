"""Explicit Working designations pin immutable versions, independently of the review view."""

import hashlib
import hmac
import json
import re
import sqlite3
from datetime import datetime

from .history import (
    MAX_OBSERVATION_BYTES,
    decode_payload,
    validate_revision,
    validate_version_observation,
    verify_payload,
)
from .transcripts import MAX_TRANSCRIPT_BYTES

FORMAT = "cbs-working-version-v1"
WORKING_POLICY = "cbs-working-role-v1"
MAX_ROLE_EVENTS = 1000
AUTO_ACTIONS = frozenset({"working_auto_first", "working_auto_successor", "working_auto_edit"})


def migrate_automatic(store):
    """Schema 18 records automatic role changes without attributing them to a user."""
    from .store import new_id

    with store.library_lock(), store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 18:
            return
        if store.initial_version == 17:
            (store.root / "backups").mkdir(exist_ok=True)
            backup = sqlite3.connect(store.root / "backups" / f"schema-17-{new_id()}.sqlite3")
            try:
                db.backup(backup)
            finally:
                backup.close()
        db.execute("BEGIN IMMEDIATE")
        db.execute("ALTER TABLE result_role_events RENAME TO result_role_events_before_auto")
        db.execute("""CREATE TABLE result_role_events (
            id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL CHECK(sequence>0),
            previous_version_id TEXT REFERENCES result_versions(id),
            previous_payload_sha256 TEXT,
            next_version_id TEXT REFERENCES result_versions(id),
            next_payload_sha256 TEXT,
            action TEXT NOT NULL CHECK(action IN
                ('working_marked','working_cleared','working_trashed','working_kept',
                 'working_auto_first','working_auto_successor','working_auto_edit')),
            observed_at TEXT NOT NULL,
            actor_kind TEXT NOT NULL CHECK(actor_kind IN ('local_user','system')),
            UNIQUE(recording_id,sequence),
            CHECK((previous_version_id IS NULL)=(previous_payload_sha256 IS NULL)),
            CHECK((next_version_id IS NULL)=(next_payload_sha256 IS NULL))
        )""")
        db.execute("INSERT INTO result_role_events SELECT * FROM result_role_events_before_auto")
        db.execute("DROP TABLE result_role_events_before_auto")
        db.execute("""CREATE TABLE working_job_authorities (
            job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
            recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
            policy TEXT NOT NULL CHECK(policy IN ('result','timing')),
            role_json TEXT NOT NULL,
            source_version_id TEXT REFERENCES result_versions(id),
            source_payload_sha256 TEXT,
            source_snapshot_sha256 TEXT,
            admission_sha256 TEXT,
            CHECK((source_version_id IS NULL)=(source_payload_sha256 IS NULL)),
            CHECK((source_version_id IS NULL)=(source_snapshot_sha256 IS NULL))
        )""")
        db.execute("PRAGMA user_version=18")


def migrate(store):
    from .store import new_id

    with store.library_lock(), store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 14:
            return
        if store.initial_version == 13:
            (store.root / "backups").mkdir(exist_ok=True)
            backup = sqlite3.connect(store.root / "backups" / f"schema-13-{new_id()}.sqlite3")
            try:
                db.backup(backup)
            finally:
                backup.close()
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS recording_result_roles (
            recording_id TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
            working_version_id TEXT REFERENCES result_versions(id),
            revision INTEGER NOT NULL CHECK(revision>=0)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS result_role_events (
            id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL CHECK(sequence>0),
            previous_version_id TEXT REFERENCES result_versions(id),
            previous_payload_sha256 TEXT,
            next_version_id TEXT REFERENCES result_versions(id),
            next_payload_sha256 TEXT,
            action TEXT NOT NULL CHECK(action IN
                ('working_marked','working_cleared','working_trashed')),
            observed_at TEXT NOT NULL,
            actor_kind TEXT NOT NULL CHECK(actor_kind='local_user'),
            UNIQUE(recording_id,sequence),
            CHECK((previous_version_id IS NULL)=(previous_payload_sha256 IS NULL)),
            CHECK((next_version_id IS NULL)=(next_payload_sha256 IS NULL))
        )""")
        # No earlier selection or producer completion implies a researcher designation.
        db.execute("PRAGMA user_version=14")


def version(db, recording_id, version_id, *, live=True):
    size = db.execute(
        "SELECT length(CAST(v.payload AS BLOB)),length(CAST(v.observed_at AS BLOB)),"
        "typeof(v.sequence) FROM result_versions v "
        "JOIN transcript_results t ON t.id=v.result_id WHERE v.id=? AND t.recording_id=?",
        (version_id, recording_id),
    ).fetchone()
    if size is None:
        raise ValueError("The Working version must belong to this recording.")
    if size[0] > MAX_TRANSCRIPT_BYTES:
        raise ValueError("The saved Working version exceeds the supported size.")
    if (
        type(size[1]) is not int
        or not 1 <= size[1] <= MAX_OBSERVATION_BYTES
        or size[2] != "integer"
    ):
        raise ValueError("The saved research observation metadata is invalid.")
    row = db.execute(
        "SELECT v.*,t.recording_id,t.label,EXISTS(SELECT 1 FROM result_trash x "
        "WHERE x.result_id=t.id) AS trashed FROM result_versions v "
        "JOIN transcript_results t ON t.id=v.result_id WHERE v.id=? AND t.recording_id=?",
        (version_id, recording_id),
    ).fetchone()
    if row is None:
        raise ValueError("The Working version must belong to this recording.")
    if live and row["trashed"]:
        raise ValueError("Restore this result from Trash before marking a Working version.")
    validate_version_observation(row["sequence"], row["observed_at"])
    if len(row["payload"].encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("The saved Working version exceeds the supported size.")
    verify_payload(row["payload"], row["payload_sha256"])
    return row


def reference(row):
    return {
        "version_id": row["id"],
        "result_id": row["result_id"],
        "sequence": row["sequence"],
        "payload_sha256": row["payload_sha256"],
    }


def validate_frozen(state):
    """Validate a role snapshot frozen at the actual request boundary."""
    if not isinstance(state, dict) or set(state) != {"revision", "working"}:
        raise ValueError("Invalid frozen Working designation.")
    validate_revision(state["revision"])
    working = state["working"]
    if working is not None and (
        not isinstance(working, dict)
        or set(working) != {"version_id", "result_id", "sequence", "payload_sha256"}
        or any(
            not isinstance(working[key], str) or re.fullmatch(r"[0-9a-f]{32}", working[key]) is None
            for key in ("version_id", "result_id")
        )
        or type(working["sequence"]) is not int
        or not 1 <= working["sequence"] < 2**63
        or not isinstance(working["payload_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", working["payload_sha256"]) is None
    ):
        raise ValueError("Invalid frozen Working designation.")
    return state


def frozen_roles_equal(left, right):
    try:
        validate_frozen(left)
        validate_frozen(right)
    except (ValueError, TypeError, RecursionError):
        return False
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":")
    )


def authorize_job(db, store, job_id, recording_id, policy, expected, source):
    """Record trusted enqueue authority outside the formerly arbitrary job parameters."""
    if policy not in {"result", "timing"}:
        raise ValueError("Invalid Working job policy.")
    validate_frozen(expected)
    if not frozen_roles_equal(read(db, store, recording_id, verify_tail=True), expected):
        raise ValueError("The Working designation changed before the job was accepted.")
    job = db.execute(
        "SELECT recording_id,kind,length(CAST(params AS BLOB)) AS size FROM jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    if job is None or job["recording_id"] != recording_id:
        raise ValueError("The Working authority must belong to this recording's job.")
    if type(job["size"]) is not int or job["size"] > 3 * MAX_TRANSCRIPT_BYTES + 1024 * 1024:
        raise ValueError("The Working job parameters exceed the supported size.")
    try:
        params = json.loads(
            db.execute("SELECT params FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        )
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Invalid Working job parameters.") from None
    if not isinstance(params, dict):
        raise ValueError("Invalid Working job parameters.")
    requested_source_hash = source_snapshot_hash(source) if source is not None else None

    def source_matches(value):
        return (
            value is None
            if source is None
            else value is not None and source_snapshot_hash(value) == requested_source_hash
        )

    intent = params.get("input_intent")
    admission = None
    if intent is not None:
        if not isinstance(intent, dict) or policy != "result":
            raise ValueError("Invalid Working job admission.")
        baseline, accepted = intent.get("baseline"), intent.get("accepted")
        admission = intent.get("accepted_sha256")
        if (
            not isinstance(admission, str)
            or re.fullmatch(r"[0-9a-f]{64}", admission) is None
            or not isinstance(baseline, dict)
            or not frozen_roles_equal(baseline.get("working_role"), expected)
            or not isinstance(accepted, dict)
            or not source_matches(intent.get("source_snapshot"))
            or job["kind"] not in {"transcribe", "enhance"}
            or (source is None and accepted.get("operation") != "transcription")
            or (
                source is not None
                and (job["kind"] != "enhance" or accepted.get("operation") != "enhancement")
            )
        ):
            raise ValueError("The Working authority differs from the accepted request.")
    else:
        if job["kind"] != "enhance" or not source_matches(params.get("source_snapshot")):
            raise ValueError("The Working authority differs from the processing job.")
        timing = params.get("timing_working_policy")
        frozen_role = (
            params.get("working_role")
            if policy == "result"
            else (timing.get("expected_role") if isinstance(timing, dict) else None)
        )
        if (
            source is None
            or not frozen_roles_equal(frozen_role, expected)
            or (policy == "result" and params.get("working_role_policy") != WORKING_POLICY)
            or (
                policy == "timing"
                and (
                    not isinstance(timing, dict)
                    or set(timing) != {"format", "expected_role"}
                    or timing.get("format") != "cbs-timing-working-v1"
                )
            )
        ):
            raise ValueError("The Working authority differs from the processing request.")
    source_id = digest = snapshot_digest = None
    if source is not None:
        if not isinstance(source, dict):
            raise ValueError("Invalid frozen Working source.")
        source_id, digest = source.get("source_version_id"), source.get("source_payload_sha256")
        saved = version(db, recording_id, source_id)
        if saved["result_id"] != source.get("id") or saved["payload_sha256"] != digest:
            raise ValueError("The Working authority must bind the exact frozen source.")
        snapshot_digest = source_snapshot_hash(source)
        payload = decode_payload(saved["payload"], digest)
        actual = source.get("segments")

        def canonical(value):
            return json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )

        if (
            not isinstance(actual, list)
            or len(actual) > len(payload["segments"])
            or canonical(source.get("speakers")) != canonical(payload["speakers"])
        ):
            raise ValueError("The Working source does not match its saved version.")
        if policy == "result":
            if canonical(actual) != canonical(payload["segments"]):
                raise ValueError("The Working source does not match its saved version.")
        else:
            remaining = iter(map(canonical, payload["segments"]))
            for item in actual:
                needle = canonical(item)
                if not any(needle == saved_item for saved_item in remaining):
                    raise ValueError(
                        "The timing source is not an ordered saved-version projection."
                    )
    elif policy == "timing":
        raise ValueError("Timing authority requires a frozen source.")
    db.execute(
        "INSERT INTO working_job_authorities VALUES (?,?,?,?,?,?,?,?)",
        (
            job_id,
            recording_id,
            policy,
            json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            source_id,
            digest,
            snapshot_digest,
            admission,
        ),
    )


def job_authority(db, job_id, recording_id, policy):
    if policy not in {"result", "timing"}:
        raise ValueError("The Working job authority has an invalid policy.")
    row = db.execute(
        "SELECT a.job_id,a.recording_id,a.policy,a.source_version_id,a.source_payload_sha256,"
        "a.source_snapshot_sha256,a.admission_sha256,length(CAST(a.role_json AS BLOB)) AS size,"
        "typeof(a.role_json) AS type,j.recording_id AS job_recording,j.kind AS job_kind "
        "FROM working_job_authorities a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.job_id=?",
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    if (
        row["recording_id"] != recording_id
        or row["job_recording"] != recording_id
        or row["policy"] != policy
        or row["type"] != "text"
        or type(row["size"]) is not int
        or not 1 <= row["size"] <= 4096
    ):
        raise ValueError("The Working job authority does not match this job.")
    source = row["source_version_id"]
    if source is None:
        if (
            policy != "result"
            or row["job_kind"] not in {"transcribe", "enhance"}
            or row["source_payload_sha256"] is not None
            or row["source_snapshot_sha256"] is not None
            or row["admission_sha256"] is None
        ):
            raise ValueError("The frozen Working job source is invalid.")
    else:
        if (
            row["job_kind"] != "enhance"
            or not isinstance(source, str)
            or re.fullmatch(r"[0-9a-f]{32}", source) is None
            or any(
                not isinstance(row[key], str) or re.fullmatch(r"[0-9a-f]{64}", row[key]) is None
                for key in ("source_payload_sha256", "source_snapshot_sha256")
            )
        ):
            raise ValueError("The frozen Working job source is invalid.")
        source_row = db.execute(
            "SELECT v.payload_sha256,t.recording_id FROM result_versions v "
            "JOIN transcript_results t ON t.id=v.result_id WHERE v.id=?",
            (source,),
        ).fetchone()
        if (
            source_row is None
            or source_row["recording_id"] != recording_id
            or source_row["payload_sha256"] != row["source_payload_sha256"]
        ):
            raise ValueError("The frozen Working job source changed.")
    if row["admission_sha256"] is not None and (
        not isinstance(row["admission_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", row["admission_sha256"]) is None
        or policy == "timing"
    ):
        raise ValueError("The frozen Working admission is invalid.")
    raw = db.execute(
        "SELECT role_json FROM working_job_authorities WHERE job_id=?", (job_id,)
    ).fetchone()[0]
    try:
        role = validate_frozen(json.loads(raw))
    except (ValueError, TypeError, RecursionError, json.JSONDecodeError):
        raise ValueError("The frozen Working job authority is invalid.") from None
    if json.dumps(role, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != raw:
        raise ValueError("The frozen Working job authority is not canonical.")
    if role["revision"] == 0:
        if role["working"] is not None:
            raise ValueError("The frozen Working job role has no matching history.")
    else:
        event = db.execute(
            "SELECT previous_version_id,previous_payload_sha256,next_version_id,"
            "next_payload_sha256,action,actor_kind "
            "FROM result_role_events "
            "WHERE recording_id=? AND sequence=?",
            (recording_id, role["revision"]),
        ).fetchone()
        reference_at_revision = (
            reference(version(db, recording_id, event["next_version_id"], live=False))
            if event and event["next_version_id"]
            else None
        )
        if (
            event is None
            or reference_at_revision != role["working"]
            or event["next_payload_sha256"]
            != (reference_at_revision["payload_sha256"] if reference_at_revision else None)
            or event["action"]
            not in {"working_marked", "working_cleared", "working_trashed", "working_kept"}
            | AUTO_ACTIONS
            or event["actor_kind"]
            != ("system" if event["action"] in AUTO_ACTIONS else "local_user")
            or (
                event["action"] != "working_kept"
                and (event["action"] in {"working_marked"} | AUTO_ACTIONS)
                != (reference_at_revision is not None)
            )
            or (
                event["action"] == "working_auto_first"
                and (event["previous_version_id"] is not None or role["revision"] != 1)
            )
            or (event["action"] == "working_auto_edit" and event["previous_version_id"] is None)
            or (event["previous_version_id"] == event["next_version_id"])
            != (event["action"] == "working_kept")
            or (
                event["action"] == "working_kept"
                and event["previous_payload_sha256"] != event["next_payload_sha256"]
            )
        ):
            raise ValueError("The frozen Working job role has no matching history.")
    return {
        "role": role,
        "source_version_id": row["source_version_id"],
        "source_payload_sha256": row["source_payload_sha256"],
        "source_snapshot_sha256": row["source_snapshot_sha256"],
        "admission_sha256": row["admission_sha256"],
    }


def source_snapshot_hash(source):
    try:
        encoded = json.dumps(
            source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("The frozen Working source is invalid.") from None
    if len(encoded) > MAX_TRANSCRIPT_BYTES + 1024 * 1024:
        raise ValueError("The frozen Working source exceeds the supported size.")
    return hashlib.sha256(encoded).hexdigest()


def inherit_job_authority(db, old_job_id, new_job_id, recording_id, admission_sha256):
    """A consented retry inherits exact original authority, never today's role."""
    job = db.execute("SELECT recording_id FROM jobs WHERE id=?", (new_job_id,)).fetchone()
    if job is None or job["recording_id"] != recording_id:
        raise ValueError("The Working authority must belong to this recording's job.")
    authority = job_authority(db, old_job_id, recording_id, "result")
    if authority is None:
        return
    raw = db.execute("SELECT params FROM jobs WHERE id=?", (new_job_id,)).fetchone()[0]
    if len(raw.encode("utf-8")) > 3 * MAX_TRANSCRIPT_BYTES + 1024 * 1024:
        raise ValueError("The Working job parameters exceed the supported size.")
    intent = json.loads(raw).get("input_intent", {})
    source = intent.get("source_snapshot")
    if (
        intent.get("accepted_sha256") != admission_sha256
        or not frozen_roles_equal(intent.get("baseline", {}).get("working_role"), authority["role"])
        or (source_snapshot_hash(source) if source is not None else None)
        != authority["source_snapshot_sha256"]
    ):
        raise ValueError("The renewed Working job differs from its frozen authority.")
    db.execute(
        "INSERT INTO working_job_authorities "
        "SELECT ?,recording_id,policy,role_json,source_version_id,source_payload_sha256,"
        "source_snapshot_sha256,? "
        "FROM working_job_authorities WHERE job_id=? AND recording_id=?",
        (new_job_id, admission_sha256, old_job_id, recording_id),
    )


def _available(db):
    """Pre-14 libraries (only seen before migration) have no designation; later ones must."""
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recording_result_roles'"
    ).fetchone():
        return True
    if db.execute("PRAGMA user_version").fetchone()[0] >= 14:
        raise RuntimeError("The Working designation tables are missing. Preserve the library.")
    return False


def _bounded_fields(fields):
    """Project small trusted-name text fields without materializing oversized values."""
    values = [
        f"CASE WHEN length(CAST({name} AS BLOB)) <= {limit} THEN {name} END AS {name}"
        for name, limit in fields
    ]
    valid = " AND ".join(
        f"({name} IS NULL OR (typeof({name})='text' AND length(CAST({name} AS BLOB)) <= {limit}))"
        for name, limit in fields
    )
    return ",".join([*values, f"CASE WHEN {valid} THEN 1 ELSE 0 END AS fields_bounded"])


def _historical_hash(db, recording_id, version_id):
    """Verify an owned historical payload without loading unrelated version metadata."""
    bounds = db.execute(
        "SELECT length(CAST(v.payload AS BLOB)),length(CAST(v.observed_at AS BLOB)),"
        "typeof(v.sequence),length(CAST(v.payload_sha256 AS BLOB)) "
        "FROM result_versions v JOIN transcript_results t ON t.id=v.result_id "
        "WHERE v.id=? AND t.recording_id=?",
        (version_id, recording_id),
    ).fetchone()
    if (
        bounds is None
        or type(bounds[0]) is not int
        or bounds[0] > MAX_TRANSCRIPT_BYTES
        or type(bounds[1]) is not int
        or not 1 <= bounds[1] <= MAX_OBSERVATION_BYTES
        or bounds[2] != "integer"
        or bounds[3] != 64
    ):
        raise ValueError("The Working history version is invalid. Preserve the library.")
    row = db.execute(
        "SELECT v.sequence,v.observed_at,v.payload,v.payload_sha256 FROM result_versions v "
        "JOIN transcript_results t ON t.id=v.result_id WHERE v.id=? AND t.recording_id=?",
        (version_id, recording_id),
    ).fetchone()
    validate_version_observation(row["sequence"], row["observed_at"])
    verify_payload(row["payload"], row["payload_sha256"])
    return row["payload_sha256"]


def _verify_write_tail(db, recording_id, state, working):
    """Verify two recent events and their predecessor links, not the entire history."""
    fields = _bounded_fields(
        [
            (name, 64)
            for name in (
                "previous_version_id",
                "previous_payload_sha256",
                "next_version_id",
                "next_payload_sha256",
                "action",
                "actor_kind",
            )
        ]
        + [("observed_at", 256)]
    )
    rows = db.execute(
        f"SELECT sequence,{fields} FROM result_role_events "
        "WHERE recording_id=? ORDER BY sequence DESC LIMIT 3",
        (recording_id,),
    ).fetchall()
    if not rows:
        if state is not None:
            raise ValueError("The Working history chain is invalid. Preserve the library.")
        return
    references = {working["version_id"]: working["payload_sha256"]} if working else {}
    for index, row in enumerate(rows[:2]):
        sequence = row["sequence"]
        before, after = row["previous_version_id"], row["next_version_id"]
        action = row["action"]
        if type(sequence) is not int or sequence < 1:
            raise ValueError("The Working history chain is invalid. Preserve the library.")
        if sequence == 1:
            linked = index + 1 == len(rows) and before is None
        else:
            prior = rows[index + 1] if index + 1 < len(rows) else None
            linked = bool(
                prior
                and prior["fields_bounded"] == 1
                and prior["sequence"] == sequence - 1
                and (before, row["previous_payload_sha256"])
                == (prior["next_version_id"], prior["next_payload_sha256"])
            )
        if (
            not linked
            or row["fields_bounded"] != 1
            or action
            not in {"working_marked", "working_cleared", "working_trashed", "working_kept"}
            | AUTO_ACTIONS
            or row["actor_kind"] != ("system" if action in AUTO_ACTIONS else "local_user")
            or (action == "working_auto_first" and (before is not None or sequence != 1))
            or (action == "working_auto_edit" and before is None)
            or (
                action != "working_kept"
                and (action in {"working_marked"} | AUTO_ACTIONS) != (after is not None)
            )
            or (before == after) != (action == "working_kept")
        ):
            raise ValueError("The Working history chain is invalid. Preserve the library.")
        try:
            timestamp = row["observed_at"]
            if not isinstance(timestamp, str) or "\x00" in timestamp or len(timestamp) > 64:
                raise ValueError()
            if datetime.fromisoformat(timestamp).tzinfo is None:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("The Working history observation time is invalid.") from None
        for ref, digest in (
            (before, row["previous_payload_sha256"]),
            (after, row["next_payload_sha256"]),
        ):
            if (ref is None) != (digest is None):
                raise ValueError("The Working history has an invalid version reference.")
            if ref is not None:
                if (
                    re.fullmatch(r"[0-9a-f]{32}", ref) is None
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                ):
                    raise ValueError("The Working history has an invalid version reference.")
                if ref not in references:
                    references[ref] = _historical_hash(db, recording_id, ref)
                if references[ref] != digest:
                    raise ValueError(
                        "The Working history version hash changed. Preserve the library."
                    )


def read(db, store, recording_id, *, verify_tail=False):
    if not db.execute("SELECT 1 FROM recordings WHERE id=?", (recording_id,)).fetchone():
        raise KeyError(recording_id)
    if not _available(db):
        return {"revision": 0, "working": None}
    state = db.execute(
        f"SELECT revision,{_bounded_fields([('working_version_id', 64)])} "
        "FROM recording_result_roles WHERE recording_id=?",
        (recording_id,),
    ).fetchone()
    if state and state["fields_bounded"] != 1:
        raise ValueError("The Working designation is invalid. Preserve the library.")
    working = (
        reference(version(db, recording_id, state["working_version_id"]))
        if state and state["working_version_id"]
        else None
    )
    fields = _bounded_fields([("next_version_id", 64), ("next_payload_sha256", 64)])
    latest = db.execute(
        f"SELECT sequence,{fields} "
        "FROM result_role_events WHERE recording_id=? ORDER BY sequence DESC LIMIT 1",
        (recording_id,),
    ).fetchone()
    if (state is None) != (latest is None) or (
        state
        and (
            latest["fields_bounded"] != 1
            or latest["sequence"] != state["revision"]
            or latest["next_version_id"] != (working["version_id"] if working else None)
            or latest["next_payload_sha256"] != (working["payload_sha256"] if working else None)
        )
    ):
        raise ValueError("The Working designation and its history disagree. Preserve the library.")
    if verify_tail:
        _verify_write_tail(db, recording_id, state, working)
    return {"revision": state["revision"] if state else 0, "working": working}


def _plan(store, db, recording_id, version_id, revision, role_revision):
    validate_revision(revision)
    validate_revision(role_revision)
    current = db.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
    if current is None:
        raise KeyError(recording_id)
    state = read(db, store, recording_id, verify_tail=True)
    if current["revision"] != revision or state["revision"] != role_revision:
        raise ValueError("The recording or Working designation changed. Preview again.")
    target = version(db, recording_id, version_id) if version_id is not None else None
    target_ref = reference(target) if target else None
    binding = {
        "format": FORMAT,
        "recording_id": recording_id,
        "revision": revision,
        "role_revision": role_revision,
        "previous": state["working"],
        "next": target_ref,
    }
    return {
        **binding,
        "fingerprint": hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "label": target["label"] if target else None,
    }


def preview(store, recording_id, version_id, revision, role_revision):
    with store.connection() as db:
        db.execute("BEGIN")
        return _plan(store, db, recording_id, version_id, revision, role_revision)


def _write(db, recording_id, state, target, action, *, actor_kind="local_user"):
    from .store import new_id, now

    previous = state["working"]
    if previous == target and action != "working_kept":
        return False
    sequence = state["revision"] + 1
    db.execute(
        "INSERT INTO recording_result_roles VALUES (?,?,?) "
        "ON CONFLICT(recording_id) DO UPDATE SET working_version_id=excluded.working_version_id,"
        "revision=excluded.revision",
        (recording_id, target["version_id"] if target else None, sequence),
    )
    db.execute(
        "INSERT INTO result_role_events VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            new_id(),
            recording_id,
            sequence,
            previous["version_id"] if previous else None,
            previous["payload_sha256"] if previous else None,
            target["version_id"] if target else None,
            target["payload_sha256"] if target else None,
            action,
            now(),
            actor_kind,
        ),
    )
    return True


def move_automatic(db, store, recording_id, target_version_id, expected, action):
    """Follow a frozen role only if the exact role state still matches."""
    if action not in AUTO_ACTIONS:
        raise ValueError("Invalid automatic Working action.")
    state = read(db, store, recording_id, verify_tail=True)
    if not frozen_roles_equal(state, expected):
        return False
    if action == "working_auto_first" and state["working"] is not None:
        raise ValueError("Automatic Working action does not match its source.")
    if action == "working_auto_edit" and state["working"] is None:
        raise ValueError("Automatic Working action does not match its source.")
    if action == "working_auto_first" and state["revision"] != 0:
        return False
    target = reference(version(db, recording_id, target_version_id))
    return _write(db, recording_id, state, target, action, actor_kind="system")


def move_consented(db, store, recording_id, target_version_id, expected):
    """Apply explicit per-edit consent only to the exact role state observed by the user."""
    state = read(db, store, recording_id, verify_tail=True)
    if not frozen_roles_equal(state, expected):
        raise ValueError("The Working designation changed. Review your choice again.")
    return _write(
        db,
        recording_id,
        state,
        reference(version(db, recording_id, target_version_id)),
        "working_marked",
    )


def keep_consented(db, store, recording_id, expected):
    """Record an explicit keep choice so delayed jobs cannot override it."""
    state = read(db, store, recording_id, verify_tail=True)
    if not frozen_roles_equal(state, expected):
        raise ValueError("The Working designation changed. Review your choice again.")
    return _write(db, recording_id, state, state["working"], "working_kept")


def apply(store, recording_id, version_id, revision, role_revision, fingerprint):
    with store.snapshot_lock:
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            plan = _plan(store, db, recording_id, version_id, revision, role_revision)
            if not isinstance(fingerprint, str) or not hmac.compare_digest(
                fingerprint, plan["fingerprint"]
            ):
                raise ValueError("The Working preview changed. Preview again.")
            _write(
                db,
                recording_id,
                {"revision": role_revision, "working": plan["previous"]},
                plan["next"],
                "working_kept"
                if plan["previous"] == plan["next"]
                else "working_marked"
                if version_id
                else "working_cleared",
            )
        recording = store.recording(recording_id)
        store.snapshot_project(recording["project_id"])
        return recording


def clear_trashed(store, db, recording_id, result_id):
    """Called in the same writer transaction, before inserting the Trash marker."""
    state = read(db, store, recording_id, verify_tail=True)
    if state["working"] and state["working"]["result_id"] == result_id:
        _write(db, recording_id, state, None, "working_trashed")


def public_history(db, store, recording_id, scope, *, payload_budget=None, verified_versions=None):
    """Verify a bounded complete role chain; expose scoped aliases and metadata only."""
    state = read(db, store, recording_id)
    if payload_budget is None:
        payload_budget = {"remaining": MAX_TRANSCRIPT_BYTES}
    rows = db.execute(
        "SELECT * FROM result_role_events WHERE recording_id=? ORDER BY sequence LIMIT ?",
        (recording_id, MAX_ROLE_EVENTS + 1),
    ).fetchall()
    if len(rows) > MAX_ROLE_EVENTS:
        raise ValueError("Working history exceeds the report's 1,000-event limit.")
    versions, payload_bytes = {}, 0

    def observed(version_id, digest):
        nonlocal payload_bytes
        if version_id is None:
            if digest is not None:
                raise ValueError("The Working history has an invalid version reference.")
            return None
        if version_id not in versions:
            saved = (verified_versions or {}).get(version_id)
            if saved is not None:
                # The report captured and decoded this exact row in the same read snapshot.
                # Do not read or charge a second copy of its transcript payload.
                if saved["id"] != version_id or saved["recording_id"] != recording_id:
                    raise ValueError("The Working version must belong to this recording.")
            else:
                size = db.execute(
                    "SELECT length(CAST(payload AS BLOB)) FROM result_versions WHERE id=?",
                    (version_id,),
                ).fetchone()
                if size is None or size[0] > payload_budget["remaining"]:
                    raise ValueError("Working history exceeds the report's content limit.")
                payload_budget["remaining"] -= size[0]
                saved = version(db, recording_id, version_id, live=False)
                payload_bytes += len(saved["payload"].encode("utf-8"))
                if payload_bytes > MAX_TRANSCRIPT_BYTES:
                    raise ValueError("Working history exceeds the report's content limit.")
            versions[version_id] = {
                "version_alias": f"{scope}-working-v{len(versions) + 1}",
                "sequence": saved["sequence"],
                "payload_sha256": saved["payload_sha256"],
            }
        ref = versions[version_id]
        if ref["payload_sha256"] != digest:
            raise ValueError("The Working history version hash changed. Preserve the library.")
        return ref

    events, previous_id = [], None
    for sequence, row in enumerate(rows, 1):
        if (
            row["sequence"] != sequence
            or row["previous_version_id"] != previous_id
            or row["action"]
            not in {"working_marked", "working_cleared", "working_trashed", "working_kept"}
            | AUTO_ACTIONS
            or row["actor_kind"] != ("system" if row["action"] in AUTO_ACTIONS else "local_user")
            or (
                row["action"] == "working_auto_first"
                and (row["previous_version_id"] is not None or sequence != 1)
            )
            or (row["action"] == "working_auto_edit" and row["previous_version_id"] is None)
            or (
                row["action"] != "working_kept"
                and (row["action"] in {"working_marked"} | AUTO_ACTIONS)
                != (row["next_version_id"] is not None)
            )
            or (row["previous_version_id"] == row["next_version_id"])
            != (row["action"] == "working_kept")
        ):
            raise ValueError("The Working history chain is invalid. Preserve the library.")
        try:
            if (
                not isinstance(row["observed_at"], str)
                or "\x00" in row["observed_at"]
                or len(row["observed_at"]) > 64
            ):
                raise ValueError()
            if datetime.fromisoformat(row["observed_at"]).tzinfo is None:
                raise ValueError()
        except ValueError:
            raise ValueError("The Working history observation time is invalid.") from None
        events.append(
            {
                "sequence": sequence,
                "previous": observed(row["previous_version_id"], row["previous_payload_sha256"]),
                "next": observed(row["next_version_id"], row["next_payload_sha256"]),
                "action": row["action"],
                "observed_at": row["observed_at"],
                "actor_kind": row["actor_kind"],
            }
        )
        previous_id = row["next_version_id"]
    if len(rows) != state["revision"]:
        raise ValueError("The Working history revision is invalid. Preserve the library.")
    return {
        "revision": state["revision"],
        "working": events[-1]["next"] if events else None,
        "events": events,
        "identity": "recorded_actor_kind_unverified",
    }

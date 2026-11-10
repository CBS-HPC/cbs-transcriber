"""Freeze and follow a Working designation for newly accepted timing requests."""

import json

from . import result_roles

KEY = "timing_working_policy"
FORMAT = "cbs-timing-working-v1"
ROLE_KEY = "working_role"
ROLE_POLICY_KEY = "working_role_policy"
ROLE_POLICY = "cbs-working-role-v1"
MAX_CHAIN_EVENTS = 16
MAX_CHAIN_BYTES = 16 * 1024 * 1024
FOLLOW_WARNING = "Word timing was saved, but Working was kept. Review the Working version."


def _same(left, right):
    """Canonical hashing retains JSON numeric and boolean types."""
    return result_roles.source_snapshot_hash(left) == result_roles.source_snapshot_hash(right)


def freeze(store, db, recording_id, params):
    """Capture role state in the transaction that accepts the frozen source."""
    if {KEY, "realign_applied", "realign_receipt", "realign_apply_receipt"} & params.keys():
        raise ValueError("Timing Working evidence is created only when work is accepted.")
    return {
        **params,
        KEY: {"format": FORMAT, "expected_role": result_roles.read(db, store, recording_id)},
    }


def validate_authorized_params(authority, policy, params):
    """Require job parameters to match their separate trusted acceptance receipt."""
    if policy not in {"result", "timing"} or not isinstance(params, dict):
        raise ValueError("The frozen Working job parameters are invalid.")
    source = params.get("source_snapshot")
    if authority["source_snapshot_sha256"] is None:
        if source is not None:
            raise ValueError("The frozen Working source differs from its accepted receipt.")
    elif (
        not isinstance(source, dict)
        or source.get("source_version_id") != authority["source_version_id"]
        or source.get("source_payload_sha256") != authority["source_payload_sha256"]
        or result_roles.source_snapshot_hash(source) != authority["source_snapshot_sha256"]
    ):
        raise ValueError("The frozen Working source differs from its accepted receipt.")
    if policy == "timing":
        if not _same(params.get(KEY), {"format": FORMAT, "expected_role": authority["role"]}):
            raise ValueError("The frozen timing Working policy differs from its receipt.")
    elif policy == "result":
        if authority["admission_sha256"] is None:
            if params.get(ROLE_POLICY_KEY) != ROLE_POLICY or not _same(
                params.get(ROLE_KEY), authority["role"]
            ):
                raise ValueError("The frozen processing Working policy differs from its receipt.")
        else:
            accepted = params.get("input_acceptance")
            if (
                not isinstance(accepted, dict)
                or accepted.get("accepted_sha256") != authority["admission_sha256"]
                or not _same(accepted.get("working_role"), authority["role"])
            ):
                raise ValueError("The frozen Working admission differs from its receipt.")


def _timing_receipt(db, recording_id, result_id, job, previous, successor):
    """Reconstruct a prior atomic timing Apply from its durable job receipt."""
    from . import local_timing, repair_cloud
    from .history import decode_payload, encode_payload

    params = job["params"]
    if params.get("realign") is not True or params.get("realign_applied") != "applied":
        return False
    frozen, outputs, digest = local_timing._inputs(db, recording_id, result_id, params)
    if "realign_apply_receipt" in params:
        if job["state"] != "completed":
            return False
        receipt = local_timing._existing(db, recording_id, result_id, job, params, digest)
        return (
            "working_choice" not in receipt
            and "working_follow_warning" not in receipt
            and receipt["previous"]["id"] == previous["id"]
            and receipt["previous"]["payload_sha256"] == previous["payload_sha256"]
            and receipt["version"]["id"] == successor["id"]
            and receipt["version"]["payload_sha256"] == successor["payload_sha256"]
            and bool(receipt["applied_ids"])
        )
    receipt = params.get("realign_receipt")
    run = params.get("ucloud_run", {})
    repair = run.get("repair", {}) if isinstance(run, dict) else {}
    if (
        not isinstance(receipt, dict)
        or "working_choice" in receipt
        or "working_follow_warning" in receipt
        or job["state"] not in {"completed", "failed", "cancelled"}
        or params.get("repair_cloud", {}).get("protocol") != repair_cloud.PROTOCOL
        or run.get("remote_state") not in repair_cloud.api.TERMINAL
        or repair.get("published") is not True
        or receipt.get("fingerprint") != repair_cloud.application_fingerprint(job)
        or receipt.get("version")
        != {"id": successor["id"], "payload_sha256": successor["payload_sha256"]}
        or type(receipt.get("base_revision")) is not int
        or receipt["base_revision"] < 0
        or type(receipt.get("applied_revision")) is not int
        or receipt.get("applied_revision") != receipt["base_revision"] + 1
    ):
        return False
    payload = decode_payload(previous["payload"], previous["payload_sha256"])
    segments, applied, skipped = local_timing._successor(payload["segments"], frozen, outputs)
    return (
        bool(applied)
        and receipt.get("applied_ids") == applied
        and receipt.get("skipped_ids") == skipped
        and encode_payload(segments, payload["speakers"])[0] == successor["payload"]
        and decode_payload(successor["payload"], successor["payload_sha256"]) is not None
    )


def _version_size(db, identifier, result_id):
    """Bound every variable-width column before loading a historical version."""
    row = db.execute(
        "SELECT length(CAST(payload AS BLOB)),length(CAST(context AS BLOB)) "
        "FROM result_versions WHERE id=? AND result_id=? "
        "AND length(id)<=64 AND length(result_id)<=64 "
        "AND length(COALESCE(predecessor_id,''))<=64 "
        "AND length(COALESCE(source_version_id,''))<=64 "
        "AND length(payload_sha256)<=128 AND length(observed_at)<=64 "
        "AND length(action)<=64 AND length(actor_kind)<=64 "
        "AND length(COALESCE(reason,''))<=2000",
        (identifier, result_id),
    ).fetchone()
    if row is None or any(type(size) is not int or size < 0 for size in row):
        return None
    return sum(row)


def _timing_chain(db, recording_id, expected, current, source, head):
    """Prove a bounded chain of automatic timing-only successors from one frozen request."""
    count = current["revision"] - expected["revision"]
    if not 0 < count <= MAX_CHAIN_EVENTS or not current["working"]:
        return False
    events = db.execute(
        "SELECT * FROM result_role_events WHERE recording_id=? AND sequence>? AND sequence<=? "
        "AND length(id)<=64 AND length(recording_id)<=64 "
        "AND length(COALESCE(previous_version_id,''))<=64 "
        "AND length(COALESCE(next_version_id,''))<=64 "
        "AND length(COALESCE(previous_payload_sha256,''))<=128 "
        "AND length(COALESCE(next_payload_sha256,''))<=128 "
        "AND length(action)<=64 AND length(actor_kind)<=64 AND length(observed_at)<=64 "
        "ORDER BY sequence LIMIT ?",
        (recording_id, expected["revision"], current["revision"], MAX_CHAIN_EVENTS + 1),
    ).fetchall()
    if len(events) != count:
        return False
    root_id = source["source_version_id"]
    root_hash = source["source_payload_sha256"]
    result_id = source["id"]
    previous_id, previous_hash = root_id, root_hash
    role_previous = expected["working"]
    role_id = role_previous["version_id"] if role_previous else None
    role_hash = role_previous["payload_sha256"] if role_previous else None
    remaining = MAX_CHAIN_BYTES
    for offset, event in enumerate(events, 1):
        if (
            event["sequence"] != expected["revision"] + offset
            or event["action"] != "working_auto_successor"
            or event["actor_kind"] != "system"
            or event["previous_version_id"] != role_id
            or event["previous_payload_sha256"] != role_hash
            or not event["next_version_id"]
        ):
            return False
        meta = db.execute(
            "SELECT id,result_id,predecessor_id,payload_sha256,action,"
            "length(CAST(payload AS BLOB)) AS payload_size,"
            "length(CAST(context AS BLOB)) AS context_size FROM result_versions WHERE id=? "
            "AND length(id)<=64 AND length(result_id)<=64 "
            "AND length(COALESCE(predecessor_id,''))<=64 "
            "AND length(payload_sha256)<=128 AND length(action)<=64",
            (event["next_version_id"],),
        ).fetchone()
        next_size = _version_size(db, event["next_version_id"], result_id)
        previous_size = _version_size(db, previous_id, result_id)
        root_size = _version_size(db, root_id, result_id)
        if (
            meta is None
            or next_size is None
            or previous_size is None
            or root_size is None
            or meta["result_id"] != result_id
            or meta["predecessor_id"] != previous_id
            or meta["payload_sha256"] != event["next_payload_sha256"]
            or meta["action"] not in {"timing_realign", "save_no_change"}
            or any(
                type(value) is not int or value < 0
                for value in (meta["payload_size"], meta["context_size"], previous_size, root_size)
            )
        ):
            return False
        remaining -= 2 * (next_size + previous_size + root_size)
        if remaining < 0:
            return False
        context_row = db.execute(
            "SELECT context FROM result_versions WHERE id=?", (meta["id"],)
        ).fetchone()
        context = json.loads(context_row[0])
        if not isinstance(context, dict) or not isinstance(context.get("job_id"), str):
            return False
        if (
            meta["action"] == "save_no_change"
            and context.get("requested_action") != "timing_realign"
        ):
            return False
        job_id = context["job_id"]
        if len(job_id) != 32:
            return False
        job_size = db.execute(
            "SELECT length(CAST(params AS BLOB)) FROM jobs WHERE id=? AND recording_id=? "
            "AND kind='enhance' AND length(state)<=64",
            (job_id, recording_id),
        ).fetchone()
        if job_size is None or type(job_size[0]) is not int or job_size[0] < 0:
            return False
        remaining -= 2 * job_size[0]
        if remaining < 0:
            return False
        job = dict(
            db.execute(
                "SELECT id,recording_id,kind,state,params FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        )
        job["params"] = json.loads(job["params"])
        from . import restored_owners

        restored_owners.require_owned(db, "job", job_id)
        authority = result_roles.job_authority(db, job_id, recording_id, "timing")
        if authority is None or not _same(authority["role"], expected):
            return False
        validate_authorized_params(authority, "timing", job["params"])
        if (
            authority["source_version_id"] != root_id
            or authority["source_payload_sha256"] != root_hash
        ):
            return False
        previous = db.execute("SELECT * FROM result_versions WHERE id=?", (previous_id,)).fetchone()
        successor = db.execute("SELECT * FROM result_versions WHERE id=?", (meta["id"],)).fetchone()
        if (
            previous is None
            or successor is None
            or previous["payload_sha256"] != previous_hash
            or type(previous["sequence"]) is not int
            or successor["sequence"] != previous["sequence"] + 1
            or successor["source_version_id"] != previous["source_version_id"]
            or successor["actor_kind"] != "local_user"
            or not _timing_receipt(db, recording_id, result_id, job, previous, successor)
        ):
            return False
        previous_id, previous_hash = meta["id"], meta["payload_sha256"]
        role_id, role_hash = previous_id, previous_hash
    return (
        current["working"]["version_id"] == head["id"] == previous_id
        and current["working"]["payload_sha256"] == head["payload_sha256"] == previous_hash
        and current["working"]["result_id"] == result_id
    )


def follow(store, db, recording_id, job_id, params, head, successor):
    """Follow only an unchanged role and the exact frozen head that was timed."""
    authority = result_roles.job_authority(db, job_id, recording_id, "timing")
    if authority is None:
        return False  # Formerly arbitrary legacy fields confer no authority.
    validate_authorized_params(authority, "timing", params)
    policy = params[KEY]
    if not isinstance(policy, dict) or set(policy) != {"format", "expected_role"}:
        raise ValueError("The frozen timing Working policy is invalid.")
    expected = policy["expected_role"]
    if (
        policy["format"] != FORMAT
        or not isinstance(expected, dict)
        or set(expected) != {"revision", "working"}
        or type(expected["revision"]) is not int
        or expected["revision"] < 0
        or expected["working"] is not None
        and (
            not isinstance(expected["working"], dict)
            or set(expected["working"]) != {"version_id", "result_id", "sequence", "payload_sha256"}
        )
    ):
        raise ValueError("The frozen timing Working policy is invalid.")
    source = params.get("source_snapshot")
    if not isinstance(source, dict):
        raise ValueError("The frozen timing source is invalid.")
    if head["result_id"] != source.get("id"):
        return False
    if head["id"] != source.get("source_version_id") or head["payload_sha256"] != source.get(
        "source_payload_sha256"
    ):
        current = result_roles.read(db, store, recording_id)
        try:
            if not _timing_chain(db, recording_id, expected, current, source, head):
                return False
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
            return False
        expected = current
    return result_roles.move_automatic(
        db, store, recording_id, successor["id"], expected, "working_auto_successor"
    )


def validate_choice_receipt(db, recording_id, receipt, predecessor_id, successor):
    """Validate explicitly confirmed choices without replaying any role write."""
    if "working_choice" not in receipt:
        return
    choice = receipt["working_choice"]
    if (
        not isinstance(choice, dict)
        or set(choice) != {"version_id", "role_revision", "make_working"}
        or choice["version_id"] != predecessor_id
        or type(choice["role_revision"]) is not int
        or not 0 <= choice["role_revision"] < 2**63 - 1
        or type(choice["make_working"]) is not bool
    ):
        raise ValueError("The applied timing Working choice is invalid. Preserve its history.")
    event = db.execute(
        "SELECT * FROM result_role_events WHERE recording_id=? AND sequence=?",
        (recording_id, choice["role_revision"] + 1),
    ).fetchone()
    if event is None or event["actor_kind"] != "local_user":
        raise ValueError("The applied timing Working choice failed its history check.")
    if choice["make_working"]:
        valid = (
            event["action"] == "working_marked"
            and event["next_version_id"] == successor["id"]
            and event["next_payload_sha256"] == successor["payload_sha256"]
        )
    else:
        valid = (
            event["action"] == "working_kept"
            and event["previous_version_id"] == event["next_version_id"]
            and event["previous_payload_sha256"] == event["next_payload_sha256"]
        )
    if not valid:
        raise ValueError("The applied timing Working choice failed its history check.")

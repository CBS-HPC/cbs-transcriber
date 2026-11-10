"""Conservative manual retention: preview exact owned backups, then move them to Trash.

This records an operator's technical choices. It never asserts CBS IT/DPO approval, schedules
deletion, deletes local packages, purges UCloud Trash or claims permanent erasure.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from . import portable_cloud as cloud
from . import portable_transport as transport
from .portable_capture import _parents, canonical, durable
from .portable_restore import _journal, operation_lock

DEFAULT = {"keep_last": 3, "minimum_age_days": 30, "holds": []}


def _policy(value):
    if type(value) is not dict or set(value) != set(DEFAULT):
        raise ValueError("The manual backup retention policy is invalid.")
    transport._integer(value["keep_last"], 1000, minimum=1)
    transport._integer(value["minimum_age_days"], 36500, minimum=1)
    if (
        type(value["holds"]) is not list
        or len(value["holds"]) > 10000
        or len(set(value["holds"])) != len(value["holds"])
    ):
        raise ValueError("The backup hold list is invalid.")
    for identifier in value["holds"]:
        transport._hex(identifier, 32)
    return {**value, "holds": sorted(value["holds"])}


def load_policy(store):
    path = store.root / "portable-retention.json"
    if not path.exists():
        return dict(DEFAULT, holds=[])
    raw, _ = transport._read(path, store.root, maximum=1024 * 1024, collect=True)
    return _policy(json.loads(raw))


def save_policy(store, value):
    value = _policy(value)
    root = _parents(store.root / "portable-retention-events")
    root.mkdir(exist_ok=True)
    with operation_lock(root / "policy.lock"):
        _journal(store.root / "portable-retention.json", value)
    return {"policy": value, "mode": "manual_trash_only", "institutional_approval": "not_checked"}


def _records(store):
    root = store.root / "portable-transfers"
    if not root.exists():
        return []
    root = _parents(root)
    names, _ = transport._entries(root, root, maximum=20000, guard=None)
    output, folders = [], set()
    for name in sorted(names):
        if not name.endswith(".json"):
            continue
        transport._hex(name[:-5], 32)
        raw, _ = transport._read(root / name, root, maximum=16 * 1024**2, collect=True)
        value = json.loads(raw)
        if value.get("format") != "cbs-portable-transfer-v1" or value.get("state") != "complete":
            continue
        if value.get("operation_id") != name[:-5]:
            raise ValueError("A backup transfer receipt has invalid operation ownership.")
        transport._hex(value["backup_id"], 32)
        transport._hex(value["commit_sha256"], 64)
        if (
            value["remote_folder"]
            != value["target"]["parent"] + "/cbs-backup-" + value["backup_id"]
        ):
            raise ValueError("A backup transfer receipt has invalid folder ownership.")
        if value["remote_folder"] in folders:
            raise ValueError(
                "Multiple receipts claim the same remote backup folder; resolve ownership first."
            )
        folders.add(value["remote_folder"])
        output.append(value)
    if len(output) > 1000:
        raise ValueError("The backup retention inventory exceeds the supported review limit.")
    return sorted(output, key=lambda item: (item["completed_at"], item["backup_id"]), reverse=True)


def preview(
    store, credentials, *, now=None, guard=None, expected_target=None, expected_revision=None
):
    now = now or datetime.now(UTC)
    policy = load_policy(store)
    target = (
        cloud.checked_target(store, expected_revision, expected_target["fingerprint"])
        if expected_target is not None
        else cloud.load_target(store)
    )
    if expected_target is not None and target != expected_target:
        raise cloud.StaleBackupStorage(
            "Backup storage changed. Reopen Library and review it again."
        )
    if target is None:
        raise ValueError("Configure backup storage before reviewing retention.")
    rows, ready, current_rank = [], [], 0
    for record in _records(store):
        transport._guard(guard)
        reason = None
        if (
            record["target"]["server"] != target["server"]
            or record["target"]["project_id"] != target["project_id"]
            or record["target"]["parent"] != target["parent"]
        ):
            reason = "another_destination"
        elif record["backup_id"] in policy["holds"]:
            reason = "held"
        elif current_rank < policy["keep_last"]:
            reason = "recent_copy_kept"
        elif datetime.fromisoformat(record["completed_at"]) > now - timedelta(
            days=policy["minimum_age_days"]
        ):
            reason = "minimum_age_not_reached"
        if reason != "another_destination":
            current_rank += 1
        if reason is None:
            try:
                capture = transport._capture_transport(
                    record["local_package"],
                    expected_commit_sha256=record["commit_sha256"],
                    guard=guard,
                )
            except (OSError, ValueError):
                reason = "independent_local_copy_unavailable"
            else:
                with cloud._connection(store, credentials, target, upload=False) as client:
                    cloud._download(
                        client,
                        target,
                        record["remote_folder"] + "/COMMITTED.json",
                        maximum=transport.LIMITS["commit_bytes"],
                        digest=record["commit_sha256"],
                        guard=guard,
                    )
                    if cloud._names(client, record["remote_folder"]) != {
                        "objects",
                        "manifest.json",
                        "COMMITTED.json",
                    } or cloud._names(client, record["remote_folder"] + "/objects") != set(
                        capture.object_names
                    ):
                        raise ValueError(
                            "The owned remote backup inventory changed; retention was refused."
                        )
                transport._recheck(capture, guard)
                ready.append(record["operation_id"])
        rows.append(
            {
                "operation_id": record["operation_id"],
                "backup_id": record["backup_id"],
                "completed_at": record["completed_at"],
                "commit_sha256": record["commit_sha256"],
                "remote_folder": record["remote_folder"],
                "eligible": reason is None,
                "reason": reason,
            }
        )
    value = {
        "format": "cbs-portable-retention-preview-v1",
        "policy": policy,
        "target_fingerprint": target["fingerprint"],
        "rows": rows,
        "eligible_operations": ready,
        "mode": "manual_trash_only",
        "institutional_approval": "not_checked",
        "trash_erasure": "not_permanent",
    }
    return {**value, "fingerprint": hashlib.sha256(canonical(value)).hexdigest()}


def _target_unchanged(store, target):
    current = cloud.load_target(store)
    if target is None or current is None or current["fingerprint"] != target["fingerprint"]:
        raise ValueError("Backup storage or approval changed. Review a fresh retention preview.")


def _trash_row(store, client, row, event, event_id, target):
    path = store.root / "portable-transfers" / (row["operation_id"] + ".json")
    with operation_lock(path.with_suffix(".lock")):
        raw, _ = transport._read(path, store.root, maximum=16 * 1024**2, collect=True)
        record = json.loads(raw)
        if (
            record["state"] != "complete"
            or any(
                record[key] != row[key] for key in ("backup_id", "commit_sha256", "remote_folder")
            )
            or any(
                record["target"][key] != target[key] for key in ("server", "project_id", "parent")
            )
        ):
            raise ValueError("The backup changed after the retention preview. Review it again.")
        _target_unchanged(store, target)
        try:
            cloud.native.trash_owned_folder(client, row["remote_folder"])
            outcome = {"backup_id": row["backup_id"], "state": "moved_to_trash"}
        except (ValueError, OSError, httpx.HTTPError):
            outcome = {"backup_id": row["backup_id"], "state": "trash_outcome_uncertain"}
        durable(event / (row["backup_id"] + ".json"), canonical(outcome))
        record["state"] = (
            "trashed" if outcome["state"] == "moved_to_trash" else "retention_uncertain"
        )
        record["retention_event"] = event_id
        _journal(path, record)
        return outcome


def apply(store, credentials, *, fingerprint, confirmed, guard=None):
    if confirmed is not True:
        raise ValueError(
            "Explicit confirmation is required to move the previewed backups to Trash."
        )
    transport._hex(fingerprint, 64)
    root = _parents(store.root / "portable-retention-events")
    root.mkdir(exist_ok=True)
    with operation_lock(root / "policy.lock"):
        target = cloud.load_target(store)
        current = preview(store, credentials, guard=guard)
        if (
            current["fingerprint"] != fingerprint
            or target is None
            or current["target_fingerprint"] != target["fingerprint"]
        ):
            raise ValueError(
                "The backup retention preview changed. Review a fresh preview before applying."
            )
        identifier = uuid.uuid4().hex
        event = root / identifier
        event.mkdir()
        intent = {
            "format": "cbs-portable-retention-event-v1",
            "event_id": identifier,
            "preview": current,
            "target": target,
            "action": "move_owned_backups_to_trash",
            "confirmed_at": datetime.now(UTC).isoformat(),
        }
        durable(event / "intent.json", canonical(intent))
        outcomes = []
        with cloud._connection(store, credentials, target, upload=False) as client:
            for row in current["rows"]:
                if not row["eligible"]:
                    continue
                transport._guard(guard)
                # Every receipt has a fresh marker/inventory and independent-copy check in
                # this same locked preview. Journal intent is durable before the POST.
                outcomes.append(_trash_row(store, client, row, event, identifier, target))
        return {"event_id": identifier, "outcomes": outcomes, "permanent_erasure": False}


def events(store):
    root = store.root / "portable-retention-events"
    if not root.exists():
        return []
    output = []
    names, _ = transport._entries(root, store.root, maximum=10000, guard=None)
    for name in sorted(names):
        if name == "policy.lock":
            continue
        transport._hex(name, 32)
        directory = root / name
        raw, _ = transport._read(
            directory / "intent.json", store.root, maximum=4 * 1024**2, collect=True
        )
        intent = json.loads(raw)
        attempts = []
        for leaf in transport._entries(directory, store.root, maximum=10000, guard=None)[0]:
            if leaf.startswith("retry-") and leaf.endswith(".json"):
                transport._hex(leaf[6:-5], 32)
                body, _ = transport._read(
                    directory / leaf, store.root, maximum=64 * 1024, collect=True
                )
                attempts.append(json.loads(body))
        outcomes = []
        for row in intent["preview"]["rows"]:
            if not row["eligible"]:
                continue
            path = directory / (row["backup_id"] + ".json")
            if path.exists():
                body, _ = transport._read(path, store.root, maximum=4096, collect=True)
                outcome = json.loads(body)
            else:
                outcome = {"backup_id": row["backup_id"], "state": "trash_action_needs_review"}
            retries = [item for item in attempts if item["backup_id"] == row["backup_id"]]
            if retries:
                outcome = max(retries, key=lambda item: item["observed_at"])
            outcomes.append(outcome)
        output.append(
            {"event_id": name, "confirmed_at": intent["confirmed_at"], "outcomes": outcomes}
        )
    return output


def retry_preview(store, credentials, event_id, backup_id, *, guard=None):
    transport._hex(event_id, 32)
    transport._hex(backup_id, 32)
    root = store.root / "portable-retention-events"
    raw, _ = transport._read(
        root / event_id / "intent.json", store.root, maximum=4 * 1024**2, collect=True
    )
    intent = json.loads(raw)
    policy = load_policy(store)
    if policy != intent["preview"]["policy"] or backup_id in policy["holds"]:
        raise ValueError(
            "Retention policy or holds changed. This earlier Trash intent cannot be retried."
        )
    selected = [
        row
        for row in intent["preview"]["rows"]
        if row["eligible"] and row["backup_id"] == backup_id
    ]
    if len(selected) != 1:
        raise ValueError("This retention event does not own that backup action.")
    row = selected[0]
    raw, _ = transport._read(
        store.root / "portable-transfers" / (row["operation_id"] + ".json"),
        store.root,
        maximum=16 * 1024**2,
        collect=True,
    )
    record = json.loads(raw)
    if any(record[key] != row[key] for key in ("backup_id", "commit_sha256", "remote_folder")):
        raise ValueError("The backup retention ownership receipt changed.")
    capture = transport._capture_transport(
        record["local_package"], expected_commit_sha256=record["commit_sha256"], guard=guard
    )
    target = cloud.load_target(store)
    with cloud._connection(store, credentials, record["target"], upload=False) as client:
        present = cloud.native.folder_listed(client, row["remote_folder"])
        if present:
            cloud._download(
                client,
                record["target"],
                row["remote_folder"] + "/COMMITTED.json",
                maximum=transport.LIMITS["commit_bytes"],
                digest=row["commit_sha256"],
                guard=guard,
            )
            if cloud._names(client, row["remote_folder"]) != {
                "objects",
                "manifest.json",
                "COMMITTED.json",
            } or cloud._names(client, row["remote_folder"] + "/objects") != set(
                capture.object_names
            ):
                raise ValueError("The owned backup inventory changed; Trash retry was refused.")
    transport._recheck(capture, guard)
    _target_unchanged(store, target)
    result = {
        "event_id": event_id,
        "backup_id": backup_id,
        "remote_folder": row["remote_folder"],
        "commit_sha256": row["commit_sha256"],
        "operation_id": row["operation_id"],
        "target": target,
        "observed": "owned_folder_present" if present else "folder_not_listed_after_trash_intent",
    }
    return {**result, "fingerprint": hashlib.sha256(canonical(result)).hexdigest()}


def retry(store, credentials, event_id, backup_id, *, fingerprint, confirmed, guard=None):
    if confirmed is not True:
        raise ValueError("Confirm a fresh preview before retrying the recorded Trash action.")
    root = _parents(store.root / "portable-retention-events")
    with operation_lock(root / "policy.lock"):
        previewed = retry_preview(store, credentials, event_id, backup_id, guard=guard)
        if previewed["fingerprint"] != fingerprint:
            raise ValueError("The Trash retry preview changed. Review it again.")
        path = store.root / "portable-transfers" / (previewed["operation_id"] + ".json")
        with operation_lock(path.with_suffix(".lock")):
            _target_unchanged(store, previewed["target"])
            if previewed["observed"] == "owned_folder_present":
                with cloud._connection(
                    store, credentials, previewed["target"], upload=False
                ) as client:
                    _target_unchanged(store, previewed["target"])
                    cloud.native.trash_owned_folder(client, previewed["remote_folder"])
                state = "moved_to_trash"
            else:
                state = "folder_not_listed_after_trash_intent"
            result = {
                "backup_id": backup_id,
                "state": state,
                "preview": previewed,
                "observed_at": datetime.now(UTC).isoformat(),
            }
            durable(root / event_id / ("retry-" + uuid.uuid4().hex + ".json"), canonical(result))
            raw, _ = transport._read(path, store.root, maximum=16 * 1024**2, collect=True)
            record = json.loads(raw)
            record["state"] = "trashed"
            record["retention_status"] = state
            record["retention_event"] = event_id
            _journal(path, record)
        return result

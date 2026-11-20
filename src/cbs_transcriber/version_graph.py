"""Branch view: a bounded metadata graph of a recording's results and versions (ADR 0182).

Read-only and text-free. Each saved result is a lane of its versions; a derived result
branches from the exact source version it was built from; Apply safe updates, conflict
decisions and wording adoption draw edges from their recorded incoming/source versions.
Only references that verify against this recording are drawn; gaps stay visible.
"""

import json

from . import result_roles
from .history import conflict_review_metadata, merge_metadata, wording_adoption_metadata

MAX_RESULTS = 50
MAX_VERSIONS = 200
MAX_CONTEXT_BYTES = 64 * 1024
LINKED = ("result_merge", "result_conflict_review", "wording_adopt", "change_undo", "change_redo")


def _context(row):
    try:
        value = json.loads(row["context"]) if row["context"] is not None else None
    except (TypeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _incoming(action, context):
    """The other version this one combined, from validated evidence only."""
    try:
        if action == "result_merge":
            return "merge", merge_metadata(context.get("merge"))["incoming"]
        if action == "result_conflict_review":
            return "merge", conflict_review_metadata(context.get("conflict_review"))["incoming"]
        if action == "wording_adopt":
            evidence = wording_adoption_metadata(context.get("wording_adoption"))
            return "wording", evidence["source"]
    except (KeyError, TypeError, ValueError, RecursionError):
        return None
    return None


def graph(store, recording_id):
    with store.connection() as db:
        db.execute("BEGIN")
        recording = db.execute(
            "SELECT id,active_transcript_id FROM recordings WHERE id=?", (recording_id,)
        ).fetchone()
        if recording is None:
            raise KeyError(recording_id)
        working = result_roles.read(db, store, recording_id)["working"]
        rows = db.execute(
            "SELECT t.id,t.label,t.source,t.created,"
            "EXISTS(SELECT 1 FROM result_trash WHERE result_id=t.id) AS trashed,"
            "(SELECT COUNT(*) FROM result_versions WHERE result_id=t.id) AS total "
            "FROM transcript_results t WHERE t.recording_id=? ORDER BY t.created,t.id LIMIT ?",
            (recording_id, MAX_RESULTS + 1),
        ).fetchall()
        limited = len(rows) > MAX_RESULTS
        results, owners, pending = [], {}, []
        for row in rows[:MAX_RESULTS]:
            versions = db.execute(
                "SELECT id,sequence,predecessor_id,source_version_id,action,observed_at,"
                "CASE WHEN (action IN (?,?,?,?,?) OR sequence=1) "
                "AND length(CAST(context AS BLOB))<=? THEN context END AS context "
                "FROM result_versions WHERE result_id=? ORDER BY sequence DESC LIMIT ?",
                (*LINKED, MAX_CONTEXT_BYTES, row["id"], MAX_VERSIONS),
            ).fetchall()
            nodes = []
            for version in reversed(versions):
                owners[version["id"]] = row["id"]
                node = {
                    "id": version["id"],
                    "sequence": version["sequence"],
                    "action": version["action"],
                    "observed_at": version["observed_at"],
                }
                context = _context(version) or {}
                if version["action"] == "change_undo" and isinstance(
                    context.get("undone_version_id"), str
                ):
                    node["undone_version_id"] = context["undone_version_id"]
                if version["action"] == "change_redo" and isinstance(
                    context.get("redone_version_id"), str
                ):
                    node["redone_version_id"] = context["redone_version_id"]
                incoming = _incoming(version["action"], context)
                if incoming is not None:
                    pending.append((incoming, row["id"], version["id"]))
                nodes.append(node)
            branch = None
            first = nodes[0] if nodes and nodes[0]["sequence"] == 1 else None
            if first is not None:
                source = versions[-1]
                context = _context(source) or {}
                if source["source_version_id"]:
                    owner = db.execute(
                        "SELECT v.result_id FROM result_versions v "
                        "JOIN transcript_results r ON r.id=v.result_id "
                        "WHERE v.id=? AND r.recording_id=?",
                        (source["source_version_id"], recording_id),
                    ).fetchone()
                    branch = (
                        {"result_id": owner[0], "version_id": source["source_version_id"]}
                        if owner
                        else "unknown"
                    )
                elif context.get("source_version_unknown") is True:
                    branch = "unknown"
            try:
                origin = json.loads(row["source"])
            except (TypeError, ValueError, RecursionError):
                origin = {}
            results.append(
                {
                    "id": row["id"],
                    "label": row["label"],
                    "source": {
                        key: origin[key]
                        for key in ("kind", "model")
                        if isinstance(origin, dict) and isinstance(origin.get(key), str)
                    },
                    "created": row["created"],
                    "trashed": bool(row["trashed"]),
                    "total_versions": row["total"],
                    "truncated": row["total"] > len(nodes),
                    "branch": branch,
                    "versions": nodes,
                }
            )
        edges = []
        for (kind, reference), result_id, version_id in pending:
            owner = owners.get(reference["version_id"])
            if owner is None:
                # Outside the shown window or another recording: verify ownership directly.
                found = db.execute(
                    "SELECT v.result_id FROM result_versions v "
                    "JOIN transcript_results r ON r.id=v.result_id "
                    "WHERE v.id=? AND v.result_id=? AND r.recording_id=?",
                    (reference["version_id"], reference["result_id"], recording_id),
                ).fetchone()
                if found is None:
                    continue
                owner = found[0]
            if owner != reference["result_id"]:
                continue
            edges.append(
                {
                    "kind": kind,
                    "from": {"result_id": owner, "version_id": reference["version_id"]},
                    "to": {"result_id": result_id, "version_id": version_id},
                }
            )
    return {
        "recording_id": recording_id,
        "active_result_id": recording["active_transcript_id"],
        "working": None
        if working is None
        else {"result_id": working["result_id"], "version_id": working["version_id"]},
        "results": results,
        "edges": edges,
        "limited": limited,
    }

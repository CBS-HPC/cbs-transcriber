"""Undo a verified individual change without restoring the rest of its old snapshot."""

import json
from itertools import pairwise

from .change_undo import MAX_LINK_CONTEXT_BYTES, _unavailable, _validate_request
from .passage_undo import MAX_HISTORY_VERSIONS, MAX_SNAPSHOT_BYTES, Canonical, _ids, _read_current
from .result_actions import append_edit

UNDO, REDO = "passage_change_undo", "passage_change_redo"
SINGLE_PASSAGE = {
    "free_rewrite",
    "word_correction",
    "word_timing_edit",
    "original_restore",
    "passage_speaker",
}
BOUNDARY = "Earlier changes cross a passage history boundary. Use the recording toolbar or History."
STALE = "The passage history changed. Open its Undo and Redo controls again."


def _history(db, result_id, head, segment_id):
    """Reconstruct the selected passage's undo/redo stacks from a bounded linear tail."""
    rows = db.execute(
        "SELECT id,sequence,predecessor_id,action,payload_sha256,observed_at,"
        "CASE WHEN length(CAST(context AS BLOB))<=? THEN context END AS local_context "
        "FROM result_versions WHERE result_id=? AND sequence>? AND sequence<=? "
        "ORDER BY sequence",
        (
            MAX_LINK_CONTEXT_BYTES,
            result_id,
            head["sequence"] - MAX_HISTORY_VERSIONS,
            head["sequence"],
        ),
    ).fetchall()
    if not rows or rows[-1]["id"] != head["id"]:
        raise ValueError(STALE)
    undo, redo = [], []
    boundary = rows[0]["sequence"] > 1
    for previous, version in pairwise(rows):
        if (
            version["predecessor_id"] != previous["id"]
            or version["sequence"] != previous["sequence"] + 1
        ):
            raise ValueError("The saved history is incomplete. Use History.")
        if version["payload_sha256"] == previous["payload_sha256"]:
            continue
        action = version["action"]
        try:
            context = json.loads(version["local_context"] or "{}")
        except (TypeError, ValueError, RecursionError):
            context = {}
        scope = context.get("segment_id") if isinstance(context, dict) else None
        if action not in SINGLE_PASSAGE | {UNDO, REDO} or not isinstance(scope, str) or not scope:
            undo.clear()
            redo.clear()
            boundary = True
            continue
        if scope != segment_id:
            continue
        if action in SINGLE_PASSAGE:
            undo.append((previous, version))
            redo.clear()
        else:
            source, destination = (undo, redo) if action == UNDO else (redo, undo)
            if not source or source[-1][1]["id"] != context.get("passage_change_version_id"):
                undo.clear()
                redo.clear()
                boundary = True
                continue
            destination.append(source.pop())
    return {"undo": undo[-1] if undo else None, "redo": redo[-1] if redo else None}, boundary


def _position(payload, segment_id):
    ids = _ids(payload["segments"])
    if segment_id not in ids:
        raise ValueError("This passage is no longer present in the saved result.")
    return ids, ids.index(segment_id)


def _snapshot(db, result_id, row, canonical):
    stored = db.execute(
        "SELECT payload,payload_sha256 FROM result_versions WHERE id=? AND result_id=? "
        "AND length(CAST(payload AS BLOB))<=?",
        (row["id"], result_id, MAX_SNAPSHOT_BYTES),
    ).fetchone()
    if stored is None or stored["payload_sha256"] != row["payload_sha256"]:
        raise ValueError(
            "This saved version is unavailable for passage Undo and Redo. Use History."
        )
    return canonical.read(stored["payload"], stored["payload_sha256"])


def _proved_replacement(db, result_id, pair, ids, index, current, kind, canonical):
    """Prove the original change was local, then verify its expected half against today's row."""
    previous, version = pair
    before = _snapshot(db, result_id, previous, canonical)
    after = _snapshot(db, result_id, version, canonical)
    old, new = before["segments"], after["segments"]
    if _ids(old) != ids or _ids(new) != ids or before["speakers"] != after["speakers"]:
        raise ValueError(BOUNDARY)
    if not canonical.same(old, new[:index] + [old[index]] + new[index + 1 :]):
        raise ValueError(BOUNDARY)
    expected, replacement = (new[index], old[index]) if kind == "undo" else (old[index], new[index])
    if canonical.same(expected, replacement) or not canonical.same(current, expected):
        raise ValueError(BOUNDARY)
    return replacement


def options(store, recording_id, result_id, segment_id):
    with store.connection() as db:
        db.execute("BEGIN")
        canonical = Canonical()
        current, head, payload = _read_current(
            store, db, recording_id, result_id, canonical=canonical
        )
        ids, index = _position(payload, segment_id)
        steps, boundary = _history(db, result_id, head, segment_id)
        response = {
            "result_id": result_id,
            "segment_id": segment_id,
            "revision": current["revision"],
            "head_version_id": head["id"],
        }
        for kind, pair in steps.items():
            if pair is None:
                response[kind] = _unavailable(
                    BOUNDARY
                    if boundary and kind == "undo"
                    else f"Nothing to {kind} in this passage."
                )
                continue
            try:
                _proved_replacement(
                    db,
                    result_id,
                    pair,
                    ids,
                    index,
                    payload["segments"][index],
                    kind,
                    Canonical(canonical),
                )
            except ValueError as error:
                response[kind] = _unavailable(str(error)[:300])
                continue
            version = pair[1]
            response[kind] = {
                "available": True,
                "version_id": version["id"],
                "action": version["action"],
                "sequence": version["sequence"],
                "observed_at": version["observed_at"],
                "passage_number": index + 1,
                "passages_changed": 1,
                "confirm": False,
            }
        return response


def apply(
    store,
    recording_id,
    result_id,
    revision,
    head_version_id,
    version_id,
    reason=None,
    *,
    segment_id,
    kind,
    working_choice=None,
):
    _validate_request(revision, head_version_id, version_id, reason)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        canonical = Canonical()
        current, head, payload = _read_current(
            store, db, recording_id, result_id, revision, canonical=canonical
        )
        if head["id"] != head_version_id:
            raise ValueError(STALE)
        ids, index = _position(payload, segment_id)
        steps, _ = _history(db, result_id, head, segment_id)
        pair = steps[kind]
        if pair is None or pair[1]["id"] != version_id:
            raise ValueError(STALE)
        segments = payload["segments"]
        replacement = _proved_replacement(
            db, result_id, pair, ids, index, segments[index], kind, canonical
        )
        append_edit(
            store,
            db,
            current,
            result_id,
            head,
            segments[:index] + [replacement] + segments[index + 1 :],
            payload["speakers"],
            action=UNDO if kind == "undo" else REDO,
            context={"segment_id": segment_id, "passage_change_version_id": version_id},
            reason=reason,
            working_choice=working_choice,
        )
    return store.saved_recording(recording_id)

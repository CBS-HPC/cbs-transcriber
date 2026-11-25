"""Undo last change and Redo by exact predecessor restoration (ADR 0180).

The step to undo is the newest version V whose content differs from its predecessor P. Saving
P's exact content again is V's exact inverse: V is the newest content change, so no later work
can be lost. Versions with unchanged content (no-change saves, Trash and Restore markers,
no-change conflict observations) are skipped. A previous Undo points back past the version it
undid, so repeated Undo walks steadily back through the result's history; a Redo re-saves the
undone version's exact content and stays available only until the next other change.

Both append ordinary successor versions through the shared revision-fenced writer; nothing in
history is removed. Review marks, Working choices and notes are separate and never undone here.
"""

import json

from .history import validate_reason, validate_revision
from .passage_undo import (
    MAX_HISTORY_VERSIONS,
    MAX_SNAPSHOT_BYTES,
    Canonical,
    _delete_proof,
    _ids,
    _merge_inverse,
    _read_current,
    _read_payload,
    _restore_index,
    _same,
    _split_inverse,
)
from .result_actions import append_edit

UNDO, REDO = "change_undo", "change_redo"
START_ACTIONS = {"legacy_observed"}
STRUCTURAL = {"passage_merge", "passage_split", "passage_delete"}
CONFIRM_ACTIONS = {"version_restore"}
# A merge or split changes two neighbouring passages as one step; three or more are "several".
CONFIRM_PASSAGES = 3
# Undo/Redo links are tiny; a V context is read only for its passage identity.
MAX_LINK_CONTEXT_BYTES = 64 * 1024
MAX_STEP_CONTEXT_BYTES = 1024 * 1024
MAX_LINK_DEPTH = 1000


class Unavailable(ValueError):
    """No step can be offered; the message is shown beside the disabled button."""


def _rows(db, result_id, head_sequence):
    """Bounded version metadata, newest first; links are parsed only for Undo/Redo rows."""
    rows = db.execute(
        "SELECT id,predecessor_id,sequence,action,payload_sha256,observed_at,"
        "CASE WHEN action IN (?,?) AND length(CAST(context AS BLOB))<=? THEN context END AS link,"
        "length(CAST(payload AS BLOB)) AS size FROM result_versions "
        "WHERE result_id=? AND sequence>? AND sequence<=? ORDER BY sequence DESC",
        (
            UNDO,
            REDO,
            MAX_LINK_CONTEXT_BYTES,
            result_id,
            max(0, head_sequence - MAX_HISTORY_VERSIONS),
            head_sequence,
        ),
    ).fetchall()
    return {row["id"]: row for row in rows}, head_sequence > MAX_HISTORY_VERSIONS


def _link(row, *keys):
    try:
        value = json.loads(row["link"]) if row["link"] is not None else None
    except (TypeError, ValueError, RecursionError):
        value = None
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 100 for key in keys
    ):
        raise Unavailable("This result's undo history is incomplete. Use History instead.")
    return [value[key] for key in keys]


def _find(rows, identifier, limited):
    row = rows.get(identifier)
    if row is None:
        raise Unavailable(
            "Older changes are beyond the undo limit. Use History for older changes."
            if limited
            else "This result's undo history is incomplete. Use History instead."
        )
    return row


def _predecessor(rows, version, limited):
    previous = _find(rows, version["predecessor_id"], limited)
    if previous["sequence"] != version["sequence"] - 1:
        raise Unavailable("This result's undo history is incomplete. Use History instead.")
    return previous


def _start(version):
    if version["action"] in START_ACTIONS:
        return "Earlier history of this result is unknown; nothing earlier to undo."
    if version["predecessor_id"] is None or version["sequence"] == 1:
        return "Nothing earlier to undo in this result."
    return None


def undo_step(rows, head, limited):
    """(V, P): the newest content change and its exact predecessor, or Unavailable."""
    version, steps = head, 0
    while True:
        steps += 1
        if steps > MAX_LINK_DEPTH:
            raise Unavailable("Older changes are beyond the undo limit. Use History.")
        message = _start(version)
        if message:
            raise Unavailable(message)
        if version["action"] == UNDO:
            undone_id, restored_id = _link(version, "undone_version_id", "restored_from_version_id")
            undone, restored = _find(rows, undone_id, limited), _find(rows, restored_id, limited)
            if (
                undone["predecessor_id"] != restored["id"]
                or undone["sequence"] >= version["sequence"]
                or restored["payload_sha256"] != version["payload_sha256"]
            ):
                raise Unavailable("This result's undo history is incomplete. Use History instead.")
            version = restored
            continue
        previous = _predecessor(rows, version, limited)
        if previous["payload_sha256"] == version["payload_sha256"]:
            version = previous
            continue
        return version, previous


def redo_step(rows, head, limited):
    """(X, U): the version the newest Undo removed, or None after any other change."""
    version, steps = head, 0
    while True:
        steps += 1
        if steps > MAX_LINK_DEPTH:
            return None
        if version["action"] == UNDO:
            undone_id, restored_id = _link(version, "undone_version_id", "restored_from_version_id")
            undone = _find(rows, undone_id, limited)
            if (
                undone["predecessor_id"] != restored_id
                or undone["sequence"] >= version["sequence"]
                or undone["payload_sha256"] == version["payload_sha256"]
            ):
                raise Unavailable("This result's undo history is incomplete. Use History instead.")
            return undone, version
        if version["action"] == REDO:
            redone_id, undo_id = _link(version, "redone_version_id", "undo_version_id")
            undo = _find(rows, undo_id, limited)
            if undo["action"] != UNDO or undo["sequence"] >= version["sequence"]:
                raise Unavailable("This result's undo history is incomplete. Use History instead.")
            if _link(undo, "undone_version_id", "restored_from_version_id")[0] != redone_id:
                raise Unavailable("This result's undo history is incomplete. Use History instead.")
            before = _predecessor(rows, undo, limited)
            if before["payload_sha256"] != version["payload_sha256"]:
                raise Unavailable("This result's undo history is incomplete. Use History instead.")
            version = before
            continue
        if _start(version):
            return None
        previous = _predecessor(rows, version, limited)
        if previous["payload_sha256"] != version["payload_sha256"]:
            return None
        version = previous


def effective_action(rows, version):
    """A Redo is labelled by what it re-saved; bounded and tolerant of missing links."""
    for _ in range(MAX_LINK_DEPTH):
        if version["action"] != REDO:
            return version["action"]
        try:
            version = rows[_link(version, "redone_version_id", "undo_version_id")[0]]
        except (KeyError, Unavailable):
            return REDO
    return REDO


def _full(db, recording_id, result_id, identifier):
    row = db.execute(
        "SELECT v.* FROM result_versions v JOIN transcript_results r ON r.id=v.result_id "
        "WHERE v.id=? AND v.result_id=? AND r.recording_id=?",
        (identifier, result_id, recording_id),
    ).fetchone()
    if row is None:
        raise Unavailable("This result's undo history is incomplete. Use History instead.")
    if len(row["payload"].encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise Unavailable("This saved version is too large to undo here. Use History instead.")
    return row


def _context(db, identifier):
    """A step's bounded context (passage identity, structural proof inputs), never its payload."""
    row = db.execute(
        "SELECT CASE WHEN length(CAST(context AS BLOB))<=? THEN context END AS context "
        "FROM result_versions WHERE id=?",
        (MAX_STEP_CONTEXT_BYTES, identifier),
    ).fetchone()
    try:
        value = json.loads(row["context"]) if row and row["context"] is not None else None
    except (TypeError, ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def _structure_agrees(action, context, before, after, canonical=None):
    """Merge/split/delete undo must agree with the existing proven inverses (ADR 0135/0147/0157)."""
    if action not in STRUCTURAL:
        return True
    segments = after["segments"]
    try:
        ids = _ids(segments)
        if action == "passage_merge":
            inverse = _merge_inverse(before, after, context, segments, ids, canonical)
            if inverse is None:
                return False
            retained, parts = inverse
            index = ids.index(retained)
            rebuilt = segments[:index] + parts + segments[index + 1 :]
        elif action == "passage_split":
            inverse = _split_inverse(before, after, context, segments, ids, canonical)
            if inverse is None:
                return False
            selected, parts, _ = inverse
            index = ids.index(selected)
            rebuilt = segments[:index] + parts + segments[index + 2 :]
        else:
            proof = _delete_proof(before, after, context, canonical)
            if proof is None:
                return False
            _, passage, left, right = proof
            index = _restore_index(ids, left, right)
            if index is None:
                return False
            rebuilt = segments[:index] + [passage] + segments[index:]
    except (KeyError, TypeError, ValueError, IndexError, RecursionError):
        return False
    same = _same if canonical is None else canonical.same
    return same(rebuilt, before["segments"]) and _same(before["speakers"], after["speakers"])


def _changes(before, after, canonical=None):
    """Passage-level counts and the first affected passage's position, without text."""
    same = _same if canonical is None else canonical.same
    old = {p.get("id"): p for p in before["segments"] if isinstance(p, dict)}
    new = {p.get("id"): p for p in after["segments"] if isinstance(p, dict)}
    changed = [
        index
        for index, passage in enumerate(after["segments"])
        if not isinstance(passage, dict)
        or passage.get("id") not in old
        or not same(old[passage.get("id")], passage)
    ]
    removed = {identifier for identifier in old if identifier not in new}
    first = changed[0] if changed else None
    if first is None and removed:
        position = next(
            i
            for i, p in enumerate(before["segments"])
            if isinstance(p, dict) and p.get("id") in removed
        )
        first = min(position, len(after["segments"]))
    return {
        "passages_changed": len(changed) + len(removed),
        "speakers_changed": not _same(before["speakers"], after["speakers"]),
        "passage_number": None if first is None else first + 1,
    }


def _describe(rows, version, before, after, *, segment_id=None, canonical=None):
    action = effective_action(rows, version)
    counts = _changes(before, after, canonical)
    return {
        "available": True,
        "version_id": version["id"],
        "action": action,
        "sequence": version["sequence"],
        "observed_at": version["observed_at"],
        **counts,
        "confirm": action in CONFIRM_ACTIONS or counts["passages_changed"] >= CONFIRM_PASSAGES,
        **({"segment_id": segment_id} if isinstance(segment_id, str) else {}),
    }


def _unavailable(message):
    return {"available": False, "message": message}


def options(store, db, recording_id, result_id, head, payload, canonical=None):
    """Undo/Redo blocks for the merge-undo-options response, inside the caller's read.

    With the caller's registry of the head payload's exact passage texts (ADR 0193), each
    comparison reads verified slices instead of encoding both transcripts passage by passage.
    """
    rows, limited = _rows(db, result_id, head["sequence"])
    head_row = rows[head["id"]]
    try:
        version, previous = undo_step(rows, head_row, limited)
        row = _full(db, recording_id, result_id, previous["id"])
        step = Canonical(canonical)
        before = step.read(row["payload"], row["payload_sha256"])
        segment = _context(db, version["id"]).get("segment_id")
        undo = _describe(rows, version, payload, before, segment_id=segment, canonical=step)
    except ValueError as error:
        # Includes integrity refusals of the earlier version; passage undos stay available.
        undo = _unavailable(str(error)[:300])
    try:
        found = redo_step(rows, head_row, limited)
        if found is None:
            redo = _unavailable("Nothing to redo.")
        else:
            target, _ = found
            row = _full(db, recording_id, result_id, target["id"])
            step = Canonical(canonical)
            after = step.read(row["payload"], row["payload_sha256"])
            segment = _context(db, target["id"]).get("segment_id")
            redo = _describe(rows, target, payload, after, segment_id=segment, canonical=step)
    except ValueError as error:
        redo = _unavailable(str(error)[:300])
    return {"head_version_id": head["id"], "undo": undo, "redo": redo}


def _validate_request(revision, head_version_id, version_id, reason):
    validate_revision(revision)
    validate_reason(reason)
    for value in (head_version_id, version_id):
        if not isinstance(value, str) or not 1 <= len(value) <= 100:
            raise ValueError("Choose the exact change shown before undoing or redoing it.")


STALE = "The newest change is different now. Review the Undo button again."


def undo(
    store,
    recording_id,
    result_id,
    revision,
    head_version_id,
    version_id,
    reason=None,
    *,
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
        rows, limited = _rows(db, result_id, head["sequence"])
        version, previous = undo_step(rows, rows[head["id"]], limited)
        if version["id"] != version_id:
            raise ValueError(STALE)
        if version["payload_sha256"] != head["payload_sha256"]:
            raise ValueError("The saved versions disagree. Preserve the library.")
        before_row = _full(db, recording_id, result_id, previous["id"])
        before = canonical.read(before_row["payload"], before_row["payload_sha256"])
        action = effective_action(rows, version)
        context = _context(db, version["id"])
        if version["action"] in STRUCTURAL and not _structure_agrees(
            version["action"], context, before, payload, canonical
        ):
            raise ValueError(
                f"This {action.removeprefix('passage_')} cannot be undone safely because its "
                "saved history does not match. Review the saved versions in History."
            )
        append_edit(
            store,
            db,
            current,
            result_id,
            head,
            before["segments"],
            before["speakers"],
            action=UNDO,
            context={
                "undone_version_id": version["id"],
                "restored_from_version_id": previous["id"],
            },
            reason=reason,
            working_choice=working_choice,
        )
    return _saved(store, recording_id)


def redo(
    store,
    recording_id,
    result_id,
    revision,
    head_version_id,
    version_id,
    reason=None,
    *,
    working_choice=None,
):
    _validate_request(revision, head_version_id, version_id, reason)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current, head, _ = _read_current(store, db, recording_id, result_id, revision)
        if head["id"] != head_version_id:
            raise ValueError(STALE)
        rows, limited = _rows(db, result_id, head["sequence"])
        found = redo_step(rows, rows[head["id"]], limited)
        if found is None or found[0]["id"] != version_id:
            raise ValueError("Nothing to redo now. Review the Redo button again.")
        target, undo_row = found
        if target["payload_sha256"] == head["payload_sha256"]:
            raise ValueError("The saved versions disagree. Preserve the library.")
        row = _full(db, recording_id, result_id, target["id"])
        after = _read_payload(row["payload"], row["payload_sha256"])
        append_edit(
            store,
            db,
            current,
            result_id,
            head,
            after["segments"],
            after["speakers"],
            action=REDO,
            context={"redone_version_id": target["id"], "undo_version_id": undo_row["id"]},
            reason=reason,
            working_choice=working_choice,
        )
    return _saved(store, recording_id)


def _saved(store, recording_id):
    return store.saved_recording(recording_id)

"""Undo proven passage structure changes without reverting unrelated edits."""

import json

from .history import encode_payload, validate_revision, verify_payload
from .result_actions import append_edit
from .transcripts import (
    MANUAL_SPLIT_POLICY,
    delete_passage,
    merge_passages,
    split_passage,
    split_passage_manual,
)

# A lookup never reads unbounded history into the editor or holds a writer indefinitely.
# Shared merge/split/delete scan cap; keep the existing name for compatibility.
MAX_MERGES = 100
MAX_HISTORY_VERSIONS = 500
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_HISTORY_BYTES = 32 * 1024 * 1024


def _read_payload(payload, digest):
    # Reuse only the bounded canonical-validation fact. Each read still hashes the
    # current bytes and decodes fresh objects; ownership and eligibility are uncached.
    verify_payload(payload, digest)
    return json.loads(payload)


def _read_current(store, db, recording_id, result_id, revision=None):
    current = db.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
    if current is None:
        raise KeyError(recording_id)
    if current["active_transcript_id"] != result_id or (
        revision is not None and current["revision"] != revision
    ):
        raise ValueError("The saved result changed. Reload the transcript and try again.")
    result = db.execute(
        "SELECT * FROM transcript_results WHERE id=? AND recording_id=? "
        "AND id NOT IN (SELECT result_id FROM result_trash)",
        (result_id, recording_id),
    ).fetchone()
    if result is None:
        raise ValueError("The saved result is unavailable. Reload the transcript.")
    try:
        head = store._head_version(db, result)
    except RuntimeError as error:
        raise ValueError(str(error)) from error
    payload = _read_payload(head["payload"], head["payload_sha256"])
    mirrors_match = (
        current["segments"] == result["segments"] and current["speakers"] == result["speakers"]
    )
    if not mirrors_match and (
        encode_payload(json.loads(current["segments"]), json.loads(current["speakers"]))[0]
        != head["payload"]
    ):
        raise ValueError("The recording and saved result disagree. Preserve the library.")
    return current, head, payload


def _same(left, right):
    return json.dumps(
        left, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ) == json.dumps(right, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _ids(segments):
    if not isinstance(segments, list) or any(
        not isinstance(p, dict) or not isinstance(p.get("id"), str) or not p["id"] for p in segments
    ):
        raise ValueError("Invalid passage identities.")
    ids = [p["id"] for p in segments]
    if len(ids) != len(set(ids)):
        raise ValueError("Passage identities are not unique.")
    return ids


def _merge_inverse(before, after, context, current, ids):
    selected, adjacent, direction = (
        context["segment_id"],
        context["adjacent_segment_id"],
        context["direction"],
    )
    if not all(isinstance(v, str) for v in (selected, adjacent, direction)):
        return None
    if direction not in {"up", "down"} or selected == adjacent:
        return None
    retained, removed = (adjacent, selected) if direction == "up" else (selected, adjacent)
    if retained not in ids or removed in ids:
        return None
    before_segments, after_segments = before["segments"], after["segments"]
    before_ids, after_ids = _ids(before_segments), _ids(after_segments)
    index = before_ids.index(selected)
    other = index + (-1 if direction == "up" else 1)
    if not 0 <= other < len(before_segments) or before_ids[other] != adjacent:
        return None
    merged = after_segments[after_ids.index(retained)]
    expected = merge_passages(
        before_segments, selected, direction, merged["speaker"], before["speakers"]
    )
    if not _same(expected, after_segments) or not _same(before["speakers"], after["speakers"]):
        return None
    if not _same(current[ids.index(retained)], merged):
        return None
    first = min(index, other)
    return retained, before_segments[first : first + 2]


def _split_inverse(before, after, context, current, ids):
    selected = context["segment_id"]
    if not isinstance(selected, str) or selected not in ids:
        return None
    before_segments, after_segments = before["segments"], after["segments"]
    before_ids, after_ids = _ids(before_segments), _ids(after_segments)
    index = before_ids.index(selected)
    if len(after_ids) != len(before_ids) + 1 or after_ids[index] != selected:
        return None
    right_id = after_ids[index + 1]
    # Prove one new, unique right-half ID at this exact position before replacing
    # only the random UUID produced by deterministic split recomputation.
    if right_id in before_ids or after_ids[: index + 1] + after_ids[index + 2 :] != before_ids:
        return None
    policy = context.get("split_policy")
    if policy not in (None, MANUAL_SPLIT_POLICY):
        return None
    split = split_passage_manual if policy == MANUAL_SPLIT_POLICY else split_passage
    expected = split(before_segments, selected, context["cursor"], context.get("split_time"))
    expected[index + 1]["id"] = right_id
    if not _same(expected, after_segments) or not _same(before["speakers"], after["speakers"]):
        return None
    current_index = ids.index(selected)
    if (
        current_index + 1 >= len(ids)
        or ids[current_index + 1] != right_id
        or not _same(current[current_index : current_index + 2], after_segments[index : index + 2])
    ):
        return None
    return selected, [before_segments[index]], right_id


def _delete_proof(before, after, context):
    selected = context["segment_id"]
    if not isinstance(selected, str) or not selected:
        return None
    before_segments, after_segments = before["segments"], after["segments"]
    before_ids, after_ids = _ids(before_segments), _ids(after_segments)
    if selected not in before_ids or selected in after_ids:
        return None
    index = before_ids.index(selected)
    passage = before_segments[index]
    if not isinstance(passage.get("text"), str) or not isinstance(passage.get("speaker"), str):
        return None
    if not _same(delete_passage(before_segments, selected), after_segments) or not _same(
        before["speakers"], after["speakers"]
    ):
        return None
    return (
        selected,
        passage,
        before_ids[index - 1] if index else None,
        before_ids[index + 1] if index + 1 < len(before_ids) else None,
    )


def _restore_index(ids, left, right):
    if left is None and right is None:
        return 0 if not ids else None
    if left is None:
        return 0 if ids and ids[0] == right else None
    if right is None:
        return len(ids) if ids and ids[-1] == left else None
    if left not in ids or right not in ids:
        return None
    index = ids.index(left) + 1
    return index if index < len(ids) and ids[index] == right else None


def _candidates(db, recording_id, result_id, current, head_sequence, *, target=None):
    """Verify structural inverses in one newest-first scan with shared history budgets."""
    rows = db.execute(
        "SELECT m.id,m.predecessor_id,m.sequence,m.action,"
        "CASE WHEN length(CAST(m.context AS BLOB))<=1048576 THEN m.context END AS context,"
        "length(CAST(m.payload AS BLOB)) AS merged_size,"
        "length(CAST(p.payload AS BLOB)) AS previous_size "
        "FROM result_versions m LEFT JOIN result_versions p "
        "ON p.id=m.predecessor_id AND p.result_id=m.result_id AND p.sequence=m.sequence-1 "
        "WHERE m.result_id=? AND m.sequence>? AND m.sequence<=? "
        "AND m.action IN ('passage_merge','passage_split','passage_delete',"
        "'passage_merge_undo','passage_split_undo','passage_delete_undo','version_restore') "
        "ORDER BY m.sequence DESC LIMIT ?",
        (result_id, max(0, head_sequence - MAX_HISTORY_VERSIONS), head_sequence, MAX_MERGES + 1),
    ).fetchall()
    limited = head_sequence > MAX_HISTORY_VERSIONS or len(rows) > MAX_MERGES
    remaining = MAX_HISTORY_BYTES
    segments = current["segments"]
    merges, splits, deletions = {}, {}, {}
    seen_deletions, deletion_history_unknown = set(), False
    present_later = set()
    try:
        ids = _ids(segments)
    except ValueError:
        return merges, splits, deletions, limited
    for row in rows[:MAX_MERGES]:
        if row["action"] == "version_restore":
            # A whole-version replacement is not an inactive passage view. Its older
            # deletions need the saved-version workflow, not guessed selective recovery.
            deletion_history_unknown = True
            continue
        if row["context"] is None:
            limited = True
            deletion_history_unknown = True
            continue
        try:
            context = json.loads(row["context"])
            if not isinstance(context, dict):
                deletion_history_unknown, limited = True, True
                continue
            selected = context.get("segment_id")
            latest_delete = True
            if row["action"] == "passage_delete":
                if deletion_history_unknown:
                    continue
                if not isinstance(selected, str) or not selected:
                    deletion_history_unknown, limited = True, True
                    continue
                # Never fall back to an older deletion of this identity if its latest proof fails.
                latest_delete = selected not in seen_deletions
                seen_deletions.add(selected)
            sizes = (row["merged_size"], row["previous_size"])
            if any(size is None or size > MAX_SNAPSHOT_BYTES for size in sizes):
                limited = True
                deletion_history_unknown = True
                continue
            if sum(sizes) > remaining:
                limited = True
                break
            remaining -= sum(sizes)
            versions = db.execute(
                "SELECT v.* FROM result_versions v JOIN transcript_results r ON r.id=v.result_id "
                "WHERE v.id IN (?,?) AND v.result_id=? AND r.recording_id=?",
                (row["id"], row["predecessor_id"], result_id, recording_id),
            ).fetchall()
            by_id = {v["id"]: v for v in versions}
            after_row, before_row = by_id[row["id"]], by_id[row["predecessor_id"]]
            if (
                after_row["sequence"] != row["sequence"]
                or before_row["sequence"] != after_row["sequence"] - 1
                or after_row["predecessor_id"] != before_row["id"]
                or after_row["action"] != row["action"]
            ):
                deletion_history_unknown, limited = True, True
                continue
            before = _read_payload(before_row["payload"], before_row["payload_sha256"])
            after = _read_payload(after_row["payload"], after_row["payload_sha256"])
            before_ids, after_ids = _ids(before["segments"]), _ids(after["segments"])
            if row["action"] == "passage_merge":
                inverse = _merge_inverse(before, after, context, segments, ids)
                if inverse is not None:
                    retained, parts = inverse
                    merges.setdefault(retained, (parts, before_row["id"]))
            elif row["action"] == "passage_split":
                inverse = _split_inverse(before, after, context, segments, ids)
                if inverse is not None:
                    retained, parts, right_id = inverse
                    splits.setdefault(retained, (parts, before_row["id"], right_id))
            elif row["action"] == "passage_delete":
                proof = _delete_proof(before, after, context)
                if proof is None:
                    deletion_history_unknown, limited = True, True
                    continue
                selected, passage, left, right = proof
                index = _restore_index(ids, left, right)
                if (
                    latest_delete
                    and selected not in ids
                    and selected not in present_later
                    and index is not None
                ):
                    deletions[selected] = {
                        "passage": passage,
                        "insertion_index": index,
                        "before_version_id": before_row["id"],
                        "delete_version_id": after_row["id"],
                    }
            # Observe lifecycle only AFTER checking this deletion. A restored passage
            # consumed by a later merge/undo-split is not still inactive now.
            present_later.update(before_ids)
            present_later.update(after_ids)
        except (KeyError, TypeError, ValueError, StopIteration, IndexError, RecursionError):
            # Invalid or incomplete provenance cannot authorize destructive restoration.
            deletion_history_unknown, limited = True, True
            continue
        # Merge/split matches are newest-first and never invalidated by older rows.
        # Apply needs only its verified target; deletion recovery retains the full scan.
        if target is not None and target[1] in (splits if target[0] == "split" else merges):
            return merges, splits, deletions, limited
    return merges, splits, deletions, limited


def options(store, recording_id, result_id):
    with store.connection() as db:
        db.execute("BEGIN")
        current, head, payload = _read_current(store, db, recording_id, result_id)
        matches, splits, deletions, limited = _candidates(
            db, recording_id, result_id, payload, head["sequence"]
        )
        return {
            "result_id": result_id,
            "revision": current["revision"],
            "segment_ids": list(matches),
            "split_segment_ids": list(splits),
            "deleted_segments": [
                {
                    "segment_id": identifier,
                    "delete_version_id": proof["delete_version_id"],
                    "insertion_index": proof["insertion_index"],
                    "text_preview": proof["passage"]["text"][:240],
                    "text_truncated": len(proof["passage"]["text"]) > 240,
                }
                for identifier, proof in deletions.items()
            ],
            "history_limited": limited,
        }


def apply(
    store, recording_id, result_id, segment_id, revision, reason=None, *, working_choice=None
):
    return _apply(
        store,
        recording_id,
        result_id,
        segment_id,
        revision,
        reason,
        working_choice=working_choice,
        split=False,
    )


def apply_split(
    store, recording_id, result_id, segment_id, revision, reason=None, *, working_choice=None
):
    return _apply(
        store,
        recording_id,
        result_id,
        segment_id,
        revision,
        reason,
        working_choice=working_choice,
        split=True,
    )


def apply_restore(
    store,
    recording_id,
    result_id,
    segment_id,
    delete_version_id,
    revision,
    reason=None,
    *,
    working_choice=None,
):
    validate_revision(revision)
    if not isinstance(delete_version_id, str) or not 1 <= len(delete_version_id) <= 100:
        raise ValueError("Choose the exact deleted passage before restoring it.")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current, head, payload = _read_current(store, db, recording_id, result_id, revision)
        _, _, deletions, _ = _candidates(db, recording_id, result_id, payload, head["sequence"])
        proof = deletions.get(segment_id)
        if proof is None or proof["delete_version_id"] != delete_version_id:
            raise ValueError(
                "This deleted passage cannot be restored safely because its history or neighboring "
                "passages changed. Restore missing neighbors first, or review the saved versions."
            )
        index = proof["insertion_index"]
        segments = payload["segments"]
        segments = segments[:index] + [proof["passage"]] + segments[index:]
        append_edit(
            store,
            db,
            current,
            result_id,
            head,
            segments,
            payload["speakers"],
            action="passage_delete_undo",
            context={
                "segment_id": segment_id,
                "restored_from_version_id": proof["before_version_id"],
                "deleted_version_id": proof["delete_version_id"],
            },
            reason=reason,
            working_choice=working_choice,
        )
    recording = store.recording(recording_id)
    store.snapshot_project(recording["project_id"])
    recording["snapshot_error"] = store.snapshot_errors.get(recording["project_id"])
    return recording


def _apply(store, recording_id, result_id, segment_id, revision, reason, *, working_choice, split):
    validate_revision(revision)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current, head, payload = _read_current(store, db, recording_id, result_id, revision)
        merges, splits, _, _ = _candidates(
            db,
            recording_id,
            result_id,
            payload,
            head["sequence"],
            target=("split" if split else "merge", segment_id),
        )
        matches = splits if split else merges
        if segment_id not in matches:
            kind = "split" if split else "merge"
            raise ValueError(
                f"This {kind} cannot be undone safely because the passage or its history changed. "
                "Reload and review the saved versions."
            )
        parts, version_id, *removed = matches[segment_id]
        segments = payload["segments"]
        index = next(i for i, p in enumerate(segments) if p["id"] == segment_id)
        segments = segments[:index] + parts + segments[index + (2 if split else 1) :]
        append_edit(
            store,
            db,
            current,
            result_id,
            head,
            segments,
            payload["speakers"],
            action="passage_split_undo" if split else "passage_merge_undo",
            context={
                "segment_id": segment_id,
                "restored_from_version_id": version_id,
                **({"adjacent_segment_id": removed[0]} if split else {}),
            },
            reason=reason,
            working_choice=working_choice,
        )
    recording = store.recording(recording_id)
    store.snapshot_project(recording["project_id"])
    recording["snapshot_error"] = store.snapshot_errors.get(recording["project_id"])
    return recording

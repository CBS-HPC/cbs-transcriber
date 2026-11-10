"""Shared snapshot reads and atomic successor writes for explicit result review actions."""

import json

from .history import decode_payload, encode_payload, validate_reason, validate_revision
from .store import now
from .transcripts import MAX_TRANSCRIPT_BYTES

INHERITED_CONTEXT = {
    "processing",
    "processing_observation",
    "source_result_id",
    "source_payload_sha256",
    "source_version_unknown",
    "producer_job_id",
    "processing_kind",
    "segment_ids",
    "artifact_evidence",
    "producer_artifacts",
    "timing_artifacts",
    "runtime_artifacts",
    "provider_artifacts",
    "native_artifacts",
    "audio_input",
    "audio_preparations",
    "history_coverage",
}


def read_pair(store, db, recording_id, target_id, source_id, revision):
    """Read checked live heads and their active mirror inside the caller's transaction."""
    validate_revision(revision)
    current = db.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
    if current is None:
        raise KeyError(recording_id)
    if current["revision"] != revision or current["active_transcript_id"] != target_id:
        raise ValueError("The saved inputs changed in another window. Preview this action again.")
    if source_id == target_id:
        raise ValueError("Choose a different saved result for this action.")
    pair = {"current": current}
    total = 0
    for name, identifier in (("target", target_id), ("source", source_id)):
        result = db.execute("SELECT * FROM transcript_results WHERE id=?", (identifier,)).fetchone()
        if result is None:
            raise KeyError(identifier)
        if result["recording_id"] != recording_id:
            raise ValueError("Both saved results must belong to the same recording.")
        if db.execute("SELECT 1 FROM result_trash WHERE result_id=?", (identifier,)).fetchone():
            raise ValueError(
                "The saved inputs changed in another window. Preview this action again."
            )
        if any(
            len(result[field].encode("utf-8")) > MAX_TRANSCRIPT_BYTES
            for field in ("segments", "speakers")
        ):
            raise ValueError("The saved input exceeds the supported snapshot size.")
        try:
            head = store._head_version(db, result)
            total += len(head["payload"].encode("utf-8"))
            if total > MAX_TRANSCRIPT_BYTES:
                raise ValueError("The two saved snapshots exceed the supported size.")
            payload = decode_payload(head["payload"], head["payload_sha256"])
        except (RuntimeError, TypeError, ValueError, RecursionError):
            raise ValueError(
                "The result and its saved version failed their integrity check."
            ) from None
        pair[name], pair[name + "_head"], pair[name + "_payload"] = result, head, payload
    try:
        if any(
            len(current[field].encode("utf-8")) > MAX_TRANSCRIPT_BYTES
            for field in ("segments", "speakers")
        ):
            raise ValueError()
        mirror = encode_payload(json.loads(current["segments"]), json.loads(current["speakers"]))[0]
        if mirror != pair["target_head"]["payload"]:
            raise ValueError()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("The recording and active result failed their integrity check.") from None
    return pair


def append_edit(
    store,
    db,
    current,
    target_id,
    head,
    segments,
    speakers,
    *,
    action,
    context,
    reason,
    timing_artifacts=None,
    working_choice=None,
):
    """Update both mirrors and append once; the caller owns BEGIN IMMEDIATE and commit."""
    validate_reason(reason)
    from . import working_manual

    working = working_manual.prepare(
        db, store, current["id"], head, working_choice, automatic=action != "timing_realign"
    )
    try:
        if len(head["context"].encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
            raise ValueError()
        previous = json.loads(head["context"])
        if not isinstance(previous, dict):
            raise ValueError()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("The saved review context is invalid. Preserve the library.") from None
    context = {
        **{key: value for key, value in previous.items() if key in INHERITED_CONTEXT},
        **context,
    }
    text = json.dumps(segments, ensure_ascii=False, allow_nan=False)
    names = json.dumps(speakers, ensure_ascii=False, allow_nan=False)
    changed = db.execute(
        "UPDATE recordings SET segments=?,speakers=?,revision=revision+1 "
        "WHERE id=? AND revision=? AND active_transcript_id=?",
        (text, names, current["id"], current["revision"], target_id),
    ).rowcount
    if changed != 1:
        raise ValueError("The saved inputs changed in another window. Preview this action again.")
    db.execute(
        "UPDATE transcript_results SET segments=?,speakers=?,updated=? "
        "WHERE id=? AND recording_id=?",
        (text, names, now(), target_id, current["id"]),
    )
    version = store._append_version(
        db,
        db.execute("SELECT * FROM transcript_results WHERE id=?", (target_id,)).fetchone(),
        action,
        context,
        "local_user",
        reason,
        timing_artifacts=timing_artifacts,
    )
    working_manual.apply(db, store, current["id"], version, working)
    return version

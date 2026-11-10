"""Snapshot-bound enhancement merge previews and atomic reviewed-result successors."""

import hashlib
import hmac
import json

from .history import (
    decode_payload,
    encode_payload,
    merge_metadata,
    validate_reason,
)
from .result_actions import append_edit, read_pair
from .transcripts import MAX_TRANSCRIPT_BYTES

FORMAT = "cbs-enhancement-merge-v1"
SUMMARY_FIELDS = (
    "updated_passages",
    "timing_updates",
    "speaker_updates",
    "conflicts",
    "skipped",
    "unchanged",
)


def _changed():
    raise ValueError("The merge inputs changed in another window. Preview the merge again.")


def _bounded(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("The merge input exceeds the supported snapshot size.")
    return text


def _json(text):
    try:
        return json.loads(_bounded(text))
    except (TypeError, ValueError, RecursionError):
        raise ValueError("The saved merge input is invalid. Preserve the library.") from None


def _payload(version):
    _bounded(version["payload"])
    return decode_payload(version["payload"], version["payload_sha256"])


def _reference(version):
    return {
        "result_id": version["result_id"],
        "version_id": version["id"],
        "payload_sha256": version["payload_sha256"],
    }


def _plan(store, db, recording_id, result_id, incoming_id, revision):
    """Everything used by a preview or commit comes from this caller-owned transaction."""
    from .transcript_merge import POLICY_VERSION, merge_payload

    pair = read_pair(store, db, recording_id, result_id, incoming_id, revision)
    current, incoming = pair["current"], pair["source"]
    source = _json(incoming["source"])
    if not isinstance(source, dict) or source.get("kind") != "enhancement":
        raise ValueError("Only a saved enhancement from this result can be merged.")
    ours_head, ours_payload = pair["target_head"], pair["target_payload"]
    incoming_head, incoming_payload = pair["source_head"], pair["source_payload"]
    creation = db.execute(
        "SELECT * FROM result_versions WHERE result_id=? AND sequence=1", (incoming_id,)
    ).fetchone()
    if creation is None or not creation["source_version_id"]:
        raise ValueError("This enhancement has no exact saved base version to merge.")
    creation_payload = _payload(creation)
    base = db.execute(
        "SELECT v.* FROM result_versions v JOIN transcript_results r ON r.id=v.result_id "
        "WHERE v.id=? AND v.result_id=? AND r.recording_id=?",
        (creation["source_version_id"], result_id, recording_id),
    ).fetchone()
    if base is None or incoming_head["source_version_id"] != base["id"]:
        raise ValueError("The enhancement's exact base must belong to the reviewed result.")
    base_payload = _payload(base)
    merged = merge_payload(base_payload, ours_payload, incoming_payload, creation_payload)
    payload, digest = encode_payload(merged["segments"], merged["speakers"])
    _bounded(payload)
    summary = {field: merged["summary"][field] for field in SUMMARY_FIELDS}
    references = {
        "base": _reference(base),
        "incoming": _reference(incoming_head),
        "creation": _reference(creation),
    }
    binding = {
        "format": FORMAT,
        "policy": POLICY_VERSION,
        "recording_id": recording_id,
        "revision": revision,
        "ours": _reference(ours_head),
        **references,
        "merged_payload_sha256": digest,
        "summary": summary,
    }
    fingerprint = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    passages = merged["passages"]
    response = {
        "fingerprint": fingerprint,
        "result_id": result_id,
        "incoming_result_id": incoming_id,
        "incoming_label": incoming["label"][:200],
        "revision": revision,
        "summary": summary,
        "passages": passages[:200],
        "truncated": len(
            {passage["id"] for passage in [*base_payload["segments"], *ours_payload["segments"]]}
        )
        > 200,
    }
    return {
        "response": response,
        "pair": pair,
        "snapshots": {
            "base": base_payload,
            "current": ours_payload,
            "incoming": incoming_payload,
            "creation": creation_payload,
        },
        "current": current,
        "ours_head": ours_head,
        "payload": payload,
        "merged": merged,
        "references": references,
    }


def preview(store, recording_id, result_id, incoming_id, revision):
    with store.connection() as db:
        db.execute("BEGIN")
        return _plan(store, db, recording_id, result_id, incoming_id, revision)["response"]


def apply(
    store,
    recording_id,
    result_id,
    incoming_id,
    revision,
    fingerprint,
    reason=None,
    *,
    working_choice=None,
):
    validate_reason(reason)
    with store.snapshot_lock:
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            plan = _plan(store, db, recording_id, result_id, incoming_id, revision)
            if not isinstance(fingerprint, str) or not hmac.compare_digest(
                fingerprint, plan["response"]["fingerprint"]
            ):
                _changed()
            if plan["payload"] == plan["ours_head"]["payload"]:
                raise ValueError("This enhancement has no safe changes to merge.")
            merged = plan["merged"]
            evidence = merge_metadata(
                {
                    "format": FORMAT,
                    **plan["references"],
                    "summary": plan["response"]["summary"],
                }
            )
            version = append_edit(
                store,
                db,
                plan["current"],
                result_id,
                plan["ours_head"],
                merged["segments"],
                merged["speakers"],
                action="result_merge",
                context={"merge": evidence},
                reason=reason,
                working_choice=working_choice,
            )
        store.snapshot_project(plan["current"]["project_id"])
        return {
            "recording": store.recording(recording_id),
            "summary": plan["response"]["summary"],
            "version_id": version["id"],
        }

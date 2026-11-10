"""Explicit selected-passage wording adoption from another saved result."""

import hashlib
import hmac
import json

from .history import encode_payload, text_hash, validate_reason, wording_adoption_metadata
from .result_actions import append_edit, read_pair
from .transcripts import MAX_TRANSCRIPT_BYTES, _interval, _passage_index, edit_segment

FORMAT = "cbs-wording-adoption-v1"


def _passage(payload, identifier):
    segments = payload["segments"]
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in segments):
        raise ValueError("The saved result has invalid passage identifiers.")
    index = _passage_index(segments, identifier)
    passage = segments[index]
    if not isinstance(passage.get("text"), str) or len(passage["text"]) > 200_000:
        raise ValueError("Choose a saved passage with bounded literal text.")
    start, end = _interval(passage)
    return (
        index,
        passage,
        {
            "id": identifier,
            "text": passage["text"],
            "start": start,
            "end": end,
            "ordinal": index + 1,
        },
    )


def _plan(
    store, db, recording_id, target_id, source_id, target_passage_id, source_passage_id, revision
):
    pair = read_pair(store, db, recording_id, target_id, source_id, revision)
    index, target, target_detail = _passage(pair["target_payload"], target_passage_id)
    _, source, source_detail = _passage(pair["source_payload"], source_passage_id)
    refusal = None
    if not source["text"].strip():
        refusal = "Choose a source passage with nonblank wording."
    elif target["text"] == source["text"]:
        refusal = "These passages already have exactly the same wording."
    segments = list(pair["target_payload"]["segments"])
    segments[index] = edit_segment(target, "free", source["text"])
    speakers = pair["target_payload"]["speakers"]
    encoded, digest = encode_payload(segments, speakers)
    if len(encoded.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("The adopted result exceeds the supported snapshot size.")
    head = pair["source_head"]
    evidence = wording_adoption_metadata(
        {
            "format": FORMAT,
            "source": {
                "result_id": source_id,
                "version_id": head["id"],
                "payload_sha256": head["payload_sha256"],
            },
            "target_passage_id": target_passage_id,
            "source_passage_id": source_passage_id,
            "target_text_sha256": text_hash(target["text"]),
            "source_text_sha256": text_hash(source["text"]),
        }
    )
    binding = {
        **evidence,
        "recording_id": recording_id,
        "revision": revision,
        "target_result_id": target_id,
        "target_version_id": pair["target_head"]["id"],
        "target_payload_sha256": pair["target_head"]["payload_sha256"],
        "proposed_payload_sha256": digest,
    }
    fingerprint = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    return {
        "pair": pair,
        "segments": segments,
        "speakers": speakers,
        "evidence": evidence,
        "refusal": refusal,
        "response": {
            "fingerprint": fingerprint,
            "result_id": target_id,
            "source_result_id": source_id,
            "source_label": pair["source"]["label"][:200],
            "revision": revision,
            "source_version_id": head["id"],
            "target_passage": target_detail,
            "source_passage": source_detail,
        },
    }


def preview(
    store, recording_id, target_id, source_id, target_passage_id, source_passage_id, revision
):
    with store.connection() as db:
        db.execute("BEGIN")
        plan = _plan(
            store,
            db,
            recording_id,
            target_id,
            source_id,
            target_passage_id,
            source_passage_id,
            revision,
        )
        if plan["refusal"]:
            raise ValueError(plan["refusal"])
        return plan["response"]


def apply(
    store,
    recording_id,
    target_id,
    source_id,
    target_passage_id,
    source_passage_id,
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
            try:
                plan = _plan(
                    store,
                    db,
                    recording_id,
                    target_id,
                    source_id,
                    target_passage_id,
                    source_passage_id,
                    revision,
                )
            except KeyError:
                raise ValueError(
                    "The wording changed in another window. Preview the adoption again."
                ) from None
            if not isinstance(fingerprint, str) or not hmac.compare_digest(
                fingerprint, plan["response"]["fingerprint"]
            ):
                raise ValueError(
                    "The wording changed in another window. Preview the adoption again."
                )
            if plan["refusal"]:
                raise ValueError(plan["refusal"])
            pair = plan["pair"]
            version = append_edit(
                store,
                db,
                pair["current"],
                target_id,
                pair["target_head"],
                plan["segments"],
                plan["speakers"],
                action="wording_adopt",
                context={"wording_adoption": plan["evidence"]},
                reason=reason,
                working_choice=working_choice,
            )
        store.snapshot_project(pair["current"]["project_id"])
        return {"recording": store.recording(recording_id), "version_id": version["id"]}

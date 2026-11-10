"""Warm validation facts cannot replace fresh structural history or writer authority."""

import hashlib
import json

import pytest
from test_undo_passage_split import options, request, save, split, state
from test_undo_passage_split import original as original

from cbs_transcriber import passage_undo
from cbs_transcriber.history import encode_payload


@pytest.mark.parametrize(
    "damage", ["old_bytes", "noncanonical", "context", "owner", "predecessor", "budget"]
)
def test_warmed_payload_validation_cannot_authorize_changed_history(
    client, original, monkeypatch, damage
):
    store = client.app.state.store
    parts = split(client, original)
    split_id = parts["active_version"]["id"]
    row = save(client, parts, parts["segments"])
    assert options(client, row)["split_segment_ids"] == ["0"]
    # Options warmed canonical-validation facts for both historical payloads.
    if damage == "budget":
        monkeypatch.setattr(passage_undo, "MAX_HISTORY_BYTES", 0)
    else:
        foreign_result = None
        if damage == "owner":
            project = store.create_project("Independent synthetic history owner")
            foreign = store.create_recording(project["id"], "foreign.wav")
            foreign_result = store.add_result(
                foreign["id"], original["segments"], {}, "Other result", {}
            )["id"]
        with store.connection() as db:
            if damage in {"old_bytes", "noncanonical"}:
                old = db.execute(
                    "SELECT payload,payload_sha256 FROM result_versions WHERE id=?", (split_id,)
                ).fetchone()
                changed = old["payload"] + " "
                digest = (
                    old["payload_sha256"]
                    if damage == "old_bytes"
                    else hashlib.sha256(changed.encode("utf-8")).hexdigest()
                )
                db.execute(
                    "UPDATE result_versions SET payload=?,payload_sha256=? WHERE id=?",
                    (changed, digest, split_id),
                )
            elif damage == "context":
                db.execute(
                    "UPDATE result_versions SET context=? WHERE id=?",
                    (json.dumps({"segment_id": "0", "cursor": 1}), split_id),
                )
            elif damage == "owner":
                db.execute(
                    "UPDATE result_versions SET result_id=? WHERE id=?", (foreign_result, split_id)
                )
            else:
                db.execute("UPDATE result_versions SET predecessor_id=NULL WHERE id=?", (split_id,))
    before = state(store, row["id"])
    assert options(client, row)["split_segment_ids"] == []
    assert request(client, row).status_code == 409
    assert state(store, row["id"]) == before


def test_warmed_payloads_are_fresh_objects_and_current_revision_stays_authoritative(
    client, original
):
    payload, digest = encode_payload(original["segments"], original["speakers"])
    first = passage_undo._read_payload(payload, digest)
    first["segments"][0]["source_detail"]["literal"][0] = "Changed caller-owned object"
    second = passage_undo._read_payload(payload, digest)
    assert second["segments"] == original["segments"]
    row = split(client, original)
    assert options(client, row)["split_segment_ids"] == ["0"]
    newer = save(client, row, row["segments"])
    before = state(client.app.state.store, row["id"])
    assert options(client, newer)["revision"] == newer["revision"]
    assert request(client, row).status_code == 409
    assert state(client.app.state.store, row["id"]) == before

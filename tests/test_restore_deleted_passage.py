"""Restore one proven deletion without undoing later passages, voices or Working choices."""

import json
from copy import deepcopy

import pytest
from test_undo_passage_split import state
from test_working_manual_policy import choice, mark

from cbs_transcriber import passage_undo
from cbs_transcriber.history import encode_payload
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def original(client):
    store = client.app.state.store
    project = store.create_project("Synthetic selective deletion restore")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [
            {"text": "Before", "speaker": "A", "start": 0, "end": 1},
            {
                "text": "Hej 👩‍🔬 cafe\u0301 og café.",
                "speaker": "B",
                "start": 2,
                "end": 5,
                "words": [{"word": "Hej", "start": 2, "end": 2.5, "score": 0.91}],
            },
            {"text": "After", "speaker": "A", "start": 6, "end": 8},
            {"text": "Tail", "speaker": "B", "start": 9, "end": 10},
        ]
    )
    segments[1]["source_detail"] = {"literal": ["Æ", None, {"nested": True}]}
    store.add_result(
        recording["id"], segments, {"A": "Anna", "B": "Benny"}, "First", {}, auto_first=True
    )
    return store.recording(recording["id"])


def remove(client, row, identifier):
    response = client.post(
        f"/api/recordings/{row['id']}/segments/{identifier}/delete",
        json={
            "result_id": row["active_transcript_id"],
            "revision": row["revision"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def options(client, row):
    response = client.get(
        f"/api/recordings/{row['id']}/merge-undo-options",
        params={"result_id": row["active_transcript_id"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def restore(client, row, descriptor, **body):
    from cbs_transcriber import passage_restore_routes

    if not any(
        getattr(route, "path", "").endswith("/{segment_id}/restore") for route in client.app.routes
    ):
        passage_restore_routes.install(client.app, client.app.state.store)
    return client.post(
        f"/api/recordings/{row['id']}/segments/{descriptor['segment_id']}/restore",
        json={
            "result_id": row["active_transcript_id"],
            "revision": row["revision"],
            "delete_version_id": descriptor["delete_version_id"],
            **body,
        },
    )


def save(client, row, segments, speakers=None, **fields):
    return client.app.state.store.save_transcript(
        row["id"],
        segments,
        row["speakers"] if speakers is None else speakers,
        row["revision"],
        result_id=row["active_transcript_id"],
        **fields,
    )


def test_deleted_only_options_are_bounded_descriptors_from_the_shared_lookup(client, original):
    row = remove(client, original, "1")
    available = options(client, row)
    assert available["segment_ids"] == available["split_segment_ids"] == []
    [deleted] = available["deleted_segments"]
    assert deleted == {
        "segment_id": "1",
        "delete_version_id": row["active_version"]["id"],
        "insertion_index": 1,
        "text_preview": original["segments"][1]["text"],
        "text_truncated": False,
    }
    assert available["revision"] == row["revision"]


@pytest.mark.parametrize("identifier", ["0", "1", "3"])
def test_restore_exact_dictionary_preserves_later_edits_voices_and_working_head(
    client, original, identifier
):
    store = client.app.state.store
    row = remove(client, original, identifier)
    segments = deepcopy(row["segments"])
    segments[-1]["text"] += " later correction 🌸"
    speakers = {**row["speakers"], "B": "Renamed after deletion", "C": "New voice"}
    current = save(client, row, segments, speakers)
    [deleted] = options(client, current)["deleted_segments"]
    response = restore(client, current, deleted, reason="Restore only the deleted passage")
    assert response.status_code == 200, response.text
    after = response.json()
    expected = deepcopy(segments)
    expected.insert(int(identifier), original["segments"][int(identifier)])
    assert after["segments"] == expected and after["speakers"] == speakers
    assert after["revision"] == current["revision"] + 1
    assert after["result_roles"]["working"]["version_id"] == after["active_version"]["id"]
    assert store.result(after["id"], after["active_transcript_id"])["segments"] == expected
    version = store.result_versions(after["id"], after["active_transcript_id"])[0]
    assert version["action"] == "passage_delete_undo"
    assert version["context"]["deleted_version_id"] == deleted["delete_version_id"]
    assert version["context"]["restored_from_version_id"] == original["active_version"]["id"]
    assert options(client, after)["deleted_segments"] == []


def test_restore_last_deleted_passage_into_empty_current_result(client, original):
    row = save(client, original, original["segments"][:1])
    prior = deepcopy(row["segments"])
    row = remove(client, row, "0")
    assert row["segments"] == []
    [deleted] = options(client, row)["deleted_segments"]
    assert deleted["insertion_index"] == 0
    response = restore(client, row, deleted)
    assert response.status_code == 200, response.text
    assert response.json()["segments"] == prior


@pytest.mark.parametrize("first,second", [("1", "2"), ("0", "1"), ("3", "2")])
def test_consecutive_deletions_require_newest_first_to_reestablish_immediate_anchors(
    client, original, first, second
):
    row = remove(client, original, first)
    old = options(client, row)["deleted_segments"][0]
    row = remove(client, row, second)
    assert [d["segment_id"] for d in options(client, row)["deleted_segments"]] == [second]
    before = state(client.app.state.store, row["id"])
    assert restore(client, row, old).status_code == 409
    assert state(client.app.state.store, row["id"]) == before
    latest = options(client, row)["deleted_segments"][0]
    response = restore(client, row, latest)
    assert response.status_code == 200, response.text
    row = response.json()
    [earlier] = options(client, row)["deleted_segments"]
    assert earlier["segment_id"] == first
    response = restore(client, row, earlier)
    assert response.status_code == 200, response.text
    assert response.json()["segments"] == original["segments"]


@pytest.mark.parametrize(
    "mutation",
    ["left_missing", "right_missing", "nonadjacent", "reordered", "id_present", "duplicate"],
)
def test_current_missing_reordered_ambiguous_or_reused_ids_refuse_without_writes(
    client, original, mutation
):
    row = remove(client, original, "1")
    [deleted] = options(client, row)["deleted_segments"]
    segments = deepcopy(row["segments"])
    if mutation == "left_missing":
        segments.pop(0)
    elif mutation == "right_missing":
        segments.pop(1)
    elif mutation == "nonadjacent":
        segments.insert(1, {**deepcopy(segments[-1]), "id": "new"})
    elif mutation == "reordered":
        segments[0], segments[1] = segments[1], segments[0]
    elif mutation == "id_present":
        segments.insert(1, {**original["segments"][1], "text": "A reused identity"})
    else:
        segments[-1]["id"] = segments[0]["id"]
    row = save(client, row, segments)
    assert options(client, row)["deleted_segments"] == []
    before = state(client.app.state.store, row["id"])
    assert restore(client, row, deleted).status_code == 409
    assert state(client.app.state.store, row["id"]) == before


@pytest.mark.parametrize("stale", ["revision", "delete_version", "result", "trashed"])
def test_restore_requires_exact_current_owned_live_proof_and_revision(client, original, stale):
    store = client.app.state.store
    row = remove(client, original, "1")
    [deleted] = options(client, row)["deleted_segments"]
    body = {}
    if stale == "revision":
        body["revision"] = original["revision"]
    elif stale == "delete_version":
        body["delete_version_id"] = original["active_version"]["id"]
    elif stale == "result":
        body["result_id"] = store.add_result(row["id"], original["segments"], {}, "Other", {})["id"]
    else:
        with store.connection() as db:
            db.execute(
                "INSERT INTO result_trash(result_id,trashed_at) VALUES (?,?)",
                (row["active_transcript_id"], "synthetic"),
            )
    before = state(store, row["id"])
    assert restore(client, row, deleted, **body).status_code == 409
    assert state(store, row["id"]) == before


@pytest.mark.parametrize(
    "damage",
    ["before_hash", "after_hash", "context", "predecessor", "sequence", "unselected", "speakers"],
)
def test_corrupt_or_unproven_delete_pair_cannot_restore(client, original, damage):
    store = client.app.state.store
    row = remove(client, original, "1")
    [deleted] = options(client, row)["deleted_segments"]
    row = save(client, row, row["segments"])
    delete_id = deleted["delete_version_id"]
    with store.connection() as db:
        if damage in {"before_hash", "after_hash"}:
            db.execute(
                "UPDATE result_versions SET payload_sha256=? WHERE id=?",
                (
                    "0" * 64,
                    original["active_version"]["id"] if damage == "before_hash" else delete_id,
                ),
            )
        elif damage == "context":
            db.execute(
                "UPDATE result_versions SET context=? WHERE id=?", ('{"segment_id":"3"}', delete_id)
            )
        elif damage == "predecessor":
            db.execute("UPDATE result_versions SET predecessor_id=NULL WHERE id=?", (delete_id,))
        elif damage == "sequence":
            db.execute("UPDATE result_versions SET sequence=sequence+40 WHERE id=?", (delete_id,))
        else:
            version = db.execute(
                "SELECT payload,payload_sha256 FROM result_versions WHERE id=?", (delete_id,)
            ).fetchone()
            payload = json.loads(version["payload"])
            if damage == "unselected":
                payload["segments"][-1]["text"] += " forged"
            else:
                payload["speakers"]["A"] = "Changed during alleged deletion"
            text, digest = encode_payload(payload["segments"], payload["speakers"])
            db.execute(
                "UPDATE result_versions SET payload=?,payload_sha256=? WHERE id=?",
                (text, digest, delete_id),
            )
    assert options(client, row)["deleted_segments"] == []
    before = state(store, row["id"])
    assert restore(client, row, deleted).status_code == 409
    assert state(store, row["id"]) == before


def test_restored_historical_presence_prevents_duplicate_and_redelete_uses_latest_payload(
    client, original
):
    row = remove(client, original, "1")
    [old] = options(client, row)["deleted_segments"]
    row = save(
        client,
        row,
        original["segments"],
        action="version_restore",
        context={"restored_from_version_id": original["active_version"]["id"]},
    )
    assert options(client, row)["deleted_segments"] == []
    assert restore(client, row, old).status_code == 409
    current = deepcopy(row["segments"])
    current[1]["text"] = "A later deleted wording"
    row = save(client, row, current)
    row = remove(client, row, "1")
    [latest] = options(client, row)["deleted_segments"]
    assert latest["delete_version_id"] != old["delete_version_id"]
    assert restore(client, row, old).status_code == 409
    response = restore(client, row, latest)
    assert response.status_code == 200 and response.json()["segments"] == current


def test_corrupt_latest_delete_never_falls_back_to_an_older_deleted_wording(client, original):
    store = client.app.state.store
    row = remove(client, original, "1")
    [earlier] = options(client, row)["deleted_segments"]
    row = restore(client, row, earlier).json()
    segments = deepcopy(row["segments"])
    segments[1]["text"] = "A newer wording must not be replaced by the old deleted wording"
    row = save(client, row, segments)
    row = remove(client, row, "1")
    latest_id = row["active_version"]["id"]
    row = save(client, row, row["segments"])
    with store.connection() as db:
        db.execute("UPDATE result_versions SET payload_sha256=? WHERE id=?", ("0" * 64, latest_id))
    assert options(client, row)["deleted_segments"] == []
    assert restore(client, row, earlier).status_code == 409


@pytest.mark.parametrize("method", ["individual", "history"])
def test_restored_then_merged_passage_cannot_be_restored_twice(client, original, method):
    row = remove(client, original, "1")
    [deleted] = options(client, row)["deleted_segments"]
    if method == "individual":
        response = restore(client, row, deleted)
    else:
        response = client.post(
            f"/api/recordings/{row['id']}/transcripts/{row['active_transcript_id']}/versions/{original['active_version']['id']}/restore",
            json={"revision": row["revision"]},
        )
    assert response.status_code == 200, response.text
    row = response.json()
    response = client.post(
        f"/api/recordings/{row['id']}/segments/0/merge",
        json={
            "result_id": row["active_transcript_id"],
            "revision": row["revision"],
            "direction": "down",
            "speaker_id": "A",
        },
    )
    assert response.status_code == 200, response.text
    row = response.json()
    available = options(client, row)
    assert available["segment_ids"] == ["0"]
    assert available["deleted_segments"] == []
    before = state(client.app.state.store, row["id"])
    assert restore(client, row, deleted).status_code == 409
    assert state(client.app.state.store, row["id"]) == before


def test_restored_split_half_consumed_by_undo_split_cannot_be_restored_again(client, original):
    response = client.post(
        f"/api/recordings/{original['id']}/segments/1/split",
        json={
            "result_id": original["active_transcript_id"],
            "revision": original["revision"],
            "cursor": len("Hej "),
            "split_time": 2.5,
        },
    )
    assert response.status_code == 200, response.text
    row = response.json()
    right_id = row["segments"][2]["id"]
    row = remove(client, row, right_id)
    [deleted] = options(client, row)["deleted_segments"]
    row = restore(client, row, deleted).json()
    response = client.post(
        f"/api/recordings/{row['id']}/segments/1/undo-split",
        json={
            "result_id": row["active_transcript_id"],
            "revision": row["revision"],
        },
    )
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["segments"] == original["segments"]
    assert options(client, row)["deleted_segments"] == []
    assert restore(client, row, deleted).status_code == 409


def test_whole_version_restore_of_merged_wording_does_not_reactivate_old_delete_proof(
    client, original
):
    response = client.post(
        f"/api/recordings/{original['id']}/segments/0/merge",
        json={
            "result_id": original["active_transcript_id"],
            "revision": original["revision"],
            "direction": "down",
            "speaker_id": "A",
        },
    )
    assert response.status_code == 200, response.text
    merged = response.json()
    response = client.post(
        f"/api/recordings/{original['id']}/transcripts/{original['active_transcript_id']}/versions/{original['active_version']['id']}/restore",
        json={"revision": merged["revision"]},
    )
    assert response.status_code == 200, response.text
    row = remove(client, response.json(), "1")
    [deleted] = options(client, row)["deleted_segments"]
    response = client.post(
        f"/api/recordings/{row['id']}/transcripts/{row['active_transcript_id']}/versions/{merged['active_version']['id']}/restore",
        json={"revision": row["revision"]},
    )
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["segments"] == merged["segments"]
    assert options(client, row)["deleted_segments"] == []
    before = state(client.app.state.store, row["id"])
    assert restore(client, row, deleted).status_code == 409
    assert state(client.app.state.store, row["id"]) == before


def test_deleted_preview_is_bounded_without_altering_the_restored_unicode_text(client, original):
    segments = deepcopy(original["segments"])
    segments[1]["text"] = "👩‍🔬 café æøå " * 100
    row = save(client, original, segments)
    row = remove(client, row, "1")
    [deleted] = options(client, row)["deleted_segments"]
    assert len(deleted["text_preview"]) == 240 and deleted["text_truncated"] is True
    assert set(deleted) == {
        "segment_id",
        "delete_version_id",
        "insertion_index",
        "text_preview",
        "text_truncated",
    }
    response = restore(client, row, deleted)
    assert response.status_code == 200 and response.json()["segments"] == segments


@pytest.mark.parametrize(
    "body",
    [
        {"revision": True},
        {"revision": -1},
        {"delete_version_id": 123},
        {"reason": "x" * 2001},
        {"restore_everything": True},
    ],
)
def test_restore_rejects_invalid_envelopes_without_writes(client, original, body):
    row = remove(client, original, "1")
    [deleted] = options(client, row)["deleted_segments"]
    before = state(client.app.state.store, row["id"])
    assert restore(client, row, deleted, **body).status_code == 422
    assert state(client.app.state.store, row["id"]) == before


@pytest.mark.parametrize(
    "budget", ["MAX_MERGES", "MAX_HISTORY_VERSIONS", "MAX_SNAPSHOT_BYTES", "MAX_HISTORY_BYTES"]
)
def test_deletion_lookup_and_apply_share_existing_bounded_history_limits(
    client, original, monkeypatch, budget
):
    row = remove(client, original, "1")
    [deleted] = options(client, row)["deleted_segments"]
    monkeypatch.setattr(passage_undo, budget, 0)
    available = options(client, row)
    assert available["deleted_segments"] == [] and available["history_limited"] is True
    assert restore(client, row, deleted).status_code == 409


@pytest.mark.parametrize("decision", ["keep", "promote", "stale", "working_false"])
def test_restore_working_choice_is_fenced_in_the_same_transaction(client, original, decision):
    store = client.app.state.store
    pinned = original["active_version"]["id"]
    alternate = store.add_result(
        original["id"], original["segments"], original["speakers"], "Alternate", {}
    )
    row = store.activate_result(original["id"], alternate["id"], original["revision"])
    row = remove(client, row, "1")
    [deleted] = options(client, row)["deleted_segments"]
    if decision == "working_false":
        row = mark(store, row["id"], row["active_version"]["id"])
    selected = choice(row, decision not in {"keep", "working_false"})
    if decision == "stale":
        mark(store, row["id"], None)
    before = state(store, row["id"])
    response = restore(client, row, deleted, working_choice=selected)
    if decision in {"stale", "working_false"}:
        assert response.status_code == 409
        assert state(store, row["id"]) == before
    else:
        assert response.status_code == 200, response.text
        after = response.json()
        assert after["result_roles"]["working"]["version_id"] == (
            pinned if decision == "keep" else after["active_version"]["id"]
        )

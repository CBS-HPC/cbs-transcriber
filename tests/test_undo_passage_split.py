"""A proven split can be undone without rolling back unrelated transcript work."""

from copy import deepcopy

import pytest
from test_working_manual_policy import choice, mark

from cbs_transcriber import passage_undo
from cbs_transcriber.history import encode_payload
from cbs_transcriber.transcripts import normalize_segments, split_passage

CURSOR = len("Hej 👩‍🔬 ")


@pytest.fixture
def original(client):
    store = client.app.state.store
    project = store.create_project("Synthetic split undo")
    row = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [
            {
                "text": "Hej 👩‍🔬 cafe\u0301 og café.",
                "speaker": "A",
                "start": 1,
                "end": 5,
                "words": [
                    {"word": "Hej", "start": 1.1, "end": 1.5, "score": 0.91},
                    {"word": "cafe\u0301", "start": 2.1, "end": 2.7, "score": 0.83},
                    {"word": "og", "start": 3.1, "end": 3.4},
                    {"word": "café", "start": 4.1, "end": 4.8},
                ],
            },
            {"text": "Unrelated tail", "speaker": "B", "start": 6, "end": 8},
            {"text": "Another passage", "speaker": "B", "start": 9, "end": 11},
        ]
    )
    segments[0]["source_detail"] = {"literal": ["Æ", None, {"nested": True}]}
    segments[1]["source_detail"] = {"retain": ["all", "fields"]}
    store.add_result(
        row["id"], segments, {"A": "Interviewer", "B": "Respondent"}, "First", {}, auto_first=True
    )
    return store.recording(row["id"])


def request(client, row, operation="undo-split", segment_id="0", **body):
    return client.post(
        f"/api/recordings/{row['id']}/segments/{segment_id}/{operation}",
        json={"revision": row["revision"], "result_id": row["active_transcript_id"], **body},
    )


def split(client, row, segment_id="0", cursor=CURSOR, **body):
    response = request(client, row, "split", segment_id, cursor=cursor, **body)
    assert response.status_code == 200, response.text
    return response.json()


def options(client, row):
    response = client.get(
        f"/api/recordings/{row['id']}/merge-undo-options",
        params={"result_id": row["active_transcript_id"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def save(client, row, segments, speakers=None, **kwargs):
    return client.app.state.store.save_transcript(
        row["id"],
        segments,
        row["speakers"] if speakers is None else speakers,
        row["revision"],
        result_id=row["active_transcript_id"],
        **kwargs,
    )


def state(store, rid):
    """Observe persistent rows directly even when deliberately corrupting synthetic history."""
    rows = {
        table: store.all(f"SELECT * FROM {table} WHERE {field}=? ORDER BY rowid", (rid,))
        for table, field in (
            ("recordings", "id"),
            ("transcript_results", "recording_id"),
            ("recording_result_roles", "recording_id"),
            ("result_role_events", "recording_id"),
        )
    }
    rows["result_versions"] = store.all(
        "SELECT * FROM result_versions WHERE result_id IN "
        "(SELECT id FROM transcript_results WHERE recording_id=?) ORDER BY rowid",
        (rid,),
    )
    return rows


@pytest.mark.parametrize("timing", ["complete", "partial", "untimed", "corrected"])
def test_undo_restores_exact_unicode_original_and_preserves_unrelated_edits(
    client, original, timing
):
    store = client.app.state.store
    if timing != "complete":
        segments = deepcopy(original["segments"])
        if timing == "partial":
            segments[0]["words"][2].update(start=None, end=None)
            segments[0]["word_timing"] = False
        elif timing == "untimed":
            segments[0].update(start=None, end=None, words=[], word_timing=False)
        else:
            from cbs_transcriber.transcripts import edit_segment

            segments[0] = edit_segment(segments[0], "word", "Goddag", 0)
        original = save(client, original, segments)
    cursor = len("Goddag 👩‍🔬 ") if timing == "corrected" else len("Hej 👩‍🔬 ")
    parts = split(
        client, original, cursor=cursor, **({"split_time": 2.0} if timing == "partial" else {})
    )
    right_id = parts["segments"][1]["id"]
    assert right_id not in {p["id"] for p in original["segments"]}
    later = deepcopy(parts["segments"])
    later[2]["text"] = "Later independent edit 🌸"
    speakers = {**parts["speakers"], "A": "Renamed after splitting", "C": "New voice"}
    current = save(client, parts, later, speakers)
    available = options(client, current)
    assert available["segment_ids"] == []
    assert available["split_segment_ids"] == ["0"]
    assert available["revision"] == current["revision"]
    assert request(client, current, segment_id=right_id).status_code == 409
    response = request(client, current, reason="Restore the exact original split passage")
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["segments"] == [original["segments"][0], *later[2:]]
    assert after["speakers"] == speakers
    assert after["revision"] == current["revision"] + 1
    assert store.result(after["id"], after["active_transcript_id"])["segments"] == after["segments"]
    version = store.result_versions(after["id"], after["active_transcript_id"])[0]
    assert version["action"] == "passage_split_undo"
    assert version["context"]["segment_id"] == "0"
    assert version["context"]["adjacent_segment_id"] == right_id
    assert version["context"]["restored_from_version_id"] == original["active_version"]["id"]
    assert version["reason"] == "Restore the exact original split passage"
    assert after["result_roles"]["working"]["version_id"] == after["active_version"]["id"]
    assert options(client, after)["split_segment_ids"] == []


@pytest.mark.parametrize(
    "mutation",
    ["left_text", "right_word", "right_speaker", "removed", "reuse", "duplicate", "separated"],
)
def test_undo_refuses_changed_nonadjacent_or_nonunique_halves(client, original, mutation):
    row = split(client, original)
    segments = deepcopy(row["segments"])
    if mutation == "left_text":
        segments[0]["text"] += " changed"
    elif mutation == "right_word":
        segments[1]["words"][0]["score"] = 0.123
    elif mutation == "right_speaker":
        segments[1]["speaker"] = "B"
    elif mutation == "removed":
        segments.pop(1)
    elif mutation == "reuse":
        segments[1]["id"] = "new-unproven-id"
    elif mutation == "duplicate":
        segments[-1]["id"] = segments[1]["id"]
    else:
        segments[1], segments[2] = segments[2], segments[1]
    current = save(client, row, segments)
    store = client.app.state.store
    before = state(store, row["id"])
    assert options(client, current)["split_segment_ids"] == []
    assert request(client, current).status_code == 409
    assert state(store, row["id"]) == before


@pytest.mark.parametrize("stale", ["revision", "result", "foreign", "trashed"])
def test_undo_requires_current_live_owned_result(client, original, stale):
    store = client.app.state.store
    row = split(client, original)
    body = {}
    if stale == "revision":
        body["revision"] = original["revision"]
    elif stale in {"result", "foreign"}:
        rid = row["id"]
        if stale == "foreign":
            project = store.create_project("Other synthetic project")
            rid = store.create_recording(project["id"], "other.wav")["id"]
        body["result_id"] = store.add_result(rid, original["segments"], {}, "Other", {})["id"]
    else:
        # A corrupt visibility projection must not allow restoring a trashed result.
        with store.connection() as db:
            db.execute(
                "INSERT INTO result_trash(result_id,trashed_at) VALUES (?,?)",
                (row["active_transcript_id"], "synthetic"),
            )
    before = state(store, row["id"])
    assert request(client, row, **body).status_code == 409
    assert state(store, row["id"]) == before


@pytest.mark.parametrize(
    "forged", ["cursor", "left", "right", "unrelated", "speakers", "reused_id"]
)
def test_history_action_and_context_alone_do_not_authorize_undo(client, original, forged):
    segments = split_passage(original["segments"], "0", len("Hej 👩‍🔬 "))
    context = {"segment_id": "0", "cursor": len("Hej 👩‍🔬 ")}
    speakers = original["speakers"]
    if forged == "cursor":
        context["cursor"] += 1
    elif forged == "left":
        segments[0]["text"] += "?"
    elif forged == "right":
        segments[1]["words"][0]["end"] += 0.1
    elif forged == "unrelated":
        segments[-1]["text"] = "Changed during the alleged split"
    elif forged == "speakers":
        speakers = {**speakers, "A": "Changed during the alleged split"}
    else:
        segments[1]["id"] = original["segments"][-1]["id"]
    row = save(client, original, segments, speakers, action="passage_split", context=context)
    assert options(client, row)["split_segment_ids"] == []
    assert request(client, row).status_code == 409


@pytest.mark.parametrize(
    "damage",
    [
        "before_hash",
        "after_hash",
        "sequence",
        "result_owner",
        "predecessor",
        "context",
        "duplicate_ids",
    ],
)
def test_history_integrity_and_ownership_are_required(client, original, damage):
    store = client.app.state.store
    creation_id = original["active_version"]["id"]
    if damage == "sequence":
        original = save(client, original, original["segments"])
    parts = split(client, original)
    split_id = parts["active_version"]["id"]
    # Keep the current head intact while corrupting the old split proof.
    row = save(client, parts, parts["segments"])
    foreign_result = None
    if damage == "result_owner":
        project = store.create_project("Foreign synthetic history")
        foreign = store.create_recording(project["id"], "foreign.wav")
        foreign_result = store.add_result(foreign["id"], original["segments"], {}, "Other", {})[
            "id"
        ]
    with store.connection() as db:
        if damage in {"before_hash", "after_hash"}:
            target = original["active_version"]["id"] if damage == "before_hash" else split_id
            db.execute("UPDATE result_versions SET payload_sha256=? WHERE id=?", ("0" * 64, target))
        elif damage == "result_owner":
            db.execute(
                "UPDATE result_versions SET result_id=? WHERE id=?", (foreign_result, split_id)
            )
        elif damage == "sequence":
            db.execute(
                "UPDATE result_versions SET predecessor_id=? WHERE id=?",
                (creation_id, split_id),
            )
        elif damage == "predecessor":
            db.execute("UPDATE result_versions SET predecessor_id=NULL WHERE id=?", (split_id,))
        elif damage == "context":
            db.execute(
                "UPDATE result_versions SET context=? WHERE id=?", ('{"segment_id":[]}', split_id)
            )
        else:
            segments = deepcopy(parts["segments"])
            segments[-1]["id"] = segments[1]["id"]
            payload, digest = encode_payload(segments, parts["speakers"])
            db.execute(
                "UPDATE result_versions SET payload=?,payload_sha256=? WHERE id=?",
                (payload, digest, split_id),
            )
    before = state(store, row["id"])
    assert options(client, row)["split_segment_ids"] == []
    assert request(client, row).status_code == 409
    assert state(store, row["id"]) == before


def test_nested_and_repeated_splits_restore_each_exact_predecessor(client, original):
    outer = split(client, original)
    right_id = outer["segments"][1]["id"]
    inner = split(client, outer, right_id, len("cafe\u0301 "))
    assert options(client, inner)["split_segment_ids"] == [right_id]
    assert request(client, inner).status_code == 409
    response = request(client, inner, segment_id=right_id)
    assert response.status_code == 200, response.text
    after_inner = response.json()
    assert after_inner["segments"] == outer["segments"]
    assert options(client, after_inner)["split_segment_ids"] == ["0"]
    response = request(client, after_inner)
    assert response.status_code == 200, response.text
    assert response.json()["segments"] == original["segments"]
    repeated = split(client, response.json())
    assert repeated["segments"][1]["id"] != right_id
    assert request(client, repeated).json()["segments"] == original["segments"]


@pytest.mark.parametrize("damage", ["head_hash", "mirror"])
def test_current_projection_integrity_refuses_with_recoverable_conflict(client, original, damage):
    store = client.app.state.store
    row = split(client, original)
    with store.connection() as db:
        if damage == "head_hash":
            db.execute(
                "UPDATE result_versions SET payload_sha256=? WHERE id=?",
                ("0" * 64, row["active_version"]["id"]),
            )
        else:
            db.execute("UPDATE recordings SET segments='[]' WHERE id=?", (row["id"],))
    before = state(store, row["id"])
    lookup = client.get(
        f"/api/recordings/{row['id']}/merge-undo-options",
        params={"result_id": row["active_transcript_id"]},
    )
    assert lookup.status_code == 409, lookup.text
    assert request(client, row).status_code == 409
    assert state(store, row["id"]) == before


def test_exact_historical_pair_restored_again_can_be_undone_again(client, original):
    outer = split(client, original)
    response = request(client, outer)
    assert response.status_code == 200, response.text
    # Emulate explicit version restoration: the exact historical split is current again.
    restored = save(
        client,
        response.json(),
        outer["segments"],
        action="version_restore",
        context={"restored_from_version_id": outer["active_version"]["id"]},
    )
    assert options(client, restored)["split_segment_ids"] == ["0"]
    assert request(client, restored).json()["segments"] == original["segments"]


@pytest.mark.parametrize("decision", ["promote", "keep", "stale", "working", "working_false"])
def test_undo_fences_working_choice_atomically(client, original, decision):
    store = client.app.state.store
    pinned = original["active_version"]["id"]
    result = store.add_result(
        original["id"], original["segments"], original["speakers"], "Alternative", {}
    )
    row = store.activate_result(original["id"], result["id"], original["revision"])
    row = split(client, row)
    if decision in {"working", "working_false"}:
        row = mark(store, row["id"], row["active_version"]["id"])
    selected = choice(row, decision not in {"keep", "working_false"})
    if decision == "stale":
        mark(store, row["id"], None)
    before = state(store, row["id"])
    response = request(client, row, working_choice=selected)
    if decision in {"stale", "working_false"}:
        assert response.status_code == 409, response.text
        assert state(store, row["id"]) == before
    else:
        assert response.status_code == 200, response.text
        after = response.json()
        assert after["segments"] == original["segments"]
        assert after["result_roles"]["working"]["version_id"] == (
            pinned if decision == "keep" else after["active_version"]["id"]
        )


@pytest.mark.parametrize(
    "budget", ["MAX_MERGES", "MAX_HISTORY_VERSIONS", "MAX_SNAPSHOT_BYTES", "MAX_HISTORY_BYTES"]
)
def test_split_lookup_and_apply_share_bounded_history_budgets(
    client, original, monkeypatch, budget
):
    row = split(client, original)
    monkeypatch.setattr(passage_undo, budget, 0)
    available = options(client, row)
    assert available["split_segment_ids"] == []
    assert available["history_limited"] is True
    before = state(client.app.state.store, row["id"])
    assert request(client, row).status_code == 409
    assert state(client.app.state.store, row["id"]) == before


def test_merge_and_split_share_one_candidate_budget(client, original, monkeypatch):
    row = split(client, original)
    merged = request(client, row, "merge", "1", direction="down")
    assert merged.status_code == 200, merged.text
    row = merged.json()
    monkeypatch.setattr(passage_undo, "MAX_MERGES", 1)
    available = options(client, row)
    assert available["segment_ids"] == ["1"]
    assert available["split_segment_ids"] == []
    assert available["history_limited"] is True
    monkeypatch.setattr(passage_undo, "MAX_MERGES", 2)
    available = options(client, row)
    assert available["segment_ids"] == ["1"]
    assert available["split_segment_ids"] == ["0"]


def test_undo_rollback_keeps_projection_and_history_atomic(client, original):
    import sqlite3

    store = client.app.state.store
    row = split(client, original)
    before = state(store, row["id"])
    with store.connection() as db:
        db.execute(
            "CREATE TRIGGER reject_split_undo BEFORE INSERT ON result_versions "
            "WHEN NEW.action='passage_split_undo' "
            "BEGIN SELECT RAISE(ABORT,'synthetic undo failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic undo failure"):
        request(client, row)
    assert state(store, row["id"]) == before

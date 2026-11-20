"""Undo last change and Redo by exact predecessor restoration (ADR 0180). Synthetic data only."""

import json
from copy import deepcopy

import pytest
from test_passage_review import mark as review_mark
from test_passage_review import read as review_read
from test_working_manual_policy import choice, mark

from cbs_transcriber import change_undo, passage_undo, release_profile, research_reports
from cbs_transcriber.history import EDIT_ACTIONS, encode_payload
from cbs_transcriber.transcripts import normalize_segments

TEXTS = ("Alpha one two", "Beta three four", "Gamma five six")


@pytest.fixture
def row(client):
    store = client.app.state.store
    project = store.create_project("Synthetic undo last change")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [
            {"text": text, "speaker": "A", "start": 2 * i, "end": 2 * i + 1.5}
            for i, text in enumerate(TEXTS)
        ]
    )
    segments[1]["source_detail"] = {"literal": ["Æ", None, {"nested": True}]}
    store.add_result(recording["id"], segments, {"A": "Anna"}, "First", {}, auto_first=True)
    return store.recording(recording["id"])


def edit(client, row, index, text):
    response = client.patch(
        f"/api/recordings/{row['id']}/segments/{row['segments'][index]['id']}",
        json={"revision": row["revision"], "mode": "free", "text": text},
    )
    assert response.status_code == 200, response.text
    return response.json()


def structure(client, row, segment_id, operation, **values):
    response = client.post(
        f"/api/recordings/{row['id']}/segments/{segment_id}/{operation}",
        json={"revision": row["revision"], "result_id": row["active_transcript_id"], **values},
    )
    assert response.status_code == 200, response.text
    return response.json()


def offered(client, row):
    response = client.get(
        f"/api/recordings/{row['id']}/merge-undo-options",
        params={"result_id": row["active_transcript_id"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def step(client, row, kind="undo", version_id=None, **body):
    if version_id is None:
        version_id = offered(client, row)[kind]["version_id"]
    return client.post(
        f"/api/recordings/{row['id']}/transcripts/{row['active_transcript_id']}/{kind}-last",
        json={
            "revision": row["revision"],
            "head_version_id": row["active_version"]["id"],
            "version_id": version_id,
            **body,
        },
    )


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def content(row):
    return deepcopy(row["segments"]), deepcopy(row["speakers"])


def versions(store, row):
    return store.all(
        "SELECT id,sequence,action,context FROM result_versions WHERE result_id=? "
        "ORDER BY sequence",
        (row["active_transcript_id"],),
    )


def test_text_edit_undo_restores_exact_predecessor_and_redo_resaves_the_edit(client, row):
    store = client.app.state.store
    original = content(row)
    edited = edit(client, row, 1, "Beta changed 🌸 café")
    changed = content(edited)
    available = offered(client, edited)
    assert available["head_version_id"] == edited["active_version"]["id"]
    assert available["undo"] == {
        "available": True,
        "version_id": edited["active_version"]["id"],
        "action": "free_rewrite",
        "sequence": 2,
        "observed_at": available["undo"]["observed_at"],
        "passages_changed": 1,
        "speakers_changed": False,
        "passage_number": 2,
        "confirm": False,
        "segment_id": edited["segments"][1]["id"],
    }
    assert available["redo"] == {"available": False, "message": "Nothing to redo."}
    undone = ok(step(client, edited, reason="Typo in my edit"))
    assert content(undone) == original
    assert undone["segments"][1]["source_detail"] == {"literal": ["Æ", None, {"nested": True}]}
    rows = versions(store, undone)
    assert [r["action"] for r in rows] == ["provider_result", "free_rewrite", "change_undo"]
    assert (
        json.loads(rows[-1]["context"]).items()
        >= {
            "undone_version_id": rows[1]["id"],
            "restored_from_version_id": rows[0]["id"],
        }.items()
    )
    first = store.result_version(row["id"], row["active_transcript_id"], rows[0]["id"])
    last = store.result_version(row["id"], row["active_transcript_id"], rows[-1]["id"])
    assert last["payload_sha256"] == first["payload_sha256"]
    assert last["reason"] == "Typo in my edit"
    after_undo = offered(client, undone)
    assert after_undo["undo"]["available"] is False
    assert after_undo["undo"]["message"] == "Nothing earlier to undo in this result."
    assert after_undo["redo"]["action"] == "free_rewrite"
    assert after_undo["redo"]["version_id"] == rows[1]["id"]
    redone = ok(step(client, undone, "redo"))
    assert content(redone) == changed
    rows = versions(store, redone)
    assert rows[-1]["action"] == "change_redo"
    assert (
        json.loads(rows[-1]["context"]).items()
        >= {"redone_version_id": rows[1]["id"], "undo_version_id": rows[2]["id"]}.items()
    )
    again = offered(client, redone)
    assert again["undo"]["action"] == "free_rewrite"  # A Redo is labelled by what it re-saved.
    assert again["undo"]["version_id"] == rows[-1]["id"]
    assert again["redo"]["available"] is False
    assert content(ok(step(client, redone))) == original


def test_undo_walks_back_one_step_per_click_to_the_start(client, row):
    store = client.app.state.store
    states = [content(row)]
    current = edit(client, row, 0, "Alpha first change")
    states.append(content(current))
    current = edit(client, current, 2, "Gamma second change")
    states.append(content(current))
    renamed = store.save_transcript(
        row["id"],
        current["segments"],
        {"A": "Anna Renamed"},
        current["revision"],
        action="speaker_rename",
        result_id=row["active_transcript_id"],
    )
    states.append(content(renamed))
    current = renamed
    labels = []
    for expected in reversed(states[:-1]):
        available = offered(client, current)
        labels.append(available["undo"]["action"])
        current = ok(step(client, current))
        assert content(current) == expected
    assert labels == ["speaker_rename", "free_rewrite", "free_rewrite"]
    assert offered(client, current)["undo"]["message"] == "Nothing earlier to undo in this result."
    # Redo walks forward again through the same three changes.
    for expected in states[1:]:
        current = ok(step(client, current, "redo"))
        assert content(current) == expected
    assert offered(client, current)["redo"]["available"] is False


def test_no_change_saves_and_trash_restore_markers_are_skipped(client, row):
    store = client.app.state.store
    original = content(row)
    current = edit(client, row, 0, "Alpha edited")
    current = store.save_transcript(
        row["id"],
        current["segments"],
        current["speakers"],
        current["revision"],
        action="free_rewrite",
        result_id=row["active_transcript_id"],
    )
    trashed = store.set_result_trashed(
        row["id"], row["active_transcript_id"], current["revision"], trashed=True
    )
    restored = store.set_result_trashed(
        row["id"], row["active_transcript_id"], trashed["revision"], trashed=False
    )
    assert restored["active_transcript_id"] == row["active_transcript_id"]
    actions = [r["action"] for r in versions(store, restored)]
    assert actions[-3:] == ["save_no_change", "result_trashed", "result_restored"]
    available = offered(client, restored)
    assert available["undo"]["action"] == "free_rewrite"
    assert available["undo"]["sequence"] == 2
    assert content(ok(step(client, restored))) == original


def test_a_new_change_clears_redo_and_becomes_the_next_undo(client, row):
    current = edit(client, row, 0, "Alpha edited")
    current = ok(step(client, current))
    assert offered(client, current)["redo"]["available"] is True
    current = edit(client, current, 2, "Gamma different edit")
    available = offered(client, current)
    assert available["redo"] == {"available": False, "message": "Nothing to redo."}
    assert available["undo"]["segment_id"] == current["segments"][2]["id"]
    current = ok(step(client, current))
    # Undoing the new change does not resurrect the earlier, already-undone edit.
    assert current["segments"][0]["text"] == TEXTS[0]
    assert current["segments"][2]["text"] == TEXTS[2]
    assert offered(client, current)["undo"]["message"] == "Nothing earlier to undo in this result."


def test_stale_views_are_refused_without_writes(client, row):
    store = client.app.state.store
    current = edit(client, row, 0, "Alpha edited")
    seen = offered(client, current)
    newer = edit(client, current, 1, "Beta edited elsewhere")
    before = versions(store, newer)
    stale_revision = step(client, current, version_id=seen["undo"]["version_id"])
    assert stale_revision.status_code == 409
    stale_head = step(
        client,
        {**newer, "active_version": current["active_version"]},
        version_id=seen["undo"]["version_id"],
    )
    assert stale_head.status_code == 409
    wrong_step = step(client, newer, version_id=seen["undo"]["version_id"])
    assert wrong_step.status_code == 409
    assert "newest change is different" in wrong_step.json()["detail"]
    nothing = step(client, newer, "redo", version_id=seen["undo"]["version_id"])
    assert nothing.status_code == 409
    assert versions(store, newer) == before
    assert store.recording(row["id"])["revision"] == newer["revision"]


@pytest.mark.parametrize(
    "body",
    [
        {"version_id": ""},
        {"head_version_id": "x" * 101},
        {"revision": -1},
        {"extra": True},
        {"working_choice": {"version_id": "a", "role_revision": 0}},
    ],
)
def test_invalid_requests_are_rejected(client, row, body):
    current = edit(client, row, 0, "Alpha edited")
    assert step(client, current, **body).status_code == 422


def test_corrupt_predecessor_or_mirror_refuses_without_writes(client, row):
    store = client.app.state.store
    current = edit(client, row, 0, "Alpha edited")
    seen = offered(client, current)
    first = versions(store, current)[0]["id"]
    corrupt = "UPDATE result_versions SET payload=replace(payload,?,?) WHERE id=?"
    with store.connection() as db:
        db.execute(corrupt, ("Alpha one", "Alpha 0ne", first))
    before = versions(store, current)
    refused = step(client, current, version_id=seen["undo"]["version_id"])
    assert refused.status_code == 409
    assert "integrity" in refused.json()["detail"]
    assert versions(store, current) == before
    damaged = offered(client, current)["undo"]
    assert damaged["available"] is False and "integrity" in damaged["message"]
    with store.connection() as db:
        db.execute(corrupt, ("Alpha 0ne", "Alpha one", first))
        db.execute(
            "UPDATE recordings SET segments=replace(segments,'Alpha edited','Alpha mirror') "
            "WHERE id=?",
            (row["id"],),
        )
    mirror = step(client, current, version_id=seen["undo"]["version_id"])
    assert mirror.status_code == 409
    assert versions(store, current) == before


def test_legacy_start_and_history_bounds_disable_undo(client, row, monkeypatch):
    store = client.app.state.store
    with store.connection() as db:
        db.execute(
            "UPDATE result_versions SET action='legacy_observed' WHERE result_id=? AND sequence=1",
            (row["active_transcript_id"],),
        )
    assert "unknown" in offered(client, row)["undo"]["message"]
    current = edit(client, row, 0, "Alpha edited")
    for _ in range(3):
        current = store.save_transcript(
            row["id"],
            current["segments"],
            current["speakers"],
            current["revision"],
            action="free_rewrite",
            result_id=row["active_transcript_id"],
        )
    monkeypatch.setattr(change_undo, "MAX_HISTORY_VERSIONS", 3)
    limited = offered(client, current)["undo"]
    assert limited["available"] is False and "beyond the undo limit" in limited["message"]
    monkeypatch.setattr(change_undo, "MAX_HISTORY_VERSIONS", 500)
    assert offered(client, current)["undo"]["sequence"] == 2


@pytest.mark.parametrize(
    ("action", "label"),
    [
        ("word_correction", "word_correction"),
        ("word_timing_edit", "word_timing_edit"),
        ("original_restore", "original_restore"),
        ("passage_speaker", "passage_speaker"),
        ("speaker_rename", "speaker_rename"),
        ("manual_save", "manual_save"),
        ("version_restore", "version_restore"),
    ],
)
def test_every_saved_content_change_is_an_undo_step(client, row, action, label):
    store = client.app.state.store
    segments = deepcopy(row["segments"])
    for passage in segments:
        passage["text"] += " changed by " + action
    saved = store.save_transcript(
        row["id"],
        segments,
        row["speakers"],
        row["revision"],
        action=action,
        result_id=row["active_transcript_id"],
    )
    available = offered(client, saved)["undo"]
    assert available["action"] == label
    assert available["passages_changed"] == 3
    assert available["confirm"] is True  # Three or more passages are "several".
    assert content(ok(step(client, saved))) == content(row)


def test_whole_result_actions_count_as_changes(client, row):
    store = client.app.state.store
    current = row
    for action in ("timing_realign", "result_merge", "wording_adopt", "result_conflict_review"):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            head = store._head_version(
                db,
                db.execute(
                    "SELECT * FROM transcript_results WHERE id=?", (row["active_transcript_id"],)
                ).fetchone(),
            )
            segments = deepcopy(current["segments"])
            segments[1]["text"] = f"Beta via {action}"
            passage_undo.append_edit(
                store,
                db,
                db.execute("SELECT * FROM recordings WHERE id=?", (row["id"],)).fetchone(),
                row["active_transcript_id"],
                head,
                segments,
                current["speakers"],
                action=action,
                context={},
                reason=None,
            )
        current = store.recording(row["id"])
    for expected in ("result_conflict_review", "wording_adopt", "result_merge", "timing_realign"):
        assert offered(client, current)["undo"]["action"] == expected
        current = ok(step(client, current))
    assert content(current) == content(row)


def test_structural_undo_agrees_with_the_proven_inverses(client, row):
    store = client.app.state.store
    ids = [p["id"] for p in row["segments"]]
    merged = structure(client, row, ids[0], "merge", direction="down")
    available = offered(client, merged)["undo"]
    assert available["action"] == "passage_merge"
    assert (available["passages_changed"], available["confirm"]) == (2, False)
    undone = ok(step(client, merged))
    assert content(undone) == content(row)
    split = structure(client, undone, ids[1], "split-v2", cursor=5)
    assert len(split["segments"]) == 4
    assert offered(client, split)["undo"]["action"] == "passage_split"
    undone = ok(step(client, split))
    assert content(undone) == content(row)
    legacy_split = structure(client, undone, ids[2], "split", cursor=6, split_time=4.6)
    assert len(legacy_split["segments"]) == 4
    undone = ok(step(client, legacy_split))
    assert content(undone) == content(row)
    deleted = structure(client, undone, ids[1], "delete")
    assert offered(client, deleted)["undo"]["action"] == "passage_delete"
    undone = ok(step(client, deleted))
    assert content(undone) == content(row)
    # A structural row whose saved context no longer proves its change is refused.
    merged = structure(client, undone, ids[0], "merge", direction="down")
    with store.connection() as db:
        db.execute(
            "UPDATE result_versions SET context=json_set(context,'$.direction','up') WHERE id=?",
            (merged["active_version"]["id"],),
        )
    refused = step(client, merged)
    assert refused.status_code == 409
    assert "cannot be undone safely" in refused.json()["detail"]


def test_split_delete_undo_undo_never_offers_duplicate_deleted_text(client, row):
    ids = [p["id"] for p in row["segments"]]
    split = structure(client, row, ids[1], "split-v2", cursor=5)
    right = split["segments"][2]["id"]
    deleted = structure(client, split, right, "delete")
    assert [d["segment_id"] for d in offered(client, deleted)["deleted_segments"]] == [right]
    current = ok(step(client, deleted))
    current = ok(step(client, current))
    assert content(current) == content(row)
    available = offered(client, current)
    assert available["deleted_segments"] == []
    assert available["split_segment_ids"] == [] and available["segment_ids"] == []
    # Redo re-applies the split exactly; the proven per-passage Undo split is offered again.
    current = ok(step(client, current, "redo"))
    assert offered(client, current)["split_segment_ids"] == [ids[1]]


def test_working_follows_an_undo_of_the_working_head(client, row):
    store = client.app.state.store
    current = edit(client, row, 0, "Alpha edited")
    assert current["result_roles"]["working"]["version_id"] == current["active_version"]["id"]
    undone = ok(step(client, current))
    assert undone["result_roles"]["working"]["version_id"] == undone["active_version"]["id"]
    events = store.all("SELECT action FROM result_role_events ORDER BY sequence")
    assert events[-1]["action"] == "working_auto_edit"


@pytest.mark.parametrize("decision", ["promote", "keep", "stale"])
def test_undo_off_the_working_head_needs_a_fresh_working_choice(client, row, decision):
    store = client.app.state.store
    current = edit(client, row, 0, "Alpha edited")
    assert current["result_roles"]["working"]["version_id"] == current["active_version"]["id"]
    current = edit(client, current, 1, "Beta edited")
    mark(store, row["id"], row["active_version"]["id"])
    current = store.recording(row["id"])
    pinned = row["active_version"]["id"]
    assert current["result_roles"]["working"]["version_id"] == pinned
    selected = choice(current, decision == "promote")
    if decision == "stale":
        current = mark(store, row["id"], None)
    response = step(client, current, working_choice=selected)
    if decision == "stale":
        assert response.status_code == 409
        assert store.recording(row["id"])["revision"] == current["revision"]
        return
    saved = ok(response)
    working = saved["result_roles"]["working"]["version_id"]
    if decision == "promote":
        assert working == saved["active_version"]["id"]
    else:
        assert working == pinned


def test_checked_marks_return_with_exact_undo_and_go_stale_after_redo(client, row):
    scene = (client, client.app.state.store, row["id"], row["active_transcript_id"])
    assert review_mark(scene).status_code == 200
    before = review_read(scene)
    current = edit(client, row, 0, "Alpha edited after checking")
    assert review_read(scene)["passages"][0]["status"] == "needs_review"
    current = ok(step(client, current))
    assert review_read(scene)["passages"] == before["passages"]
    ok(step(client, current, "redo"))
    assert review_read(scene)["passages"][0]["stale"] is True
    assert len(client.app.state.store.all("SELECT * FROM passage_reviews")) == 1


def test_queued_word_timing_for_undone_text_never_applies(client, row):
    store = client.app.state.store
    current = edit(client, row, 0, "Alpha edited")
    segment = current["segments"][0]["id"]
    job = store.enqueue_enhancement(
        row["id"],
        row["active_transcript_id"],
        current["revision"],
        {"realign": True, "segment_id": segment, "enhancement": {"execution": "local"}},
    )
    params = job["params"]
    params["realign_output"] = {
        "passages": {
            segment: {
                "words": [
                    {"word": "Alpha", "start": 0.1, "end": 0.6, "char_start": 0, "char_end": 5},
                    {"word": "edited", "start": 0.7, "end": 1.4, "char_start": 6, "char_end": 12},
                ],
                "word_timing": True,
            }
        }
    }
    store.update_job(job["id"], params=params, state="completed")
    current = ok(step(client, current))
    # The timing writer checks passage text against the live result (ADR 0052).
    with pytest.raises(ValueError) as refused:
        store.apply_local_timing(
            row["id"], row["active_transcript_id"], job["id"], current["revision"]
        )
    assert str(refused.value) == (
        "The passages changed again; their newer text and timing were preserved."
    )
    assert store.recording(row["id"])["segments"] == current["segments"]


def test_change_rows_join_the_shared_vocabulary_report_and_sqlite_backup(client, row, tmp_path):
    import sqlite3

    assert {"change_undo", "change_redo"} <= EDIT_ACTIONS <= research_reports.ACTIONS
    store = client.app.state.store
    current = ok(step(client, edit(client, row, 0, "Alpha edited")))
    current = ok(step(client, current, "redo"))
    copy = tmp_path / "backup.sqlite3"
    with store.connection() as db, sqlite3.connect(copy) as target:
        db.backup(target)
    with sqlite3.connect(copy) as db:
        restored = db.execute(
            "SELECT action,payload_sha256 FROM result_versions WHERE result_id=? ORDER BY sequence",
            (row["active_transcript_id"],),
        ).fetchall()
    assert [r[0] for r in restored][-2:] == ["change_undo", "change_redo"]
    assert restored[-2][1] == restored[0][1]
    assert restored[-1][1] == encode_payload(current["segments"], current["speakers"])[1]


def test_mvp_profile_offers_undo_since_the_v2_decision(tmp_path):
    from conftest import MemoryCredentials
    from fastapi.testclient import TestClient

    from cbs_transcriber.app import create_app

    # The user chose on 20 November that MVP v2 shows Undo last change, History and Branches.
    app = create_app(tmp_path, start_worker=False, credentials=MemoryCredentials(), release="mvp")
    with TestClient(app, headers={"X-CBS-Request": "1"}) as mvp:
        store = mvp.app.state.store
        project = store.create_project("Synthetic MVP undo")
        recording = store.create_recording(project["id"], "synthetic.wav")
        store.add_result(
            recording["id"],
            normalize_segments([{"text": TEXTS[0], "speaker": "A", "start": 0, "end": 1}]),
            {},
            "First",
            {},
        )
        current = edit(mvp, store.recording(recording["id"]), 0, "Alpha edited")
        assert "undo" in offered(mvp, current)
        response = step(mvp, current)
        assert response.status_code == 200, response.text
        assert store.recording(recording["id"])["segments"][0]["text"] == TEXTS[0]


def test_a_profile_hiding_undo_hides_steps_and_refuses_new_undo(tmp_path, monkeypatch):
    from conftest import MemoryCredentials
    from fastapi.testclient import TestClient

    from cbs_transcriber.app import create_app

    monkeypatch.setitem(release_profile.PROFILES, "mvp", frozenset(release_profile.FEATURES))
    app = create_app(tmp_path, start_worker=False, credentials=MemoryCredentials(), release="mvp")
    with TestClient(app, headers={"X-CBS-Request": "1"}) as mvp:
        store = mvp.app.state.store
        project = store.create_project("Synthetic MVP undo")
        recording = store.create_recording(project["id"], "synthetic.wav")
        store.add_result(
            recording["id"],
            normalize_segments([{"text": TEXTS[0], "speaker": "A", "start": 0, "end": 1}]),
            {},
            "First",
            {},
        )
        current = edit(mvp, store.recording(recording["id"]), 0, "Alpha edited")
        hidden = offered(mvp, current)
        assert "undo" not in hidden and "redo" not in hidden
        assert hidden["deleted_segments"] == []
        refused = step(mvp, current, version_id=current["active_version"]["id"])
        assert refused.status_code == 403
        assert refused.json()["code"] == release_profile.CODE
        assert refused.json()["feature"] == "undo_last_change"
        assert store.recording(recording["id"])["segments"][0]["text"] == "Alpha edited"

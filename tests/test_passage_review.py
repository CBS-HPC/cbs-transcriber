"""Manual passage review progress has independent persistence and concurrency fences."""

import sqlite3

import pytest

from cbs_transcriber import passage_review
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def review_scene(client):
    store = client.app.state.store
    project = store.create_project("Synthetic review")
    recording = store.create_recording(project["id"], "synthetic.wav")
    result = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": "First passage", "start": 0, "end": 2, "speaker": "A"},
                {"text": "Second passage", "start": 2, "end": 4, "speaker": "A"},
            ]
        ),
        {"A": "Speaker"},
        "Synthetic",
        {},
        auto_first=True,
    )
    return client, store, recording["id"], result["id"]


def read(scene, **params):
    client, _, rid, result_id = scene
    response = client.get(
        f"/api/recordings/{rid}/passage-reviews", params={"result_id": result_id, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def mark(scene, status="checked", index=0, loaded=None, **changes):
    client, _, rid, result_id = scene
    loaded = loaded or read(scene)
    item = loaded["passages"][index]
    return client.patch(
        f"/api/recordings/{rid}/segments/{item['segment_id']}/review",
        json={
            "result_id": result_id,
            "revision": loaded["revision"],
            "status": status,
            "review_revision": item["review_revision"],
            "passage_sha256": item["passage_sha256"],
            **changes,
        },
    )


def test_marks_have_independent_revision_and_do_not_write_transcript_or_working(review_scene):
    _, store, rid, result_id = review_scene
    before = store.recording(rid)
    history = store.result_versions(rid, result_id)
    events = store.all("SELECT * FROM result_role_events")
    snapshots = {p: p.read_bytes() for p in store.root.rglob("project.json")}
    original = read(review_scene)
    assert original["counts"] == {"checked": 0, "needs_review": 0, "not_checked": 2, "total": 2}
    saved = mark(review_scene).json()
    assert saved["passage"]["review_revision"] == 1
    assert saved["counts"]["checked"] == 1
    assert mark(review_scene, "needs_review", index=1).status_code == 200
    after = store.recording(rid)
    assert after == before
    assert store.result_versions(rid, result_id) == history
    assert store.all("SELECT * FROM result_role_events") == events
    assert {p: p.read_bytes() for p in snapshots} == snapshots
    assert read(review_scene)["counts"] == {
        "checked": 1,
        "needs_review": 1,
        "not_checked": 0,
        "total": 2,
    }


def test_reset_is_a_revisioned_row_and_stale_review_does_not_overwrite(review_scene):
    original = read(review_scene)
    assert mark(review_scene, loaded=original).status_code == 200
    assert mark(review_scene, "needs_review", loaded=original).status_code == 409
    assert mark(review_scene, "not_checked").status_code == 200
    reset = read(review_scene)["passages"][0]
    assert reset["status"] == "not_checked" and reset["review_revision"] == 2
    assert mark(review_scene, loaded=original).status_code == 409


def test_edit_invalidates_checked_and_stale_requests_preserve_new_text(review_scene):
    client, store, rid, _ = review_scene
    assert mark(review_scene).status_code == 200
    original = read(review_scene)
    edited = client.patch(
        f"/api/recordings/{rid}/segments/0",
        json={
            "revision": original["revision"],
            "mode": "free",
            "text": "Changed passage",
        },
    )
    assert edited.status_code == 200, edited.text
    current = read(review_scene)
    assert current["passages"][0]["status"] == "needs_review"
    assert current["passages"][0]["stale"]
    assert current["passages"][0]["review_revision"] == 1
    assert mark(review_scene, loaded=original).status_code == 409
    assert (
        mark(review_scene, passage_sha256=original["passages"][0]["passage_sha256"]).status_code
        == 409
    )
    assert mark(review_scene).status_code == 200
    assert not read(review_scene)["passages"][0]["stale"]
    assert store.recording(rid)["segments"][0]["text"] == "Changed passage"


def test_exact_merge_undo_restores_the_prior_checked_hash(review_scene):
    client, store, rid, result_id = review_scene
    assert mark(review_scene).status_code == 200
    assert mark(review_scene, index=1).status_code == 200
    before = read(review_scene)
    merged = client.post(
        f"/api/recordings/{rid}/segments/0/merge",
        json={
            "result_id": result_id,
            "revision": before["revision"],
            "direction": "down",
        },
    )
    assert merged.status_code == 200, merged.text
    assert read(review_scene)["counts"] == {
        "checked": 0,
        "needs_review": 1,
        "not_checked": 0,
        "total": 1,
    }
    undone = client.post(
        f"/api/recordings/{rid}/segments/0/undo-merge",
        json={
            "result_id": result_id,
            "revision": merged.json()["revision"],
        },
    )
    assert undone.status_code == 200, undone.text
    after = read(review_scene)
    assert after["counts"]["checked"] == 2
    assert after["passages"] == before["passages"]
    assert len(store.all("SELECT * FROM passage_reviews")) == 2


def test_split_retained_half_needs_review_and_new_half_is_not_checked(review_scene):
    client, _, rid, result_id = review_scene
    assert mark(review_scene).status_code == 200
    before = read(review_scene)
    split = client.post(
        f"/api/recordings/{rid}/segments/0/split",
        json={
            "result_id": result_id,
            "revision": before["revision"],
            "cursor": len("First "),
            "split_time": 1.0,
        },
    )
    assert split.status_code == 200, split.text
    current = read(review_scene)
    assert current["counts"] == {
        "checked": 0,
        "needs_review": 1,
        "not_checked": 2,
        "total": 3,
    }
    assert current["passages"][0]["status"] == "needs_review"
    assert current["passages"][1]["status"] == "not_checked"
    assert current["passages"][1]["review_revision"] == 0


def test_new_result_has_no_inherited_marks_and_trash_keeps_metadata(review_scene):
    client, store, rid, result_id = review_scene
    assert mark(review_scene).status_code == 200
    recording = store.recording(rid)
    new = store.add_result(rid, recording["segments"], recording["speakers"], "Another", {})
    store.activate_result(rid, new["id"], store.recording(rid)["revision"])
    current = (client, store, rid, new["id"])
    assert read(current)["counts"]["not_checked"] == 2
    assert (
        client.get(
            f"/api/recordings/{rid}/passage-reviews", params={"result_id": result_id}
        ).status_code
        == 409
    )
    selected = store.activate_result(rid, result_id, store.recording(rid)["revision"])
    store.set_result_trashed(rid, result_id, selected["revision"], trashed=True)
    assert (
        client.get(
            f"/api/recordings/{rid}/passage-reviews", params={"result_id": result_id}
        ).status_code
        == 409
    )
    assert len(store.all("SELECT * FROM passage_reviews WHERE result_id=?", (result_id,))) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("review_revision", True),
        ("revision", -1),
        ("status", "ok"),
        ("passage_sha256", "bad"),
        ("unknown", "field"),
    ],
)
def test_invalid_review_fields_are_rejected_without_writes(review_scene, field, value):
    _, store, _, _ = review_scene
    assert mark(review_scene, **{field: value}).status_code == 422
    assert store.all("SELECT * FROM passage_reviews") == []


def test_reopen_and_sqlite_backup_preserve_marks(review_scene, tmp_path):
    _, store, rid, result_id = review_scene
    assert mark(review_scene).status_code == 200
    before = passage_review.read(store, rid, result_id)
    reopened = Store(store.root)
    assert passage_review.read(reopened, rid, result_id) == before
    copy = tmp_path / "backup" / "library.sqlite3"
    copy.parent.mkdir()
    with store.connection() as db, sqlite3.connect(copy) as target:
        db.backup(target)
    assert passage_review.read(Store(copy.parent), rid, result_id) == before


def test_invalid_saved_review_metadata_fails_closed(review_scene):
    client, store, rid, result_id = review_scene
    assert mark(review_scene).status_code == 200
    before = store.recording(rid)
    with store.connection() as db:
        db.execute("UPDATE passage_reviews SET passage_sha256=?", ("A" * 64,))
    response = client.get(f"/api/recordings/{rid}/passage-reviews", params={"result_id": result_id})
    assert response.status_code == 400
    assert "review marks are invalid" in response.json()["detail"]
    assert store.recording(rid) == before


def test_retained_marks_of_removed_passages_do_not_exhaust_current_read_budget(
    review_scene, monkeypatch
):
    _, store, _, result_id = review_scene
    monkeypatch.setattr(passage_review, "MAX_PASSAGES", 2)
    with store.connection() as db:
        for segment_id in ("former-1", "former-2", "former-3"):
            db.execute(
                "INSERT INTO passage_reviews VALUES (?,?,?,?,?,?)",
                (result_id, segment_id, "0" * 64, "checked", 1, "2026-11-09T00:00:00+00:00"),
            )
    assert read(review_scene)["counts"]["not_checked"] == 2
    assert mark(review_scene).status_code == 200
    assert len(store.all("SELECT * FROM passage_reviews")) == 4


def test_schema18_upgrade_backs_up_and_preserves_transcripts(tmp_path):
    store = Store(tmp_path / "library")
    project = store.create_project("Before migration")
    with store.connection() as db:
        db.execute("DROP TABLE passage_reviews")
        db.execute("PRAGMA user_version=18")
    upgraded = Store(store.root)
    assert (
        upgraded.one("SELECT name FROM projects WHERE id=?", (project["id"],))["name"]
        == project["name"]
    )
    assert upgraded.all("SELECT * FROM passage_reviews") == []
    backups = list((store.root / "backups").glob("schema-18-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 18
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"

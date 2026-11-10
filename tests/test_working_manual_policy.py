"""Manual edit decisions follow or preserve exact immutable Working versions."""

import sqlite3

import pytest
from test_result_conflicts import branch as conflict_branch  # noqa: F401
from test_result_merges import branch as merge_branch  # noqa: F401
from test_wording_adoptions import adoption as adoption

from cbs_transcriber import result_conflicts, result_roles, working_manual
from cbs_transcriber.transcripts import normalize_segments


def setup_recording(client):
    store = client.app.state.store
    project = store.create_project("Synthetic Working edits")
    recording = store.create_recording(project["id"], "synthetic.wav")
    result = store.add_result(
        recording["id"], normalize_segments([{"text": "Before", "speaker": "A"}]), {}, "One", {}
    )
    return store, recording["id"], result["id"]


def mark(store, rid, version_id):
    row = store.recording(rid)
    roles = row["result_roles"]["revision"]
    preview = result_roles.preview(store, rid, version_id, row["revision"], roles)
    return result_roles.apply(
        store, rid, version_id, row["revision"], roles, preview["fingerprint"]
    )


def edit(client, rid, row, text, choice=None):
    return client.patch(
        f"/api/recordings/{rid}/segments/0",
        json={
            "revision": row["revision"],
            "mode": "free",
            "text": text,
            **({"working_choice": choice} if choice is not None else {}),
        },
    )


def choice(row, make_working):
    return {
        "version_id": row["active_version"]["id"],
        "role_revision": row["result_roles"]["revision"],
        "make_working": make_working,
    }


@pytest.mark.parametrize("decision", ["promote", "keep", "stale", "working", "working_false"])
def test_undo_merge_fences_working_choice_atomically(client, decision):
    store = client.app.state.store
    project = store.create_project("Synthetic undo Working decision")
    recording = store.create_recording(project["id"], "synthetic.wav")
    rid = recording["id"]
    segments = normalize_segments(
        [
            {"text": "First", "start": 0, "end": 1, "speaker": "A"},
            {"text": "Second", "start": 1, "end": 2, "speaker": "A"},
        ]
    )
    store.add_result(rid, segments, {}, "First", {})
    pinned = mark(store, rid, store.recording(rid)["active_version"]["id"])
    alternative = store.add_result(rid, segments, {}, "Alternative", {})
    row = store.activate_result(rid, alternative["id"], pinned["revision"])
    merged = client.post(
        f"/api/recordings/{rid}/segments/0/merge",
        json={"revision": row["revision"], "result_id": alternative["id"], "direction": "down"},
    )
    assert merged.status_code == 200, merged.text
    row = merged.json()
    if decision in {"working", "working_false"}:
        row = mark(store, rid, row["active_version"]["id"])
    before = row
    selected = choice(before, decision not in {"keep", "working_false"})
    if decision == "stale":
        mark(store, rid, None)
        before = store.recording(rid)
    versions = store.all("SELECT id FROM result_versions WHERE result_id=?", (alternative["id"],))
    events = store.all("SELECT * FROM result_role_events WHERE recording_id=?", (rid,))
    response = client.post(
        f"/api/recordings/{rid}/segments/0/undo-merge",
        json={
            "revision": row["revision"],
            "result_id": alternative["id"],
            "working_choice": selected,
        },
    )
    assert response.status_code == (409 if decision in {"stale", "working_false"} else 200), (
        response.text
    )
    after = store.recording(rid)
    if decision in {"stale", "working_false"}:
        assert after["segments"] == before["segments"]
        assert after["result_roles"] == before["result_roles"]
        assert after["active_version"] == before["active_version"]
        assert (
            store.all("SELECT id FROM result_versions WHERE result_id=?", (alternative["id"],))
            == versions
        )
        assert store.all("SELECT * FROM result_role_events WHERE recording_id=?", (rid,)) == events
    else:
        assert after["segments"] == segments
        expected = (
            pinned["active_version"]["id"] if decision == "keep" else after["active_version"]["id"]
        )
        assert after["result_roles"]["working"]["version_id"] == expected


def test_edit_of_working_head_follows_atomically_and_keeps_history(client):
    store, rid, result_id = setup_recording(client)
    before = store.recording(rid)
    before = mark(store, rid, before["active_version"]["id"])
    response = edit(client, rid, before, "After", choice(before, True))
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["result_roles"]["working"]["version_id"] == after["active_version"]["id"]
    assert after["active_version"]["id"] != before["active_version"]["id"]
    assert (
        store.one("SELECT count(*) AS n FROM result_versions WHERE result_id=?", (result_id,))["n"]
        == 2
    )
    event = store.one(
        "SELECT action,actor_kind FROM result_role_events WHERE recording_id=? "
        "ORDER BY sequence DESC LIMIT 1",
        (rid,),
    )
    assert dict(event) == {"action": "working_auto_edit", "actor_kind": "system"}


@pytest.mark.parametrize("make_working", [False, True])
def test_non_working_edit_obeys_explicit_choice(client, make_working):
    store, rid, _ = setup_recording(client)
    pinned = mark(store, rid, store.recording(rid)["active_version"]["id"])
    second = store.add_result(
        rid, normalize_segments([{"text": "Alternative", "speaker": "A"}]), {}, "Two", {}
    )
    before = store.activate_result(rid, second["id"], pinned["revision"])
    response = edit(client, rid, before, "Alternative edited", choice(before, make_working))
    assert response.status_code == 200, response.text
    after = response.json()
    expected = after["active_version"]["id"] if make_working else pinned["active_version"]["id"]
    assert after["result_roles"]["working"]["version_id"] == expected
    assert after["result_roles"]["revision"] == pinned["result_roles"]["revision"] + 1


def test_stale_role_choice_rejects_entire_edit(client):
    store, rid, result_id = setup_recording(client)
    first = mark(store, rid, store.recording(rid)["active_version"]["id"])
    stale = choice(first, True)
    mark(store, rid, None)
    before = store.recording(rid)
    versions = store.all("SELECT id FROM result_versions WHERE result_id=?", (result_id,))
    response = edit(client, rid, first, "Must not save", stale)
    assert response.status_code == 409
    after = store.recording(rid)
    assert after["segments"] == before["segments"]
    assert after["result_roles"] == before["result_roles"]
    assert store.all("SELECT id FROM result_versions WHERE result_id=?", (result_id,)) == versions


def test_legacy_omitted_choice_cannot_promote_non_working_result(client):
    store, rid, _ = setup_recording(client)
    pinned = mark(store, rid, store.recording(rid)["active_version"]["id"])
    second = store.add_result(rid, normalize_segments([{"text": "Other"}]), {}, "Two", {})
    before = store.activate_result(rid, second["id"], pinned["revision"])
    response = edit(client, rid, before, "Other edited")
    assert response.status_code == 200, response.text
    assert response.json()["result_roles"] == pinned["result_roles"]


def test_edit_while_an_older_version_of_same_result_is_working(client):
    store, rid, _ = setup_recording(client)
    first = mark(store, rid, store.recording(rid)["active_version"]["id"])
    updated = edit(client, rid, first, "Second", choice(first, True)).json()
    historical = mark(store, rid, first["active_version"]["id"])
    assert historical["active_version"]["id"] == updated["active_version"]["id"]
    response = edit(client, rid, historical, "Third", choice(historical, False))
    assert response.status_code == 200, response.text
    assert response.json()["result_roles"]["working"] == historical["result_roles"]["working"]
    assert response.json()["result_roles"]["revision"] == historical["result_roles"]["revision"] + 1


def test_role_write_failure_rolls_back_the_entire_manual_edit(client):
    store, rid, result_id = setup_recording(client)
    before = mark(store, rid, store.recording(rid)["active_version"]["id"])
    with store.connection() as db:
        db.execute(
            "CREATE TRIGGER reject_auto_role BEFORE INSERT ON result_role_events "
            "BEGIN SELECT RAISE(ABORT,'synthetic role failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.save_transcript(
            rid,
            normalize_segments([{"text": "Must roll back", "speaker": "A"}]),
            {},
            before["revision"],
            working_choice=choice(before, True),
        )
    after = store.recording(rid)
    assert after["segments"] == before["segments"]
    assert after["result_roles"] == before["result_roles"]
    assert (
        store.one("SELECT count(*) AS n FROM result_versions WHERE result_id=?", (result_id,))["n"]
        == 1
    )


def test_wording_adoption_carries_working_choice_in_same_transaction(client, adoption):
    store = adoption["store"]
    rid = adoption["recording"]["id"]
    before = store.recording(rid)
    before = mark(store, rid, before["active_version"]["id"])
    plan = client.post(adoption["url"] + "/wording-preview", json=adoption["request"])
    assert plan.status_code == 200, plan.text
    saved = client.post(
        adoption["url"] + "/wording-adopt",
        json={
            **adoption["request"],
            "fingerprint": plan.json()["fingerprint"],
            "working_choice": choice(before, True),
        },
    )
    assert saved.status_code == 200, saved.text
    row = saved.json()["recording"]
    assert row["result_roles"]["working"]["version_id"] == row["active_version"]["id"]


def test_wording_adoption_rejects_stale_working_choice(client, adoption):
    store = adoption["store"]
    rid = adoption["recording"]["id"]
    before = mark(store, rid, store.recording(rid)["active_version"]["id"])
    plan = client.post(adoption["url"] + "/wording-preview", json=adoption["request"])
    assert plan.status_code == 200, plan.text
    mark(store, rid, None)
    snapshot = store.recording(rid)
    response = client.post(
        adoption["url"] + "/wording-adopt",
        json={
            **adoption["request"],
            "fingerprint": plan.json()["fingerprint"],
            "working_choice": choice(before, True),
        },
    )
    assert response.status_code == 409, response.text
    after = store.recording(rid)
    assert after["segments"] == snapshot["segments"]
    assert after["result_roles"] == snapshot["result_roles"]


def test_current_working_head_rejects_contradictory_choice_atomically(client):
    store, rid, result_id = setup_recording(client)
    before = mark(store, rid, store.recording(rid)["active_version"]["id"])
    versions = store.all("SELECT id FROM result_versions WHERE result_id=?", (result_id,))
    events = store.all("SELECT * FROM result_role_events WHERE recording_id=?", (rid,))
    response = edit(client, rid, before, "After", choice(before, False))
    assert response.status_code == 409, response.text
    after = store.recording(rid)
    assert after["segments"] == before["segments"]
    assert after["result_roles"] == before["result_roles"]
    assert after["active_version"] == before["active_version"]
    assert store.all("SELECT id FROM result_versions WHERE result_id=?", (result_id,)) == versions
    assert store.all("SELECT * FROM result_role_events WHERE recording_id=?", (rid,)) == events


@pytest.mark.parametrize(
    "damage",
    [
        "actor",
        "prior_actor",
        "link",
        "hash",
        "time",
        "action_nul",
        "actor_nul",
        "time_nul",
        "oversized_time",
    ],
)
def test_corrupt_recent_working_history_blocks_manual_write_atomically(client, damage):
    store, rid, result_id = setup_recording(client)
    first = mark(store, rid, store.recording(rid)["active_version"]["id"])
    before = mark(store, rid, first["active_version"]["id"])
    statements = {
        "actor": "UPDATE result_role_events SET actor_kind='system' "
        "WHERE recording_id=? AND sequence=2",
        "prior_actor": "UPDATE result_role_events SET actor_kind='system' "
        "WHERE recording_id=? AND sequence=1",
        "link": "UPDATE result_role_events SET previous_version_id=NULL,"
        "previous_payload_sha256=NULL "
        "WHERE recording_id=? AND sequence=2",
        "hash": "UPDATE result_role_events SET previous_payload_sha256='"
        + "0" * 64
        + "' WHERE recording_id=? AND sequence=2",
        "time": "UPDATE result_role_events SET observed_at='now' "
        "WHERE recording_id=? AND sequence=2",
    }
    if damage in {"action_nul", "actor_nul", "time_nul", "oversized_time"}:
        column, value = {
            "action_nul": ("action", "working_kept\x00extra"),
            "actor_nul": ("actor_kind", "local_user\x00extra"),
            "time_nul": ("observed_at", "2026-11-03\x0012:00:00+00:00"),
            "oversized_time": ("observed_at", "x" * 100_000),
        }[damage]
        with store.connection() as db:
            db.execute("PRAGMA ignore_check_constraints=ON")
            db.execute(
                f"UPDATE result_role_events SET {column}=? WHERE recording_id=? AND sequence=2",
                (value, rid),
            )
    else:
        store.execute(statements[damage], (rid,))
    versions = store.all("SELECT id FROM result_versions WHERE result_id=?", (result_id,))
    events = store.all("SELECT * FROM result_role_events WHERE recording_id=?", (rid,))
    response = edit(client, rid, before, "Must not save", choice(before, True))
    assert response.status_code == 400, response.text
    after = store.recording(rid)
    assert after["segments"] == before["segments"]
    assert after["result_roles"] == before["result_roles"]
    assert after["active_version"] == before["active_version"]
    assert store.all("SELECT id FROM result_versions WHERE result_id=?", (result_id,)) == versions
    assert store.all("SELECT * FROM result_role_events WHERE recording_id=?", (rid,)) == events


def test_corrupt_role_event_blocks_automatic_successor(client):
    store, rid, _ = setup_recording(client)
    current = mark(store, rid, store.recording(rid)["active_version"]["id"])
    successor = store.add_result(
        rid, normalize_segments([{"text": "Successor"}]), {}, "Successor", {}
    )
    target = store.result_versions(rid, successor["id"])[0]["id"]
    expected = current["result_roles"]
    store.execute("UPDATE result_role_events SET actor_kind='system' WHERE recording_id=?", (rid,))
    events = store.all("SELECT * FROM result_role_events WHERE recording_id=?", (rid,))
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="history chain"):
            result_roles.move_automatic(db, store, rid, target, expected, "working_auto_successor")
    assert store.all("SELECT * FROM result_role_events WHERE recording_id=?", (rid,)) == events
    assert store.recording(rid)["result_roles"] == expected


def test_first_manual_result_is_working_without_reviving_a_trashed_result(client):
    store = client.app.state.store
    project = store.create_project("Synthetic manual creation")
    first = store.create_recording(project["id"], "first.wav")
    first_saved = store.save_transcript(
        first["id"], normalize_segments([{"text": "First"}]), {}, first["revision"]
    )
    assert (
        first_saved["result_roles"]["working"]["version_id"] == first_saved["active_version"]["id"]
    )
    event = store.one(
        "SELECT action,actor_kind FROM result_role_events WHERE recording_id=?", (first["id"],)
    )
    assert dict(event) == {"action": "working_auto_first", "actor_kind": "system"}

    existing = store.create_recording(project["id"], "old.wav")
    result = store.add_result(existing["id"], normalize_segments([{"text": "Old"}]), {}, "Old", {})
    old = store.recording(existing["id"])
    store.set_result_trashed(existing["id"], result["id"], old["revision"], trashed=True)
    old = store.recording(existing["id"])
    saved = store.save_transcript(
        existing["id"], normalize_segments([{"text": "New"}]), {}, old["revision"]
    )
    assert saved["result_roles"]["working"] is None


@pytest.mark.parametrize("operation", ["merge", "split", "delete", "speaker", "rename"])
@pytest.mark.parametrize("stale", [False, True])
def test_structural_and_speaker_edits_fence_the_choice(client, operation, stale):
    store = client.app.state.store
    project = store.create_project("Synthetic Working mutations")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [
            {"text": "First text", "start": 0, "end": 2, "speaker": "A"},
            {"text": "Second text", "start": 2, "end": 4, "speaker": "A"},
        ]
    )
    store.add_result(recording["id"], segments, {"B": "Other"}, "Initial", {})
    pinned = mark(store, recording["id"], store.recording(recording["id"])["active_version"]["id"])
    alternative = store.add_result(recording["id"], segments, {"B": "Other"}, "Other", {})
    row = store.activate_result(recording["id"], alternative["id"], pinned["revision"])
    request_choice = choice(row, True)
    if stale:
        mark(store, recording["id"], None)
    base = f"/api/recordings/{recording['id']}"
    body = {"revision": row["revision"], "working_choice": request_choice}
    route, extra, method = {
        "merge": (
            "/segments/0/merge",
            {"result_id": alternative["id"], "direction": "down"},
            "post",
        ),
        "split": (
            "/segments/0/split",
            {"result_id": alternative["id"], "cursor": 5, "split_time": 1.0},
            "post",
        ),
        "delete": ("/segments/0/delete", {"result_id": alternative["id"]}, "post"),
        "speaker": ("/segments/0/speaker", {"speaker_id": "B"}, "patch"),
        "rename": ("/speakers", {"speaker": "A", "name": "Named"}, "patch"),
    }[operation]
    before = store.recording(recording["id"])
    response = getattr(client, method)(base + route, json={**body, **extra})
    assert response.status_code == (409 if stale else 200), response.text
    after = store.recording(recording["id"])
    if stale:
        assert after["segments"] == before["segments"]
        assert after["speakers"] == before["speakers"]
        assert after["result_roles"] == before["result_roles"]
    else:
        assert after["result_roles"]["working"]["version_id"] == after["active_version"]["id"]


@pytest.mark.parametrize("stale", [False, True])
def test_restoring_old_version_fences_working_choice(client, stale):
    store, rid, result_id = setup_recording(client)
    first = mark(store, rid, store.recording(rid)["active_version"]["id"])
    response = edit(client, rid, first, "Newer", choice(first, True))
    assert response.status_code == 200, response.text
    current = mark(store, rid, first["active_version"]["id"])
    decision = choice(current, True)
    if stale:
        mark(store, rid, None)
    before = store.recording(rid)
    response = client.post(
        f"/api/recordings/{rid}/transcripts/{result_id}/versions/"
        f"{first['active_version']['id']}/restore",
        json={"revision": current["revision"], "working_choice": decision},
    )
    assert response.status_code == (409 if stale else 200), response.text
    after = store.recording(rid)
    if stale:
        assert after["segments"] == before["segments"]
        assert after["result_roles"] == before["result_roles"]
    else:
        assert after["segments"][0]["text"] == "Before"
        assert after["result_roles"]["working"]["version_id"] == after["active_version"]["id"]


@pytest.mark.parametrize("stale", [False, True])
def test_result_merge_fences_working_choice(client, merge_branch, stale):  # noqa: F811
    store, row = merge_branch["store"], merge_branch["recording"]
    before = mark(store, row["id"], store.recording(row["id"])["active_version"]["id"])
    preview = client.post(merge_branch["url"] + "/merge-preview", json=merge_branch["request"])
    assert preview.status_code == 200, preview.text
    decision = choice(before, True)
    if stale:
        mark(store, row["id"], None)
    snapshot = store.recording(row["id"])
    response = client.post(
        merge_branch["url"] + "/merge",
        json={
            **merge_branch["request"],
            "fingerprint": preview.json()["fingerprint"],
            "working_choice": decision,
        },
    )
    assert response.status_code == (409 if stale else 200), response.text
    after = store.recording(row["id"])
    if stale:
        assert after["segments"] == snapshot["segments"]
        assert after["result_roles"] == snapshot["result_roles"]
    else:
        assert after["result_roles"]["working"]["version_id"] == after["active_version"]["id"]


@pytest.mark.parametrize("stale", [False, True])
def test_conflict_review_fences_working_choice(conflict_branch, stale):  # noqa: F811
    store, row = conflict_branch["store"], conflict_branch["recording"]
    before = mark(store, row["id"], store.recording(row["id"])["active_version"]["id"])
    decisions = [{"passage_id": "0", "group": "wording", "choice": "incoming"}]
    args = (
        store,
        row["id"],
        conflict_branch["ours"]["id"],
        conflict_branch["incoming"]["id"],
        row["revision"],
        decisions,
    )
    preview = result_conflicts.preview(*args)
    decision = choice(before, True)
    if stale:
        mark(store, row["id"], None)
    snapshot = store.recording(row["id"])
    if stale:
        with pytest.raises(working_manual.Conflict):
            result_conflicts.apply(*args, preview["fingerprint"], working_choice=decision)
        after = store.recording(row["id"])
        assert after["segments"] == snapshot["segments"]
        assert after["result_roles"] == snapshot["result_roles"]
    else:
        result_conflicts.apply(*args, preview["fingerprint"], working_choice=decision)
        after = store.recording(row["id"])
        assert after["result_roles"]["working"]["version_id"] == after["active_version"]["id"]


@pytest.mark.parametrize("assigned", [False, True])
def test_explicit_keep_fences_older_automatic_job(client, assigned):
    store, rid, _ = setup_recording(client)
    original = store.recording(rid)
    if assigned:
        original = mark(store, rid, original["active_version"]["id"])
    elif original["result_roles"]["working"] is not None:
        original = mark(store, rid, None)
    alternative = store.add_result(
        rid, normalize_segments([{"text": "Alternative"}]), {}, "Other", {}
    )
    before = store.activate_result(rid, alternative["id"], original["revision"])
    with store.connection() as db:
        frozen = result_roles.read(db, store, rid)
    response = edit(client, rid, before, "Edited", choice(before, False))
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["result_roles"]["working"] == before["result_roles"]["working"]
    assert after["result_roles"]["revision"] == before["result_roles"]["revision"] + 1
    event = store.one(
        "SELECT action,actor_kind,previous_version_id,next_version_id FROM result_role_events "
        "WHERE recording_id=? ORDER BY sequence DESC LIMIT 1",
        (rid,),
    )
    assert event["action"] == "working_kept"
    assert event["actor_kind"] == "local_user"
    assert event["previous_version_id"] == event["next_version_id"]
    with store.connection() as db:
        assert not result_roles.move_automatic(
            db, store, rid, after["active_version"]["id"], frozen, "working_auto_successor"
        )
    assert store.recording(rid)["result_roles"] == after["result_roles"]


def test_keep_event_failure_rolls_back_edit(client):
    store, rid, _ = setup_recording(client)
    before = store.recording(rid)
    if before["result_roles"]["working"] is not None:
        before = mark(store, rid, None)
    with store.connection() as db:
        db.execute(
            "CREATE TRIGGER reject_keep BEFORE INSERT ON result_role_events "
            "BEGIN SELECT RAISE(ABORT,'synthetic keep failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.save_transcript(
            rid,
            normalize_segments([{"text": "Must roll back", "speaker": "A"}]),
            {},
            before["revision"],
            working_choice=choice(before, False),
        )
    after = store.recording(rid)
    assert after["segments"] == before["segments"]
    assert after["active_version"] == before["active_version"]
    assert after["result_roles"] == before["result_roles"]

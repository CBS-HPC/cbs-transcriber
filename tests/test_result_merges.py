"""Merge a real frozen enhancement lineage without replacing reviewed wording/history."""

import json
import sqlite3
from contextlib import contextmanager
from copy import deepcopy

import pytest

from cbs_transcriber import result_merges
from cbs_transcriber.transcripts import edit_segment, normalize_segments


@pytest.fixture
def branch(client):
    store = client.app.state.store
    project = store.create_project("Synthetic merge")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [
            {"text": "Hej København!", "start": 0, "end": 2},
            {"text": "Review this.", "start": 2, "end": 4},
            {"text": "Tak igen.", "start": 4, "end": 6},
        ]
    )
    segments[1]["extra_source_context"] = {"literal": ["untouched", "æ\u0301"]}
    ours = store.add_result(
        recording["id"],
        segments,
        {"MANUAL": "Named researcher"},
        "Reviewed source",
        {"kind": "provider", "model": "synthetic-asr", "private_path": "private-source-path"},
    )
    current = store.recording(recording["id"])
    frozen = store.freeze_result(recording["id"], ours["id"], current["revision"])
    job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    enhanced = normalize_segments(
        [
            {
                "text": "Hej København!",
                "start": 0,
                "end": 2,
                "speaker": "SPEAKER_00",
                "words": [
                    {"word": "Hej", "start": 0.1, "end": 0.5},
                    {"word": "København!", "start": 0.8, "end": 1.9},
                ],
            },
            {"text": "Review this.", "start": 2, "end": 4},
            {
                "text": "Tak igen.",
                "start": 4,
                "end": 6,
                "words": [
                    {"word": "Tak", "start": 4.1, "end": 4.9},
                    {"word": "igen.", "start": 5.1, "end": 5.8},
                ],
            },
        ]
    )
    incoming = store.add_result(
        recording["id"],
        enhanced,
        {"SPEAKER_00": "Detected voice"},
        "Synthetic enhancement",
        {"kind": "enhancement", "source_result_id": ours["id"], "model": "synthetic-aligner"},
        job_id=job["id"],
    )
    reviewed = deepcopy(ours["segments"])
    reviewed[1] = edit_segment(reviewed[1], "free", "Reviewed 🧪 æ\u0301 wording.\n")
    current = store.save_transcript(
        recording["id"],
        reviewed,
        ours["speakers"],
        current["revision"],
        action="free_rewrite",
        context={"segment_id": reviewed[1]["id"]},
    )
    store.save_notes("recordings", recording["id"], "Independent notes æøå", 0)
    return {
        "store": store,
        "recording": current,
        "ours": store.result(recording["id"], ours["id"]),
        "incoming": incoming,
        "frozen": frozen,
        "job": job,
        "url": f"/api/recordings/{recording['id']}/transcripts/{ours['id']}",
        "request": {"incoming_result_id": incoming["id"], "revision": current["revision"]},
    }


def rows(store):
    with store.connection() as db:
        return {
            table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in (
                "recordings",
                "transcript_results",
                "result_versions",
                "jobs",
                "projects",
                "note_versions",
                "result_trash",
            )
        }


def preview(client, branch):
    response = client.post(branch["url"] + "/merge-preview", json=branch["request"])
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, branch, planned, **extra):
    return client.post(
        branch["url"] + "/merge",
        json={**branch["request"], "fingerprint": planned["fingerprint"], **extra},
    )


def append_incoming(branch, segments=None):
    store, incoming = branch["store"], branch["incoming"]
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if segments is not None:
            db.execute(
                "UPDATE transcript_results SET segments=? WHERE id=?",
                (json.dumps(segments, ensure_ascii=False), incoming["id"]),
            )
        return store._append_version(
            db,
            db.execute("SELECT * FROM transcript_results WHERE id=?", (incoming["id"],)).fetchone(),
            "manual_save",
            {},
            "local_user",
        )


def test_preview_uses_one_read_snapshot_and_never_writes(client, branch, monkeypatch):
    store = branch["store"]
    before = rows(store)
    connect = store.connection
    statements, connections = [], []

    @contextmanager
    def traced():
        with connect() as db:
            connections.append(db)
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, "connection", traced)
    planned = preview(client, branch)
    assert len(connections) == 1
    assert "BEGIN" in statements
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)
    monkeypatch.setattr(store, "connection", connect)
    assert rows(store) == before
    assert preview(client, branch) == planned
    assert planned["summary"] == {
        "updated_passages": 2,
        "timing_updates": 2,
        "speaker_updates": 1,
        "conflicts": 0,
        "skipped": 1,
        "unchanged": 0,
    }
    assert [passage["status"] for passage in planned["passages"]] == [
        "updated",
        "skipped",
        "updated",
    ]
    assert planned["passages"][1]["text"] == branch["ours"]["segments"][1]["text"]
    assert planned["incoming_label"] == branch["incoming"]["label"]
    assert not planned["truncated"]


def test_apply_appends_one_exact_successor_preserving_other_evidence(client, branch):
    store, current, ours, incoming = (
        branch["store"],
        branch["recording"],
        branch["ours"],
        branch["incoming"],
    )
    before = rows(store)
    ours_head = store.result_versions(current["id"], ours["id"])[0]
    creation = store.result_versions(current["id"], incoming["id"])[0]
    planned = preview(client, branch)
    response = apply(client, branch, planned, reason="Literal <review> æøå")
    assert response.status_code == 200, response.text
    saved = response.json()
    recording = saved["recording"]
    assert recording["active_transcript_id"] == ours["id"]
    assert recording["revision"] == current["revision"] + 1
    assert recording["notes"] == "Independent notes æøå" and recording["notes_revision"] == 1
    assert recording["segments"][1] == ours["segments"][1]
    assert recording["speakers"] == {**ours["speakers"], **incoming["speakers"]}
    for index in (0, 2):
        segment = recording["segments"][index]
        assert segment["text"] == ours["segments"][index]["text"]
        assert segment["words"] == incoming["segments"][index]["words"]
        for field in ("original_text", "original_words", "original_word_timing", "edited"):
            assert segment[field] == ours["segments"][index][field]
    assert store.result(current["id"], ours["id"])["segments"] == recording["segments"]
    assert store.result(current["id"], incoming["id"]) == incoming
    versions = store.result_versions(current["id"], ours["id"])
    assert len(versions) == 3
    head = store.result_version(current["id"], ours["id"], saved["version_id"])
    assert head["action"] == "result_merge" and head["actor_kind"] == "local_user"
    assert head["predecessor_id"] == ours_head["id"]
    assert head["source_version_id"] == ours_head["source_version_id"]
    assert head["reason"] == "Literal <review> æøå"
    assert head["context"]["processing"] == ours_head["context"]["processing"]
    evidence = head["context"]["merge"]
    assert set(evidence) == {"format", "base", "incoming", "creation", "summary"}
    assert evidence["base"]["version_id"] == branch["frozen"]["source_version_id"]
    assert evidence["incoming"]["version_id"] == creation["id"]
    assert evidence["creation"] == evidence["incoming"]
    assert evidence["summary"] == planned["summary"] == saved["summary"]
    assert "private-source-path" not in json.dumps(head["context"])
    for table in ("jobs", "projects", "note_versions", "result_trash"):
        assert rows(store)[table] == before[table]
    assert rows(store)["result_versions"][:-1] == before["result_versions"]
    before_repeat = rows(store)
    assert apply(client, branch, planned).status_code == 409
    assert rows(store) == before_repeat


@pytest.mark.parametrize(
    "change", ["ours", "active", "incoming-head", "ours-trash", "incoming-trash"]
)
def test_stale_preview_never_writes_after_inputs_change(client, branch, change):
    planned = preview(client, branch)
    store, recording, ours, incoming = (
        branch["store"],
        branch["recording"],
        branch["ours"],
        branch["incoming"],
    )
    if change == "ours":
        store.save_transcript(
            recording["id"], ours["segments"], ours["speakers"], recording["revision"]
        )
    elif change == "active":
        store.activate_result(recording["id"], incoming["id"], recording["revision"])
    elif change.endswith("trash"):
        target = incoming if change == "incoming-trash" else ours
        store.set_result_trashed(recording["id"], target["id"], recording["revision"], trashed=True)
    else:
        append_incoming(branch)  # A new immutable head, even with identical payload bytes.
    before = rows(store)
    response = apply(client, branch, planned)
    assert response.status_code == 409, response.text
    assert "another window" in response.json()["detail"]
    assert rows(store) == before


@pytest.mark.parametrize("snapshot", ["base", "ours", "incoming", "creation"])
def test_all_four_version_payloads_are_integrity_checked(client, branch, snapshot):
    store = branch["store"]
    creation = store.result_versions(branch["recording"]["id"], branch["incoming"]["id"])[0]
    incoming_head = append_incoming(branch)
    identifiers = {
        "base": branch["frozen"]["source_version_id"],
        "ours": store.result_versions(branch["recording"]["id"], branch["ours"]["id"])[0]["id"],
        "incoming": incoming_head["id"],
        "creation": creation["id"],
    }
    store.execute(
        "UPDATE result_versions SET payload_sha256=? WHERE id=?", ("0" * 64, identifiers[snapshot])
    )
    before = rows(store)
    response = client.post(branch["url"] + "/merge-preview", json=branch["request"])
    assert response.status_code == 400, response.text
    assert "integrity" in response.json()["detail"]
    assert rows(store) == before


@pytest.mark.parametrize(
    "problem", ["unknown", "other-result", "other-recording", "not-enhancement"]
)
def test_invalid_or_unknown_frozen_base_is_refused(client, branch, problem):
    store, recording = branch["store"], branch["recording"]
    if problem == "not-enhancement":
        store.execute(
            "UPDATE transcript_results SET source=? WHERE id=?",
            (json.dumps({"kind": "provider"}), branch["incoming"]["id"]),
        )
    else:
        if problem == "unknown":
            identifier = None
        else:
            target = (
                recording
                if problem == "other-result"
                else store.create_recording(recording["project_id"], "other.wav")
            )
            other = store.add_result(
                target["id"], normalize_segments([{"text": "Other."}]), {}, "Other", {}
            )
            identifier = store.result_versions(target["id"], other["id"])[0]["id"]
        store.execute(
            "UPDATE result_versions SET source_version_id=? WHERE result_id=?",
            (identifier, branch["incoming"]["id"]),
        )
    before = rows(store)
    response = client.post(branch["url"] + "/merge-preview", json=branch["request"])
    assert response.status_code == 400, response.text
    assert rows(store) == before


@pytest.mark.parametrize("mirror", ["active", "incoming"])
def test_projection_mirror_corruption_is_refused(client, branch, mirror):
    store = branch["store"]
    if mirror == "active":
        store.execute(
            "UPDATE recordings SET speakers='{}' WHERE id=?", (branch["recording"]["id"],)
        )
    else:
        store.execute(
            "UPDATE transcript_results SET speakers='{}' WHERE id=?", (branch["incoming"]["id"],)
        )
    before = rows(store)
    response = client.post(branch["url"] + "/merge-preview", json=branch["request"])
    assert response.status_code == 400, response.text
    assert rows(store) == before


def test_version_append_failure_rolls_back_both_mirrors(client, branch, monkeypatch):
    store = branch["store"]
    planned = preview(client, branch)
    before = rows(store)
    store.execute(
        "CREATE TRIGGER reject_merge BEFORE INSERT ON result_versions "
        "WHEN NEW.action='result_merge' BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END"
    )
    snapshots = []
    monkeypatch.setattr(store, "snapshot_project", snapshots.append)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic rollback"):
        apply(client, branch, planned)
    assert rows(store) == before
    assert not snapshots


def test_no_change_merge_cannot_append_a_version(client, branch):
    base = branch["store"].result_version(
        branch["recording"]["id"], branch["ours"]["id"], branch["frozen"]["source_version_id"]
    )["payload"]
    append_incoming(branch, base["segments"])
    # No incoming timing remains; the incoming speaker dictionary alone is not adopted.
    planned = preview(client, branch)
    assert planned["summary"]["updated_passages"] == 0
    before = rows(branch["store"])
    response = apply(client, branch, planned)
    assert response.status_code == 400 and "no safe changes" in response.json()["detail"]
    assert rows(branch["store"]) == before


def test_merge_preserves_a_derived_targets_original_source_lineage(client, branch):
    store, recording = branch["store"], branch["recording"]
    frozen = store.freeze_result(recording["id"], branch["ours"]["id"], recording["revision"])
    target_job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    target = store.add_result(
        recording["id"],
        deepcopy(frozen["segments"]),
        deepcopy(frozen["speakers"]),
        "Derived target",
        {"kind": "enhancement", "source_result_id": frozen["id"]},
        job_id=target_job["id"],
    )
    current = store.activate_result(recording["id"], target["id"], recording["revision"])
    target_base = store.freeze_result(recording["id"], target["id"], current["revision"])
    incoming_job = store.add_job(recording["id"], "enhance", {"source_snapshot": target_base})
    enhanced = deepcopy(target_base["segments"])
    enhanced[0].update(words=deepcopy(branch["incoming"]["segments"][0]["words"]), word_timing=True)
    incoming = store.add_result(
        recording["id"],
        enhanced,
        {},
        "Later enhancement",
        {"kind": "enhancement", "source_result_id": target["id"]},
        job_id=incoming_job["id"],
    )
    scoped = {
        **branch,
        "url": f"/api/recordings/{recording['id']}/transcripts/{target['id']}",
        "request": {"incoming_result_id": incoming["id"], "revision": current["revision"]},
    }
    planned = preview(client, scoped)
    response = apply(client, scoped, planned)
    assert response.status_code == 200, response.text
    head = store.result_version(recording["id"], target["id"], response.json()["version_id"])
    assert head["source_version_id"] == frozen["source_version_id"]
    assert head["predecessor_id"] == target_base["source_version_id"]
    assert head["context"]["merge"]["base"]["version_id"] == target_base["source_version_id"]
    assert head["source_version_id"] != head["context"]["merge"]["incoming"]["version_id"]


def test_snapshot_runs_only_after_the_atomic_merge_has_committed(client, branch, monkeypatch):
    store = branch["store"]
    planned = preview(client, branch)
    snapshots = []

    def snapshot(project_id):
        current = store.recording(branch["recording"]["id"])
        head = store.result_versions(current["id"], branch["ours"]["id"])[0]
        snapshots.append((project_id, current["revision"], head["action"]))

    monkeypatch.setattr(store, "snapshot_project", snapshot)
    response = apply(client, branch, planned)
    assert response.status_code == 200, response.text
    assert snapshots == [
        (branch["recording"]["project_id"], branch["recording"]["revision"] + 1, "result_merge")
    ]


def test_a_client_cannot_reuse_another_preview_fingerprint(client, branch):
    planned = preview(client, branch)
    before = rows(branch["store"])
    response = apply(client, branch, planned, fingerprint="0" * 64)
    assert response.status_code == 409 and "another window" in response.json()["detail"]
    assert rows(branch["store"]) == before


@pytest.mark.parametrize("route", ["merge-preview", "merge"])
@pytest.mark.parametrize("extra", [{"segments": []}, {"context": {}}, {"revision": True}])
def test_merge_requests_accept_no_client_payload_or_coerced_revision(client, branch, route, extra):
    request = {**branch["request"], **extra}
    if route == "merge":
        request["fingerprint"] = "0" * 64
    before = rows(branch["store"])
    response = client.post(branch["url"] + "/" + route, json=request)
    assert response.status_code == 422
    assert rows(branch["store"]) == before


def test_snapshot_budget_rejects_without_a_write(client, branch, monkeypatch):
    monkeypatch.setattr(result_merges, "MAX_TRANSCRIPT_BYTES", 100)
    before = rows(branch["store"])
    response = client.post(branch["url"] + "/merge-preview", json=branch["request"])
    assert response.status_code == 400
    assert rows(branch["store"]) == before

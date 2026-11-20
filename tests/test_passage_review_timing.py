"""Automatic Needs review for word timing is derived, never stored (ADR 0191). Synthetic only."""

import hashlib
import json
from pathlib import Path

import pytest
from test_change_undo import ok, step
from test_passage_review import mark, read
from test_passage_timing import process, request

from cbs_transcriber import enhancement, passage_review, portable_capture, portable_restore
from cbs_transcriber.result_actions import append_edit
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

PASSAGES = [
    {"text": "Timed.", "start": 0, "end": 2, "words": [{"word": "Timed.", "start": 0, "end": 2}]},
    {"text": "God morgen.", "start": 2, "end": 5},
    {"text": "Tak for nu.", "start": 5, "end": 9},
    {"text": " — … ", "start": 9, "end": 10},
]


def add(store, rid, source, passages=PASSAGES, **options):
    return store.add_result(
        rid, normalize_segments(passages), {}, "Synthetic", source, auto_first=True, **options
    )


@pytest.fixture
def scene(client, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store = client.app.state.store
    project = store.create_project("Synthetic timing review")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    store.execute("UPDATE recordings SET status='ready',duration=30 WHERE id=?", (rid,))
    result = add(store, rid, {"capability": "words"})
    return client, store, rid, result["id"]


def statuses(loaded):
    return [(p["status"], p["automatic"]) for p in loaded["passages"]]


def timing(scene, monkeypatch, ids):
    client, store, rid, result_id = scene
    response = request(client, rid, {"id": result_id}, ids)
    assert response.status_code == 202, response.text
    job = response.json()["id"]
    process(client, store, job, monkeypatch)
    applied = client.post(
        f"/api/recordings/{rid}/realign/{job}", json={"revision": store.recording(rid)["revision"]}
    )
    assert applied.status_code == 200, applied.text
    return job


def test_missing_timing_is_needs_review_only_where_word_timing_is_expected(scene):
    client, store, rid, result_id = scene
    loaded = read(scene)
    assert statuses(loaded) == [
        ("not_checked", None),
        ("needs_review", "word_timing_missing"),
        ("needs_review", "word_timing_missing"),
        ("not_checked", None),  # Nothing lexical to time.
    ]
    assert loaded["counts"] == {"checked": 0, "needs_review": 2, "not_checked": 2, "total": 4}
    assert all(p["review_revision"] == 0 and not p["stale"] for p in loaded["passages"])
    assert store.all("SELECT * FROM passage_reviews") == []  # Derived, never stored.
    untimed = [{key: p[key] for key in ("text", "start", "end")} for p in PASSAGES]
    # Speakers-only and untimed-by-design results are not flooded.
    for source in ({}, {"capability": "speakers", "options": {"diarize": True, "align": False}}):
        other = add(store, rid, source, untimed)
        store.activate_result(rid, other["id"], store.recording(rid)["revision"])
        assert {p["status"] for p in read((client, store, rid, other["id"]))["passages"]} == {
            "not_checked"
        }
    # Requested alignment, or passages carrying their own word timing, mean timing is expected.
    aligned = add(store, rid, {"options": {"align": True}}, untimed)
    store.activate_result(rid, aligned["id"], store.recording(rid)["revision"])
    assert read((client, store, rid, aligned["id"]))["counts"]["needs_review"] == 3
    evidenced = add(store, rid, {}, PASSAGES)
    store.activate_result(rid, evidenced["id"], store.recording(rid)["revision"])
    assert read((client, store, rid, evidenced["id"]))["counts"]["needs_review"] == 2


def test_a_researchers_mark_on_untimed_content_stays_until_that_content_changes(scene):
    client, store, rid, _ = scene
    assert mark(scene, "checked", index=1).status_code == 200
    removed = mark(scene, "not_checked", index=2)
    assert removed.status_code == 200, removed.text
    assert removed.json()["passage"] == {
        **removed.json()["passage"],
        "status": "not_checked",
        "automatic": None,
        "review_revision": 1,
    }
    loaded = read(scene)
    # The researcher's mark wins; a removed automatic mark is never invented again.
    assert statuses(loaded)[1:3] == [("checked", None), ("not_checked", None)]
    assert loaded["counts"]["needs_review"] == 0
    edited = client.patch(
        f"/api/recordings/{rid}/segments/2",
        json={"revision": loaded["revision"], "mode": "free", "text": "Tak for i dag."},
    )
    assert edited.status_code == 200, edited.text
    current = read(scene)
    assert statuses(current)[2] == ("needs_review", "word_timing_missing")
    assert current["passages"][2]["stale"] and current["passages"][2]["review_revision"] == 1


def test_added_timing_needs_review_again_without_touching_review_rows(scene, monkeypatch):
    client, store, rid, result_id = scene
    assert mark(scene, "checked", index=1).status_code == 200
    assert mark(scene, "not_checked", index=2).status_code == 200
    rows = store.all("SELECT * FROM passage_reviews ORDER BY segment_id")
    job = timing(scene, monkeypatch, ["1", "2"])
    loaded = read(scene)
    assert statuses(loaded)[1:3] == [("needs_review", "timing_added")] * 2
    assert [p["review_revision"] for p in loaded["passages"][1:3]] == [1, 1]
    assert store.all("SELECT * FROM passage_reviews ORDER BY segment_id") == rows
    version = store.result_versions(rid, result_id)[0]
    assert version["action"] == "timing_realign" and version["context"]["job_id"] == job
    assert version["context"]["timing_added"] == {
        p["segment_id"]: p["passage_sha256"] for p in loaded["passages"][1:3]
    }
    # Reviewing the timed passage replaces the automatic mark with the researcher's own.
    checked = mark(scene, "checked", index=1, loaded=loaded)
    assert checked.status_code == 200, checked.text
    assert checked.json()["passage"]["automatic"] is None
    assert statuses(read(scene))[1:3] == [("checked", None), ("needs_review", "timing_added")]
    # A lost reply re-applies idempotently against the same exact evidence.
    again = client.post(
        f"/api/recordings/{rid}/realign/{job}",
        json={"revision": store.recording(rid)["revision"]},
    )
    assert again.status_code == 200, again.text


def test_skipped_passages_are_not_marked_as_timed(scene, monkeypatch):
    client, store, rid, result_id = scene
    response = request(client, rid, {"id": result_id}, ["1", "2"])
    job = response.json()["id"]
    process(client, store, job, monkeypatch)
    edited = client.patch(
        f"/api/recordings/{rid}/segments/2",
        json={"revision": store.recording(rid)["revision"], "mode": "free", "text": "Tak."},
    )
    assert edited.status_code == 200, edited.text
    applied = client.post(
        f"/api/recordings/{rid}/realign/{job}", json={"revision": store.recording(rid)["revision"]}
    )
    assert applied.status_code == 200, applied.text
    assert list(store.result_versions(rid, result_id)[0]["context"]["timing_added"]) == ["1"]
    assert statuses(read(scene))[1:3] == [
        ("needs_review", "timing_added"),
        ("needs_review", "word_timing_missing"),
    ]
    listed = next(j for j in client.get("/api/jobs").json() if j["id"] == job)
    assert listed["realign_outcome"] == {
        "applied": "applied",
        "applied_count": 1,
        "skipped_count": 1,
        "output_count": 2,
        "complete_count": 2,
    }


def test_undo_returns_to_the_missing_mark_and_redo_to_timing_added(scene, monkeypatch):
    client, store, rid, _ = scene
    assert mark(scene, "checked", index=1).status_code == 200
    timing(scene, monkeypatch, ["1"])
    assert statuses(read(scene))[1] == ("needs_review", "timing_added")
    undone = ok(step(client, store.recording(rid)))
    # The Checked mark bound to the untimed content is valid again (ADR 0180 rule).
    assert statuses(read(scene))[1] == ("checked", None)
    ok(step(client, undone, "redo"))
    assert statuses(read(scene))[1] == ("needs_review", "timing_added")
    rows = store.all("SELECT segment_id,status,revision FROM passage_reviews")
    assert rows == [{"segment_id": "1", "status": "checked", "revision": 1}]


def test_review_evidence_is_exact_and_fails_closed(scene, monkeypatch):
    client, store, rid, result_id = scene
    job = timing(scene, monkeypatch, ["1"])
    version = store.result_versions(rid, result_id)[0]
    forged = {"1": "0" * 64}
    store.execute(
        "UPDATE result_versions SET context=json_set(context,'$.timing_added',json(?)) WHERE id=?",
        (json.dumps(forged), version["id"]),
    )
    # The lost-reply proof reconstructs the successor and refuses changed evidence.
    again = client.post(
        f"/api/recordings/{rid}/realign/{job}",
        json={"revision": store.recording(rid)["revision"]},
    )
    assert again.status_code == 409 and "review evidence" in again.text
    store.execute(
        "UPDATE result_versions SET context=json_set(context,'$.timing_added',json(?)) WHERE id=?",
        ('{"1":"not a digest"}', version["id"]),
    )
    with pytest.raises(ValueError, match="review evidence is invalid"):
        passage_review.read(store, rid, result_id)
    # Versions written before ADR 0191 carry no evidence and raise no timing-added mark.
    store.execute(
        "UPDATE result_versions SET context=json_remove(context,'$.timing_added') WHERE id=?",
        (version["id"],),
    )
    assert statuses(passage_review.read(store, rid, result_id))[1] == ("not_checked", None)


def test_timing_review_evidence_is_bounded(scene, monkeypatch):
    _, store, rid, result_id = scene
    timing(scene, monkeypatch, ["1"])
    monkeypatch.setattr(passage_review, "MAX_TIMING_ENTRIES", 0)
    assert statuses(passage_review.read(store, rid, result_id))[1] == ("not_checked", None)


def test_local_word_timing_keeps_its_own_completion_message(scene):
    client, store, rid, result_id = scene
    job = request(client, rid, {"id": result_id}, ["1"]).json()["id"]
    store.update_job(job, message="Word timing could not be found in this passage's audio.")
    queue = client.app.state.queue
    assert queue._completion_message(job) == (
        "Word timing could not be found in this passage's audio."
    )
    other = store.add_job(rid, "media", {})
    assert queue._completion_message(other["id"]) == "Complete"


def append_timing(store, rid, result_id, segment_id):
    """A timing successor written by the shared writer with ADR 0191 evidence.

    The real worker's capture artifacts nest too deep for Windows restore staging under
    pytest's temporary folder, so this synthetic successor has no timing artifacts.
    """
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT * FROM recordings WHERE id=?", (rid,)).fetchone()
        result = db.execute("SELECT * FROM transcript_results WHERE id=?", (result_id,)).fetchone()
        segments = json.loads(current["segments"])
        index = next(i for i, s in enumerate(segments) if s["id"] == segment_id)
        timed = normalize_segments(
            [
                {
                    **{key: segments[index][key] for key in ("text", "start", "end")},
                    "words": [
                        {"word": word, "start": 2 + i * 0.5, "end": 2.4 + i * 0.5}
                        for i, word in enumerate(segments[index]["text"].split())
                    ],
                }
            ]
        )[0]
        segments[index] = segments[index] | {
            "words": timed["words"],
            "word_timing": True,
            "words_current": True,
        }
        append_edit(
            store,
            db,
            current,
            result_id,
            store._head_version(db, result),
            segments,
            json.loads(current["speakers"]),
            action="timing_realign",
            context={
                "job_id": "0" * 32,
                "segment_id": segment_id,
                passage_review.TIMING_KEY: passage_review.timing_added(segments, [segment_id]),
            },
            reason=None,
        )


def test_portable_restore_reproduces_the_derived_marks(client, tmp_path, wav_bytes):
    source = client.app.state.store
    project = source.create_project("R")
    recording = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("tiny.wav", wav_bytes, "audio/wav")},
    ).json()["recording"]
    source.execute("UPDATE jobs SET state='done'")
    source.execute("UPDATE recordings SET status='ready',duration=30")
    rid = recording["id"]
    result_id = add(source, rid, {"capability": "words"})["id"]
    scene = (client, source, rid, result_id)
    assert mark(scene, "checked", index=0).status_code == 200
    append_timing(source, rid, result_id, "1")
    before = passage_review.read(source, rid, result_id)
    assert statuses(before)[:3] == [
        ("checked", None),
        ("needs_review", "timing_added"),
        ("needs_review", "word_timing_missing"),
    ]
    with source.library_lock():
        saved = portable_capture.capture_library(
            source,
            client.app.state.queue,
            tmp_path / "packages",
            gate=portable_capture.CaptureGate(),
        )
    from test_portable_restore import destination

    target = destination(tmp_path, "r")
    receipt = portable_restore.restore_library(
        saved["folder"],
        expected_commit_sha256=saved["commit_sha256"],
        target=target,
        live_root=source.root,
    )
    restored = Store(target)
    after = passage_review.read(restored, rid, result_id)
    assert after["passages"] == before["passages"] and after["counts"] == before["counts"]
    preserved = Path(target) / "preservation" / receipt["operation_id"] / "source.sqlite3"
    assert hashlib.sha256(preserved.read_bytes()).hexdigest() == receipt["source_database_sha256"]

"""Derived results say when their exact source result was edited after it was frozen."""

from cbs_transcriber.transcripts import normalize_segments


def setup(client):
    store = client.app.state.store
    project = store.create_project("Stale source")
    recording = store.create_recording(project["id"], "a.wav")
    source = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Original wording.", "start": 0, "end": 2}]),
        {},
        "Provider",
        {},
    )
    frozen = store.freeze_result(recording["id"], source["id"], 1)
    job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    return store, recording, source, job


def edit(client, store, recording, text):
    revision = store.recording(recording["id"])["revision"]
    response = client.patch(
        f"/api/recordings/{recording['id']}/segments/0",
        json={"revision": revision, "mode": "free", "text": text},
    )
    assert response.status_code == 200, response.text


def listed(client, job_id):
    return next(row for row in client.get("/api/jobs").json() if row["id"] == job_id)


def test_unchanged_source_is_not_flagged(client):
    store, recording, source, job = setup(client)
    assert listed(client, job["id"])["source_changed"] is False
    enhanced = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Original wording.", "start": 0, "end": 2}]),
        {},
        "Enhanced",
        {"kind": "enhancement", "source_result_id": source["id"]},
        job_id=job["id"],
    )
    rows = {row["id"]: row for row in store.results(recording["id"])}
    assert rows[enhanced["id"]]["source_changed"] is False
    assert rows[enhanced["id"]]["source_version_sequence"] == 1
    # Results without a frozen source carry no comparison at all.
    assert rows[source["id"]]["source_changed"] is False
    assert rows[source["id"]]["source_version_sequence"] is None


def test_edits_after_queueing_flag_job_and_result_without_rebasing(client):
    store, recording, source, job = setup(client)
    edit(client, store, recording, "Edited while queued.")
    assert listed(client, job["id"])["source_changed"] is True
    enhanced = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Original wording.", "start": 0, "end": 2}]),
        {},
        "Enhanced",
        {"kind": "enhancement", "source_result_id": source["id"]},
        job_id=job["id"],
    )
    # The enhancement keeps the frozen wording; the edit stays in the source result only.
    assert enhanced["segments"][0]["text"] == "Original wording."
    assert store.recording(recording["id"])["active_transcript_id"] == source["id"]
    recording_view = client.get(f"/api/recordings/{recording['id']}").json()
    row = next(t for t in recording_view["transcripts"] if t["id"] == enhanced["id"])
    assert row["source_changed"] is True
    assert (row["source_version_sequence"], row["source_head_sequence"]) == (1, 2)
    assert listed(client, job["id"])["source_changed"] is True


def test_edits_after_the_result_also_flag_it(client):
    store, recording, source, job = setup(client)
    enhanced = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Original wording.", "start": 0, "end": 2}]),
        {},
        "Enhanced",
        {"kind": "enhancement", "source_result_id": source["id"]},
        job_id=job["id"],
    )
    edit(client, store, recording, "Edited later.")
    row = next(t for t in store.results(recording["id"]) if t["id"] == enhanced["id"])
    assert row["source_changed"] is True
    # Editing the enhanced result itself does not make its own source look changed.
    current = store.recording(recording["id"])
    store.activate_result(recording["id"], enhanced["id"], current["revision"])
    edit(client, store, recording, "Enhanced and reviewed.")
    row = next(t for t in store.results(recording["id"]) if t["id"] == enhanced["id"])
    assert (row["source_version_sequence"], row["source_head_sequence"]) == (1, 2)

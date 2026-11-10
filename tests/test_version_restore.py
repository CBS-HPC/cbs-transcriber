"""Restoring an earlier version appends it as the newest version; history keeps everything."""

from cbs_transcriber import research_reports
from cbs_transcriber.transcripts import normalize_segments


def setup(client):
    store = client.app.state.store
    project = store.create_project("Restore")
    recording = store.create_recording(project["id"], "a.wav")
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Original wording.", "start": 0, "end": 2}]),
        {},
        "R",
        {},
    )
    revision = store.recording(recording["id"])["revision"]
    edited = client.patch(
        f"/api/recordings/{recording['id']}/segments/0",
        json={"revision": revision, "mode": "free", "text": "Edited wording."},
    )
    assert edited.status_code == 200, edited.text
    return store, recording, result


def test_restore_appends_the_exact_earlier_state(client):
    store, recording, result = setup(client)
    versions = store.result_versions(recording["id"], result["id"])
    original = next(v for v in versions if v["sequence"] == 1)
    current = store.recording(recording["id"])
    url = (
        f"/api/recordings/{recording['id']}/transcripts/{result['id']}"
        f"/versions/{original['id']}/restore"
    )
    response = client.post(url, json={"revision": current["revision"], "reason": "Undo"})
    assert response.status_code == 200, response.text
    assert response.json()["segments"][0]["text"] == "Original wording."
    newest = store.result_versions(recording["id"], result["id"])[0]
    assert newest["action"] == "version_restore" and newest["sequence"] == 3
    detail = store.result_version(recording["id"], result["id"], newest["id"])
    assert detail["context"]["restored_from_version_id"] == original["id"]
    assert detail["payload_sha256"] == original["payload_sha256"]
    assert "version_restore" in research_reports.EDIT_ACTIONS
    # A stale window cannot restore over newer work.
    stale = client.post(url, json={"revision": current["revision"]})
    assert stale.status_code == 409


def test_versions_cannot_be_restored_into_another_result(client):
    store, recording, result = setup(client)
    other = store.add_result(
        recording["id"], normalize_segments([{"text": "Other."}]), {}, "Other", {}
    )
    version = store.result_versions(recording["id"], other["id"])[0]
    response = client.post(
        f"/api/recordings/{recording['id']}/transcripts/{result['id']}"
        f"/versions/{version['id']}/restore",
        json={"revision": store.recording(recording["id"])["revision"]},
    )
    assert response.status_code == 404

"""Fresh manual editing must remain available when word timing is ambiguous."""

from copy import deepcopy

from test_free_text_timing_retention import passage


def seed(client, text="ja tak tak", *, partial=False):
    store = client.app.state.store
    project = store.create_project("Synthetic manual editing")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segment = passage(text)
    if partial:
        segment["words"][1].update(start=None, end=None)
        segment["word_timing"] = False
    other = passage("Unrelated passage.")
    other["id"] = "other"
    store.add_result(
        recording["id"], [segment, other], {"A": "Voice"}, "Synthetic", {}, auto_first=True
    )
    return store, store.recording(recording["id"])


def test_new_text_route_appends_repeated_word_without_losing_existing_times(client):
    store, current = seed(client)
    url = f"/api/recordings/{current['id']}/segments/0"
    body = {"revision": current["revision"], "mode": "free", "text": "ja tak tak tak"}
    legacy = client.patch(url + "/edit-v2", json=body)
    assert legacy.status_code == 400
    response = client.patch(url + "/edit-v3", json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["segments"][0]["words"][:-1] == current["segments"][0]["words"]
    assert saved["segments"][0]["words"][-1]["start"] is None
    assert saved["segments"][0]["words"][-1]["end"] is None
    assert saved["segments"][1] == current["segments"][1]
    assert saved["speakers"] == current["speakers"]
    versions = store.result_versions(current["id"], current["active_transcript_id"])
    assert versions[0]["context"]["text_policy"] == "permissive-word-map-v2"
    assert saved["result_roles"]["working"]["version_id"] == versions[0]["id"]
    assert not store.all("SELECT id FROM jobs WHERE recording_id=?", (current["id"],))
    stale = client.patch(url + "/edit-v3", json=body)
    assert stale.status_code in (400, 409)
    assert len(store.result_versions(current["id"], current["active_transcript_id"])) == 2


def test_new_split_route_allows_missing_timing_and_proves_exact_undo(client):
    store, current = seed(client, "Hej København igen.", partial=True)
    original = deepcopy(current["segments"])
    base = f"/api/recordings/{current['id']}/segments/0"
    response = client.post(
        base + "/split-v2",
        json={
            "revision": current["revision"],
            "result_id": current["active_transcript_id"],
            "cursor": len("Hej "),
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["segments"][0]["text"] + saved["segments"][1]["text"] == original[0]["text"]
    assert saved["segments"][2] == original[1]
    version = store.result_versions(current["id"], current["active_transcript_id"])[0]
    assert version["context"]["split_policy"] == "manual-word-boundary-v2"
    restored = client.post(
        base + "/undo-split",
        json={"revision": saved["revision"], "result_id": saved["active_transcript_id"]},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["segments"] == original


def test_new_split_route_refuses_inside_word_without_writing(client):
    store, current = seed(client, "Hej verden")
    response = client.post(
        f"/api/recordings/{current['id']}/segments/0/split-v2",
        json={
            "revision": current["revision"],
            "result_id": current["active_transcript_id"],
            "cursor": 1,
        },
    )
    assert response.status_code == 400, response.text
    assert "between words" in response.text
    assert store.recording(current["id"])["segments"] == current["segments"]
    assert len(store.result_versions(current["id"], current["active_transcript_id"])) == 1

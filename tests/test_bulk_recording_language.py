"""The bulk language action is atomic over its exact revisioned selection."""


def test_bulk_language_preserves_other_recordings_and_rejects_stale_or_foreign(client):
    store = client.app.state.store
    project = store.create_project("Synthetic")
    other = store.create_project("Other")
    rows = [store.create_recording(project["id"], f"{index}.wav") for index in range(3)]
    foreign = store.create_recording(other["id"], "foreign.wav")
    endpoint = f"/api/projects/{project['id']}/recording-languages"
    selected = [{"id": row["id"], "revision": 0} for row in rows[:2]]
    response = client.put(endpoint, json={"language": "en", "recordings": selected})
    assert response.status_code == 200, response.text
    assert [row["revision"] for row in response.json()["recordings"]] == [1, 1]
    assert store.recording(rows[2]["id"])["language_override"] is None
    stale = client.put(endpoint, json={"language": "da", "recordings": selected})
    assert stale.status_code == 409
    assert [store.recording(row["id"])["language_override"] for row in rows] == ["en", "en", None]
    mixed = [{"id": rows[0]["id"], "revision": 1}, {"id": foreign["id"], "revision": 0}]
    assert client.put(endpoint, json={"language": "da", "recordings": mixed}).status_code == 409
    assert store.recording(rows[0]["id"])["language_override"] == "en"
    current = [{"id": row["id"], "revision": 1} for row in rows[:2]]
    assert client.put(endpoint, json={"language": None, "recordings": current}).status_code == 200
    assert [store.recording(row["id"])["language_override"] for row in rows] == [None, None, None]
    assert (
        client.put(endpoint, json={"language": "en", "recordings": current[:1] * 2}).status_code
        == 400
    )


def test_bulk_language_conflict_rolls_back_every_member(client):
    store = client.app.state.store
    project = store.create_project("Synthetic")
    first = store.create_recording(project["id"], "first.wav")
    second = store.create_recording(project["id"], "second.wav")
    client.put(f"/api/recordings/{second['id']}/language", json={"language": "en", "revision": 0})
    response = client.put(
        f"/api/projects/{project['id']}/recording-languages",
        json={
            "language": "da",
            "recordings": [{"id": first["id"], "revision": 0}, {"id": second["id"], "revision": 0}],
        },
    )
    assert response.status_code == 409
    assert store.recording(first["id"])["language_override"] is None
    assert store.recording(second["id"])["language_override"] == "en"

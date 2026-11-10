"""A stale Provider Settings form cannot silently overwrite newer saved settings."""


def openai(client):
    response = client.post(
        "/api/providers",
        json={
            "name": "OpenAI",
            "provider_type": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "whisper-1",
            "approved_for": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_stale_form_cannot_restore_an_approval_removed_in_another_window(client):
    loaded = openai(client)
    first = client.put(
        "/api/providers/" + loaded["id"],
        json={**loaded, "approved_for": ["protected"], "expected_version": loaded["version"]},
    )
    assert first.status_code == 200, first.text
    newer = first.json()
    assert newer["version"] != loaded["version"]
    stale = client.put(
        "/api/providers/" + loaded["id"],
        json={**loaded, "language": "da", "expected_version": loaded["version"]},
    )
    assert stale.status_code == 409 and "another window" in stale.json()["detail"]
    current = next(
        p for p in client.get("/api/providers").json()["providers"] if p["id"] == loaded["id"]
    )
    assert current["approved_for"] == ["protected"] and current["language"] == ""
    fresh = client.put(
        "/api/providers/" + loaded["id"],
        json={**current, "language": "da", "expected_version": current["version"]},
    )
    assert fresh.status_code == 200 and fresh.json()["language"] == "da"


def test_clients_without_a_version_keep_working(client):
    loaded = openai(client)
    payload = {key: value for key, value in loaded.items() if key != "version"}
    response = client.put("/api/providers/" + loaded["id"], json={**payload, "language": "en"})
    assert response.status_code == 200 and response.json()["language"] == "en"

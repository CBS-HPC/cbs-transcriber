"""Expected values prevent stale project settings from overwriting newer writes."""


def test_stale_name_classification_and_storage_are_rejected(client, tmp_path):
    store = client.app.state.store
    project = store.create_project("Original")
    identifier = project["id"]
    assert client.patch(f"/api/projects/{identifier}", json={"name": "New"}).status_code == 200
    response = client.patch(
        f"/api/projects/{identifier}", json={"name": "Stale", "expected_name": "Original"}
    )
    assert response.status_code == 409
    response = client.patch(
        f"/api/projects/{identifier}/classification", json={"classification": "protected"}
    )
    assert response.status_code == 200
    response = client.patch(
        f"/api/projects/{identifier}/classification",
        json={"classification": "standard", "expected_classification": "standard"},
    )
    assert response.status_code == 409
    response = client.post(
        f"/api/projects/{identifier}/storage",
        json={"storage_parent": str(tmp_path / "new-parent"), "expected_storage_path": "stale"},
    )
    assert response.status_code == 409
    assert not (tmp_path / "new-parent").exists()
    row = store.one("SELECT name,classification FROM projects WHERE id=?", (identifier,))
    assert row == {"name": "New", "classification": "protected"}

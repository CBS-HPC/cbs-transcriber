"""Mounted background ZIP routes retain snapshots and source-operation boundaries."""

import io
import json
import threading
from types import SimpleNamespace
from zipfile import ZipFile

from test_export_originals import originals as originals

from cbs_transcriber import exports, libraries, portable_routes


def _start(client, project, rows, **options):
    return client.post(
        f"/api/projects/{project['id']}/export-tasks",
        json={
            "recording_ids": [row["id"] for row in rows],
            "formats": ["json"],
            **options,
        },
    )


def _finish(client, task):
    manager = client.app.state.export_tasks
    thread = manager._tasks[task["export_task"]["id"]].thread
    thread.join(timeout=10)
    assert not thread.is_alive()
    return next(row for row in manager.activity() if row["id"] == task["id"])


def test_export_http202_freezes_snapshot_and_fences_file_operations(
    client, originals, monkeypatch, tmp_path
):
    store, project, rows, sources = originals
    entered, release = threading.Event(), threading.Event()
    original = exports._stage_original

    def stalled(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(exports, "_stage_original", stalled)
    response = _start(client, project, rows[:1], include_originals=True)
    assert response.status_code == 202, response.text
    task = response.json()
    try:
        assert entered.wait(2)
        assert task["state"] == "running"
        renamed = client.patch(
            f"/api/projects/{project['id']}",
            json={"name": "Later project name", "expected_name": project["name"]},
        )
        assert renamed.status_code == 200, renamed.text
        assert any(row["id"] == task["id"] for row in client.get("/api/jobs").json())
        preview = client.post(f"/api/projects/{project['id']}/deletion-preview")
        assert preview.status_code == 200, preview.text
        deletion = client.delete(
            f"/api/projects/{project['id']}",
            params={
                "operation_id": preview.json()["operation_id"],
                "fingerprint": preview.json()["fingerprint"],
            },
        )
        capture = client.post(
            "/api/library/portable-backups", json={"destination": str(tmp_path / "capture")}
        )
        move = client.post(
            f"/api/projects/{project['id']}/storage",
            json={"storage_parent": str(tmp_path / "move")},
        )
        for refusal in (deletion, capture, move):
            assert refusal.status_code == 409, refusal.text
            assert "export" in refusal.json()["detail"].lower()
        assert store.one("SELECT name FROM projects WHERE id=?", (project["id"],))["name"] == (
            "Later project name"
        )
        assert all(path.read_bytes() == data for path, data in sources.values())
        assert not (tmp_path / "capture").exists()
        assert not (tmp_path / "move").exists()
    finally:
        release.set()
    ready = _finish(client, task)
    assert ready["state"] == "completed", ready
    response = client.get(ready["export_task"]["download_url"])
    assert response.status_code == 200, response.text
    with ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["project"] == project["name"]
        assert manifest["recording_ids"] == [rows[0]["id"]]
    assert client.get("/api/export-tasks").json()["tasks"] == [ready]
    # A finished derived ZIP no longer reads the library or prevents deleting its source.
    deletion = client.delete(f"/api/projects/{project['id']}")
    assert deletion.status_code == 200, deletion.text
    assert not store.all("SELECT id FROM projects WHERE id=?", (project["id"],))
    retained = client.get(ready["export_task"]["download_url"])
    assert retained.status_code == 200
    assert retained.content == response.content


def test_export_routes_require_current_browser_instance_and_same_origin(client, originals):
    _, project, rows, _ = originals
    path = f"/api/projects/{project['id']}/export-tasks"
    body = {"recording_ids": [rows[0]["id"]], "formats": ["json"]}
    instance = client.app.state.browser_instance
    foreign = client.post(
        path,
        json=body,
        headers={"Origin": "https://unrelated.example", "X-CBS-Instance": instance},
    )
    assert foreign.status_code == 403
    stale = client.post(path, json=body, headers={"X-CBS-Instance": "old-instance"})
    assert stale.status_code == 409
    assert client.app.state.export_tasks.activity() == []
    response = client.post(
        path, json=body, headers={"Origin": "http://127.0.0.1", "X-CBS-Instance": instance}
    )
    assert response.status_code == 202, response.text
    ready = _finish(client, response.json())
    download = ready["export_task"]["download_url"]
    for query in ({}, {"cbs_instance": "old-instance"}):
        response = client.get(download, params=query, headers={"Sec-Fetch-Site": "same-origin"})
        assert response.status_code == 409
        assert client.app.state.export_tasks._tasks[ready["export_task"]["id"]].readers == 0
    delivered = client.get(
        download,
        params={"cbs_instance": instance},
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.content.startswith(b"PK")
    assert int(delivered.headers["Content-Length"]) == len(delivered.content)
    assert delivered.headers["Cache-Control"] == "no-store"
    assert delivered.headers["Content-Disposition"] == (
        f'attachment; filename="project-{project["id"]}.zip"'
    )
    assert client.app.state.export_tasks._tasks[ready["export_task"]["id"]].readers == 0


def test_mounted_capture_guard_prevents_export_start_during_capture(client, originals, monkeypatch):
    _, project, rows, _ = originals
    entered, release = threading.Event(), threading.Event()
    result = []

    def capture(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return {"synthetic": True}

    monkeypatch.setattr(portable_routes, "capture_library", capture)

    def request():
        result.append(client.post("/api/library/portable-backups", json={}))

    thread = threading.Thread(target=request)
    thread.start()
    try:
        assert entered.wait(2)
        response = _start(client, project, rows[:1])
        assert response.status_code == 409, response.text
        assert client.app.state.export_tasks.activity() == []
    finally:
        release.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert result[0].status_code == 201, result[0].text


def test_library_switch_refuses_active_export_then_seals_new_starts(
    client, originals, monkeypatch, tmp_path
):
    _, project, rows, _ = originals
    entered, release = threading.Event(), threading.Event()
    original = exports._stage_original
    switched = []
    target = SimpleNamespace(path=tmp_path / "other-library")
    monkeypatch.setattr(libraries, "select", lambda *args, **kwargs: target)
    client.app.state.switch_library = switched.append

    def stalled(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(exports, "_stage_original", stalled)
    response = _start(client, project, rows[:1], include_originals=True)
    assert response.status_code == 202, response.text
    task = response.json()
    try:
        assert entered.wait(2)
        response = client.post("/api/libraries/open", json={"path": str(target.path)})
        assert response.status_code == 409, response.text
        assert switched == []
    finally:
        release.set()
    ready = _finish(client, task)
    assert ready["state"] == "completed", ready
    response = client.post("/api/libraries/open", json={"path": str(target.path)})
    assert response.status_code == 202, response.text
    assert switched == [target]
    assert _start(client, project, rows[:1]).status_code == 409
    assert client.get(ready["export_task"]["download_url"]).status_code == 200

"""Verified local library backups (ADR 0061)."""

import hashlib
import json
import sqlite3
from pathlib import Path

from cbs_transcriber.backup import create_backup
from cbs_transcriber.store import SCHEMA_VERSION


def test_backup_is_a_consistent_verified_copy_with_hashed_originals(client, wav_bytes, credentials):
    store = client.app.state.store
    credentials.set("https://api.openai.com/v1", "sk-synthetic-secret-key", False)
    project = store.create_project("Backed up")
    uploaded = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("a.wav", wav_bytes, "audio/wav")},
    ).json()["recording"]
    response = client.post("/api/library/backups", json={})
    assert response.status_code == 201, response.text
    summary = response.json()
    folder = Path(summary["folder"])
    assert folder.parent == store.root / "backups"
    assert (summary["projects"], summary["recordings"], summary["originals"]) == (1, 1, 1)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["integrity"] == "ok" and manifest["schema"] == SCHEMA_VERSION
    [original] = manifest["originals"]
    assert original["recording_id"] == uploaded["id"]
    assert original["sha256"] == hashlib.sha256(wav_bytes).hexdigest()
    with sqlite3.connect(folder / "library.sqlite3") as db:
        assert db.execute("SELECT name FROM projects").fetchone()[0] == "Backed up"
    # Keys live in the OS credential store; nothing secret is in the copy or its manifest.
    for path in folder.iterdir():
        assert b"sk-synthetic-secret-key" not in path.read_bytes()
    assert not (folder / "server.lock").exists()
    listed = client.get("/api/library/backups").json()["backups"]
    assert [b["folder"] for b in listed] == [summary["folder"]]


def test_unchanged_originals_reuse_hashes_and_a_failed_backup_leaves_nothing(
    client, wav_bytes, monkeypatch, tmp_path
):
    from cbs_transcriber import backup

    store = client.app.state.store
    project = store.create_project("Hashes")
    client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("a.wav", wav_bytes, "audio/wav")},
    )
    create_backup(store)
    calls = []
    monkeypatch.setattr(backup, "sha256", lambda path: calls.append(path) or "x")
    second = create_backup(store)
    assert calls == [] and second["originals"] == 1
    # An integrity failure removes the half-made folder.
    before = sorted(p.name for p in (store.root / "backups").iterdir())

    def broken(snapshot):
        raise ValueError("The backup copy failed its integrity check; it was not kept.")

    verify = backup.verify
    monkeypatch.setattr(backup, "verify", broken)
    response = client.post("/api/library/backups", json={})
    assert response.status_code == 400 and "integrity" in response.text
    assert sorted(p.name for p in (store.root / "backups").iterdir()) == before
    monkeypatch.setattr(backup, "verify", verify)
    # Another absolute folder may be chosen; relative ones are refused.
    other = create_backup(store, tmp_path / "elsewhere")
    assert Path(other["folder"]).parent == tmp_path / "elsewhere"
    assert client.post("/api/library/backups", json={"destination": "relative"}).status_code == 400

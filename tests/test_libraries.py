"""Library registry, discovery, validation and the switch/choose endpoints (ADR 0051)."""

import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber import libraries, storage
from cbs_transcriber.app import create_app, create_chooser_app
from cbs_transcriber.store import SCHEMA_VERSION, Store


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(storage, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "legacy_library_root", lambda: tmp_path / "legacy")
    return tmp_path


def make_library(folder, project=None):
    store = Store(folder)
    if project:
        store.create_project(project)
    return store.root


def test_remember_keeps_current_first_and_bounded_recent(home):
    for index in range(12):
        libraries.remember(home / f"lib{index}")
    data = libraries.load()
    assert data["current"] == str((home / "lib11").resolve())
    assert len(data["recent"]) == libraries.RECENT and data["recent"][0] == data["current"]
    raw = json.loads(libraries.registry_path().read_text(encoding="utf-8"))
    assert set(raw) == {"version", "current", "recent"}  # Paths only, nothing else.


def test_startup_opens_the_only_library_or_asks_when_several(home):
    fresh = libraries.startup_library(schema=SCHEMA_VERSION)
    assert fresh.path == libraries.default_new() and fresh.database_identity is None
    legacy = make_library(home / "legacy")
    assert libraries.startup_library(schema=SCHEMA_VERSION).path == legacy
    make_library(libraries.default_new())
    assert libraries.startup_library(schema=SCHEMA_VERSION) is None  # Two found: ask.
    libraries.remember(legacy)
    assert (
        libraries.startup_library(schema=SCHEMA_VERSION).path == legacy
    )  # Remembered choice wins.
    assert libraries.startup_library(force=True, schema=SCHEMA_VERSION) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows packaged-app redirection")
def test_discovery_finds_libraries_redirected_by_packaged_apps(home):
    redirected = make_library(
        home / "local" / "Packages" / "OpenAI.Codex_abc" / "LocalCache/Local/CBS Transcriber",
        "Interviews",
    )
    assert libraries.candidates() == [redirected]
    facts = libraries.describe(redirected)
    assert facts["kind"] == "App data redirected by OpenAI.Codex"
    assert (facts["projects"], facts["recordings"], facts["schema"]) == (1, 0, SCHEMA_VERSION)


def test_describe_is_read_only(home):
    root = make_library(home / "other", "P")

    def files():
        # SQLite may add its usual -wal/-shm companions for a read-only WAL reader.
        return sorted(p.name for p in root.iterdir() if not p.name.endswith(("-wal", "-shm")))

    before, database = files(), (root / "library.sqlite3").read_bytes()
    libraries.describe(root)
    assert libraries.in_use(root) is False
    assert files() == before and (root / "library.sqlite3").read_bytes() == database


def test_check_validates_libraries_and_new_folders(home):
    root = make_library(home / "existing")
    assert libraries.check(str(root), schema=SCHEMA_VERSION) == root
    with pytest.raises(ValueError, match="absolute"):
        libraries.check("relative", schema=SCHEMA_VERSION)
    with pytest.raises(ValueError, match="No CBS Transcriber library"):
        libraries.check(str(home / "empty"), schema=SCHEMA_VERSION)
    (home / "busy").mkdir()
    (home / "busy" / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="empty or new folder"):
        libraries.check(str(home / "busy"), create=True, schema=SCHEMA_VERSION)
    new = libraries.check(str(home / "fresh"), create=True, schema=SCHEMA_VERSION)
    assert new == (home / "fresh").resolve() and not new.exists()
    with pytest.raises(ValueError, match="newer"):
        libraries.check(str(root), schema=SCHEMA_VERSION - 1)
    with Store(root).library_lock():
        with pytest.raises(ValueError, match="already open"):
            libraries.check(str(root), schema=SCHEMA_VERSION)


def test_new_libraries_are_refused_in_synced_folders(home, monkeypatch):
    monkeypatch.setenv("OneDriveCommercial", str(home / "OneDrive - CBS"))
    with pytest.raises(ValueError, match="Cloud-synced"):
        libraries.check(str(home / "OneDrive - CBS" / "Lib"), create=True, schema=SCHEMA_VERSION)


def test_switch_endpoint_hands_the_choice_to_the_launcher(home, credentials):
    current = make_library(home / "current")
    other = make_library(home / "other", "Elsewhere")
    app = create_app(current, start_worker=False, credentials=credentials)
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        listed = client.get("/api/libraries").json()
        assert listed["current"] == str(current) and listed["can_switch"] is False
        assert listed["libraries"][0]["current"] is True
        assert client.post("/api/libraries/open", json={"path": str(other)}).status_code == 409
        chosen = []
        app.state.switch_library = chosen.append
        same = client.post("/api/libraries/open", json={"path": str(current)})
        assert same.status_code == 400 and "already open" in same.json()["detail"]
        recording = app.state.store.create_recording(
            app.state.store.create_project("Busy")["id"], "a.wav"
        )
        job = app.state.store.add_job(recording["id"], "transcribe", {})
        blocked = client.post("/api/libraries/open", json={"path": str(other)})
        assert blocked.status_code == 409 and "jobs" in blocked.json()["detail"]
        app.state.store.update_job(job["id"], state="cancelled")
        opened = client.post("/api/libraries/open", json={"path": str(other)})
        assert opened.status_code == 202 and [choice.path for choice in chosen] == [other]
        # Cross-site pages cannot switch the library.
        forged = client.post(
            "/api/libraries/open",
            json={"path": str(other)},
            headers={"Origin": "https://example.org"},
        )
        assert forged.status_code == 403


def test_chooser_app_lists_and_hands_over_a_choice(home):
    root = make_library(home / "legacy", "Old")
    make_library(libraries.default_new())
    app = create_chooser_app()
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        assert "Choose a library" in client.get("/").text
        listed = client.get("/api/libraries").json()
        assert {item["path"] for item in listed["libraries"]} == {
            str(root),
            str(libraries.default_new().resolve()),
        }
        assert client.post("/api/libraries/open", json={"path": str(root)}).status_code == 409
        chosen = []
        app.state.choose = chosen.append
        response = client.post("/api/libraries/open", json={"path": str(root)})
        assert response.status_code == 202 and [choice.path for choice in chosen] == [root]
        bad = client.post("/api/libraries/open", json={"path": str(home / "nothing")})
        assert bad.status_code == 400


def test_newer_schema_is_never_opened_or_migrated(home):
    root = make_library(home / "future")
    with sqlite3.connect(root / "library.sqlite3") as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(ValueError, match="newer"):
        libraries.check(str(root), schema=SCHEMA_VERSION)

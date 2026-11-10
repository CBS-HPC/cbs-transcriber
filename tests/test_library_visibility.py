"""Hiding a library is reversible and never removes its database or work."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber import libraries, storage
from cbs_transcriber.app import create_app
from cbs_transcriber.store import SCHEMA_VERSION, Store


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(storage, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "redirected_roots", lambda: [])
    return tmp_path


def test_hidden_legacy_library_stays_recoverable_and_does_not_become_new_default(home):
    old = Store(home / "legacy")
    project = old.create_project("Synthetic")
    recording = old.create_recording(project["id"], "synthetic.wav")
    old.add_job(recording["id"], "transcribe", {})
    token = libraries.known()[0]["visibility_token"]
    libraries.remember(old.root)
    libraries.set_hidden(str(old.root), token, hidden=True)
    assert libraries.candidates() == []
    assert libraries.startup_library(schema=SCHEMA_VERSION) is None
    hidden = libraries.known()
    assert len(hidden) == 1 and hidden[0]["hidden"] is True
    assert old.db_path.exists() and len(old.all("SELECT id FROM jobs")) == 1
    libraries.set_hidden(str(old.root), token, hidden=False)
    assert libraries.startup_library(schema=SCHEMA_VERSION).path == old.root


def test_hide_requires_current_identity_and_never_hides_open_or_unknown_library(home, credentials):
    current = Store(home / "current")
    other = Store(home / "legacy")
    app = create_app(current.root, start_worker=False, credentials=credentials)
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        listed = client.get("/api/libraries").json()["libraries"]
        current_row = next(row for row in listed if row["current"])
        other_row = next(row for row in listed if row["path"] == str(other.root))

        def save(row, **kw):
            return client.post(
                "/api/libraries/visibility",
                json={"path": row["path"], "token": row["visibility_token"], "hidden": True, **kw},
            )

        assert save(current_row).status_code == 400
        assert save(other_row, token="0" * 64).status_code == 400
        with other.library_lock():
            assert save(other_row).status_code == 400
        assert save(other_row).status_code == 200
        refreshed = client.get("/api/libraries").json()["libraries"]
        assert next(row for row in refreshed if row["path"] == str(other.root))["hidden"] is True
        forged = client.post(
            "/api/libraries/visibility",
            json={"path": str(other.root), "token": other_row["visibility_token"], "hidden": False},
            headers={"Origin": "https://example.org"},
        )
        assert forged.status_code == 403


def test_invalid_visibility_file_never_hides_existing_libraries_or_gets_overwritten(home):
    root = Store(home / "legacy").root
    path = libraries.visibility_path()
    path.parent.mkdir(parents=True)
    path.write_text("{bad", encoding="utf-8")
    assert libraries.candidates() == [root]
    token = libraries.known()[0]["visibility_token"]
    with pytest.raises(ValueError, match="could not be read"):
        libraries.set_hidden(str(root), token, hidden=True)
    assert path.read_text(encoding="utf-8") == "{bad"


def test_visibility_pending_collision_never_deletes_existing_file(home, monkeypatch):
    root = Store(home / "legacy").root
    token = libraries.known()[0]["visibility_token"]
    path = libraries.visibility_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + "." + "c" * 32 + ".pending")
    pending.write_text("preserve existing file", encoding="utf-8")
    monkeypatch.setattr(libraries.uuid, "uuid4", lambda: SimpleNamespace(hex="c" * 32))
    with pytest.raises(FileExistsError):
        libraries.set_hidden(str(root), token, hidden=True)
    assert pending.read_text(encoding="utf-8") == "preserve existing file"


def test_replaced_library_at_same_path_is_not_hidden(home):
    original = Store(home / "legacy").root
    token = libraries.known()[0]["visibility_token"]
    libraries.set_hidden(str(original), token, hidden=True)
    original.rename(home / "moved")
    replacement = Store(original).root
    assert libraries.candidates() == [replacement]
    assert libraries.known()[0]["hidden"] is False
    with pytest.raises(ValueError, match="changed"):
        libraries.set_hidden(str(replacement), token, hidden=True)


def test_hidden_recent_library_remains_recoverable_after_recent_list_rolls_over(home):
    root = Store(home / "other").root
    libraries.register(root)
    token = libraries.known()[0]["visibility_token"]
    libraries.set_hidden(str(root), token, hidden=True)
    for index in range(libraries.RECENT + 1):
        libraries.remember(home / f"new-{index}")
    assert str(root) not in libraries.load()["recent"]
    assert any(row["path"] == str(root) and row["hidden"] for row in libraries.known())


def test_target_lease_is_held_through_visibility_write_even_if_status_check_races(
    home, monkeypatch
):
    other = Store(home / "legacy")
    token = libraries.known()[0]["visibility_token"]
    monkeypatch.setattr(libraries, "in_use", lambda _path: False)
    with other.library_lock(), pytest.raises(ValueError, match="already running"):
        libraries.set_hidden(str(other.root), token, hidden=True)
    assert not libraries.visibility_path().exists()

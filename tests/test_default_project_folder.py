"""Library and projects default to one visible, unsynced folder in the home directory."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber import storage
from cbs_transcriber.app import create_app


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A synthetic home folder, with the earlier hidden app-data default beside it."""
    monkeypatch.delenv("CBS_TRANSCRIBER_PROJECTS_DIR", raising=False)
    monkeypatch.delenv("CBS_TRANSCRIBER_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    legacy = tmp_path / "AppData" / "Local" / "CBS Transcriber"
    monkeypatch.setattr(storage, "legacy_library_root", lambda: legacy)
    return tmp_path / "home", legacy


def library(folder):
    folder.mkdir(parents=True)
    sqlite3.connect(folder / "library.sqlite3").close()


@pytest.mark.parametrize(
    "user_home", ["C:/Users/researcher", "/Users/researcher"], ids=["windows", "macos"]
)
def test_one_visible_folder_holds_projects_and_library(monkeypatch, user_home):
    monkeypatch.delenv("CBS_TRANSCRIBER_PROJECTS_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path(user_home))
    monkeypatch.setattr(storage, "legacy_library_root", lambda: Path(user_home) / "hidden")
    folder = Path(user_home) / "CBS Transcriber"
    assert storage.default_project_parent() == folder / "Projects"
    assert storage.default_library_root() == folder / "Library"
    # Not inside Documents/Desktop, which OneDrive and iCloud commonly sync.
    assert "Documents" not in folder.parts and "Desktop" not in folder.parts


def test_projects_override_still_wins(home, tmp_path, monkeypatch):
    monkeypatch.setenv("CBS_TRANSCRIBER_PROJECTS_DIR", str(tmp_path / "chosen"))
    assert storage.default_project_parent() == (tmp_path / "chosen").resolve()


def test_an_existing_library_is_used_where_it_is_and_never_moved(home):
    user_home, legacy = home
    library(legacy)
    assert storage.default_library_root() == legacy
    assert not (user_home / "CBS Transcriber").exists()
    # Once a library exists in the new place, it is the default.
    library(user_home / "CBS Transcriber" / "Library")
    assert storage.default_library_root() == user_home / "CBS Transcriber" / "Library"


def test_create_app_opens_the_friendly_library_by_default(home, credentials):
    user_home, legacy = home
    app = create_app(start_worker=False, credentials=credentials)
    assert app.state.store.root == (user_home / "CBS Transcriber" / "Library").resolve()
    assert not legacy.exists()


def test_launcher_default_is_used_for_new_projects_only(tmp_path, credentials):
    parent = tmp_path / "Friendly projects"
    app = create_app(
        tmp_path / "library", start_worker=False, credentials=credentials, project_parent=parent
    )
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        options = client.get("/api/storage").json()
        assert options["default_project_parent"] == str(parent)
        assert options["default_parent_synced"] is False
        project = client.post(
            "/api/projects", json={"name": "Interviews", "classification": "standard"}
        ).json()
        folder = app.state.store.project_folder(project["id"])
        assert folder.parent == parent.resolve() and folder.is_dir()


def test_relative_project_folders_become_absolute(tmp_path, monkeypatch):
    import sys

    from cbs_transcriber import __main__ as launcher

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CBS_TRANSCRIBER_DATA_DIR", raising=False)
    monkeypatch.setenv("CBS_TRANSCRIBER_PROJECTS_DIR", "relative projects")
    assert storage.default_project_parent() == tmp_path.resolve() / "relative projects"
    chosen = []

    class Stop(Exception):
        pass

    def refuse_discovery(**kwargs):
        pytest.fail("A project-path test must not discover the user's libraries.")

    def capture(data_dir, project_parent, *, release):
        assert data_dir == tmp_path.resolve() / "library"
        assert release.name == "full"
        chosen.append(project_parent)
        raise Stop

    monkeypatch.setattr(launcher.libraries, "startup_library", refuse_discovery)
    monkeypatch.setattr(launcher, "create_app", capture)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cbs-transcriber", "--data-dir", str(tmp_path / "library"), "--projects-dir", "mine"],
    )
    try:
        launcher.main()
    except Stop:
        pass
    assert chosen == [tmp_path.resolve() / "mine"] and chosen[0].is_absolute()

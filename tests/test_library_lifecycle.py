"""Synthetic library handoff races; no serving socket or background worker is started."""

import errno
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber import __main__ as launcher
from cbs_transcriber import libraries
from cbs_transcriber.store import SCHEMA_VERSION, Store


def move_after_readers_close(source, destination):
    # Windows can briefly deny rename after the closed SQLite inspection. The race under
    # test starts only once this synthetic move succeeds; do not turn access denial into it.
    for attempt in range(21):
        try:
            source.rename(destination)
            return
        except PermissionError:
            if attempt == 20:
                raise
            Event().wait(0.05)


@pytest.mark.parametrize("replacement", [False, True])
def test_existing_library_moved_after_switch_acceptance_never_becomes_a_new_library(
    tmp_path, monkeypatch, credentials, replacement
):
    current, target, preserved = (tmp_path / name for name in ("current", "target", "preserved"))
    Store(current).create_project("Current synthetic project")
    Store(target).create_project("Chosen synthetic project")
    original = (target / libraries.DATABASE).read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cbs-transcriber",
            "--no-browser",
            "--data-dir",
            str(current),
            "--projects-dir",
            str(tmp_path / "projects"),
        ],
    )
    monkeypatch.setattr(libraries, "registry_path", lambda: tmp_path / "libraries.json")
    monkeypatch.setattr(launcher, "legacy_library_root", lambda: tmp_path / "legacy")
    create_app = launcher.create_app
    monkeypatch.setattr(
        launcher,
        "create_app",
        lambda *args, **kwargs: create_app(
            *args, **kwargs, start_worker=False, credentials=credentials
        ),
    )
    monkeypatch.setattr(
        launcher,
        "server_for",
        lambda app, port: SimpleNamespace(app=app, config=SimpleNamespace(), should_exit=False),
    )
    observed = []

    def run(server, started):
        with TestClient(server.app, headers={"X-CBS-Request": "1"}) as client:
            started()
            observed.append(server.app.state.store.root)
            if len(observed) == 1:
                response = client.post("/api/libraries/open", json={"path": str(target)})
                assert response.status_code == 202, response.text
                move_after_readers_close(target, preserved)
                if replacement:
                    Store(target).create_project("Unselected replacement")
        return True

    monkeypatch.setattr(launcher, "run", run)
    launcher.main()

    assert observed == [current, current]
    assert (preserved / libraries.DATABASE).read_bytes() == original
    if replacement:
        assert Store(target).all("SELECT name FROM projects") == [
            {"name": "Unselected replacement"}
        ]
    else:
        assert not target.exists()


@pytest.mark.parametrize("replacement", [False, True])
def test_initial_discovery_retains_identity_before_returning_to_launcher(
    tmp_path, monkeypatch, replacement
):
    target, preserved = tmp_path / "chosen", tmp_path / "preserved"
    Store(target).create_project("Selected synthetic project")
    original = (target / libraries.DATABASE).read_bytes()
    monkeypatch.delenv("CBS_TRANSCRIBER_DATA_DIR", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cbs-transcriber", "--no-browser", "--projects-dir", str(tmp_path / "projects")],
    )
    monkeypatch.setattr(libraries, "registry_path", lambda: tmp_path / "libraries.json")
    monkeypatch.setattr(libraries, "default_new", lambda: tmp_path / "default")
    monkeypatch.setattr(libraries, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "redirected_roots", lambda: [])
    monkeypatch.setattr(launcher, "legacy_library_root", lambda: tmp_path / "legacy")
    libraries.remember(target)
    discover = libraries.startup_library

    def moved_after_discovery(**kwargs):
        selected = discover(**kwargs)
        move_after_readers_close(target, preserved)
        if replacement:
            Store(target).create_project("Unselected replacement")
        return selected

    monkeypatch.setattr(libraries, "startup_library", moved_after_discovery)
    opened = []

    def create(path, **kwargs):
        store = Store(path)
        opened.append(store.all("SELECT name FROM projects"))
        return SimpleNamespace(state=SimpleNamespace(store=store))

    monkeypatch.setattr(launcher, "create_app", create)
    monkeypatch.setattr(
        launcher, "server_for", lambda *args: SimpleNamespace(config=SimpleNamespace())
    )
    monkeypatch.setattr(launcher, "run", lambda server, started: (started(), True)[1])
    with pytest.raises(SystemExit):
        launcher.main()
    assert not opened
    assert (preserved / libraries.DATABASE).read_bytes() == original
    if replacement:
        assert Store(target).all("SELECT name FROM projects") == [
            {"name": "Unselected replacement"}
        ]
    else:
        assert not target.exists()


@pytest.mark.parametrize("occupation", ["file", "library", "replaced-empty-directory"])
def test_new_library_selection_never_adopts_a_later_occupant(tmp_path, occupation):
    target = tmp_path / "new"
    target.mkdir()
    selected = libraries.select(str(target), create=True, schema=SCHEMA_VERSION)
    if occupation == "file":
        (target / "unrelated.txt").write_text("Keep this exact synthetic text")
    elif occupation == "library":
        Store(target).create_project("Not selected")
    else:
        target.rename(tmp_path / "preserved-empty")
        target.mkdir()
    with pytest.raises(ValueError):
        libraries.recheck(selected, schema=SCHEMA_VERSION)
    if occupation == "file":
        assert (target / "unrelated.txt").read_text() == "Keep this exact synthetic text"
    elif occupation == "library":
        assert Store(target).all("SELECT name FROM projects") == [{"name": "Not selected"}]
    else:
        assert not list(target.iterdir())


def test_unchanged_selection_and_new_creation_recheck_without_writes(tmp_path):
    target = tmp_path / "existing"
    store = Store(target)
    selected = libraries.select(str(target), schema=SCHEMA_VERSION)
    before = store.db_path.read_bytes()
    assert libraries.recheck(selected, schema=SCHEMA_VERSION) == target
    assert store.db_path.read_bytes() == before
    with store.library_lock():
        with pytest.raises(ValueError, match="already open"):
            libraries.recheck(selected, schema=SCHEMA_VERSION)
    empty = tmp_path / "new"
    selected = libraries.select(str(empty), create=True, schema=SCHEMA_VERSION)
    assert libraries.recheck(selected, schema=SCHEMA_VERSION) == empty
    assert not empty.exists()


def test_database_only_replacement_is_not_adopted_or_modified(tmp_path):
    target = tmp_path / "existing"
    store = Store(target)
    selected = libraries.select(str(target), schema=SCHEMA_VERSION)
    original = store.db_path.read_bytes()
    saved = tmp_path / "saved-original.sqlite3"
    store.db_path.rename(saved)
    replacement = Store(target)
    replacement.create_project("Unselected replacement database")
    before = replacement.db_path.read_bytes()
    with pytest.raises(ValueError, match="changed"):
        libraries.recheck(selected, schema=SCHEMA_VERSION)
    assert saved.read_bytes() == original
    assert replacement.db_path.read_bytes() == before


@pytest.mark.parametrize("operation", [libraries.remember, libraries.register])
def test_registry_replace_failure_preserves_the_previous_selection(
    tmp_path, monkeypatch, operation
):
    registry = tmp_path / "libraries.json"
    monkeypatch.setattr(libraries, "registry_path", lambda: registry)
    libraries.remember(tmp_path / "previous")
    before, previous = registry.read_bytes(), libraries.load()
    replace = Path.replace

    def denied(path, target):
        if path == registry.with_suffix(".pending"):
            raise PermissionError("Synthetic registry replacement denied")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(PermissionError):
        operation(tmp_path / "new")
    assert registry.read_bytes() == before
    assert libraries.load() == previous


def test_unavailable_remembered_candidate_does_not_hide_a_healthy_library(tmp_path, monkeypatch):
    unavailable, healthy = tmp_path / "unavailable", tmp_path / "healthy"
    first = Store(unavailable)
    Store(healthy).create_project("Available synthetic library")
    before = first.db_path.read_bytes()
    monkeypatch.setattr(libraries, "registry_path", lambda: tmp_path / "libraries.json")
    monkeypatch.setattr(libraries, "default_new", lambda: tmp_path / "default")
    monkeypatch.setattr(libraries, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "redirected_roots", lambda: [])
    libraries.remember(unavailable)
    libraries.register(healthy)
    stat = Path.stat

    def denied(path, *args, **kwargs):
        if path == first.db_path:
            raise PermissionError(errno.EACCES, "Synthetic metadata access denied")
        return stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    assert libraries.startup_library(schema=SCHEMA_VERSION).path == healthy
    assert libraries.candidates() == [healthy]
    assert libraries.load()["current"] == str(unavailable)
    assert first.db_path.read_bytes() == before


@pytest.mark.parametrize("failure", [FileNotFoundError, PermissionError])
@pytest.mark.parametrize("fail_on", [1, 2])
def test_listing_survives_database_stat_failure_after_presence_check(
    tmp_path, monkeypatch, failure, fail_on
):
    target = tmp_path / "unavailable"
    store = Store(target)
    original = store.db_path.read_bytes()
    stat = Path.stat
    observed = 0

    def transient_stat(path, *args, **kwargs):
        nonlocal observed
        if path == store.db_path:
            observed += 1
            if observed == fail_on:
                raise failure("Synthetic metadata read failure")
        return stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", transient_stat)
    facts = libraries.describe(target)
    assert facts.get("unreadable") or facts["exists"] is False or facts["schema"] == SCHEMA_VERSION
    assert store.db_path.read_bytes() == original

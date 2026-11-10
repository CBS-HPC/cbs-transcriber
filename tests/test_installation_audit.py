"""Offline launcher failures must not prevent opening an otherwise ready app."""

import json
import sys
from types import SimpleNamespace

import pytest

from cbs_transcriber import __main__ as launcher
from cbs_transcriber import libraries
from cbs_transcriber.store import Store


@pytest.mark.parametrize("value", [None, [], "registry", 42])
def test_non_object_registry_falls_back_without_startup_exception(tmp_path, monkeypatch, value):
    path = tmp_path / "CBS Transcriber æ" / "libraries.json"
    path.parent.mkdir()
    path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(libraries, "registry_path", lambda: path)

    assert libraries.load() == {"current": None, "recent": []}


@pytest.mark.parametrize("value", [None, 42, "not a path list", {"wrong": "shape"}])
def test_non_list_recent_registry_preserves_valid_current(tmp_path, monkeypatch, value):
    current = str(tmp_path / "Research library øæå")
    path = tmp_path / "libraries.json"
    path.write_text(json.dumps({"current": current, "recent": value}), encoding="utf-8")
    monkeypatch.setattr(libraries, "registry_path", lambda: path)

    assert libraries.load() == {"current": current, "recent": []}


@pytest.mark.parametrize("no_browser", [False, True])
def test_registry_write_failure_does_not_block_ready_launcher(
    tmp_path, monkeypatch, capsys, no_browser
):
    root = tmp_path / "Library with spaces æ"
    Store(root)
    projects = tmp_path / "Projects with spaces ø"
    app = SimpleNamespace(state=SimpleNamespace(store=SimpleNamespace(root=root)))
    opened = []
    monkeypatch.delenv("CBS_TRANSCRIBER_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["cbs-transcriber", *(["--no-browser"] if no_browser else [])])
    monkeypatch.setattr(launcher, "default_project_parent", lambda: projects)
    monkeypatch.setattr(launcher, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(
        libraries,
        "startup_library",
        lambda **kwargs: libraries.select(str(root), schema=kwargs["schema"]),
    )
    monkeypatch.setattr(launcher, "create_app", lambda *args, **kwargs: app)
    monkeypatch.setattr(launcher, "server_for", lambda *args: SimpleNamespace())
    monkeypatch.setattr(launcher, "run", lambda server, started: (started(), True)[1])
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))

    def readonly_registry(_root):
        raise PermissionError("Synthetic registry write denied")

    monkeypatch.setattr(libraries, "remember", readonly_registry)
    launcher.main()

    assert opened == ([] if no_browser else ["http://127.0.0.1:8765"])
    assert "could not be remembered" in capsys.readouterr().out
    assert app.state.store.root == root

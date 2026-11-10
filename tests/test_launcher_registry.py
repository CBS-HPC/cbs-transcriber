"""One-off --data-dir launches never change which library opens by default (ADR 0051)."""

import sys
from pathlib import Path

import pytest

from cbs_transcriber import __main__ as launcher
from cbs_transcriber import libraries, storage


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("CBS_TRANSCRIBER_DATA_DIR", raising=False)
    monkeypatch.setattr(storage, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(launcher, "legacy_library_root", lambda: tmp_path / "legacy")
    # Serve nothing: report a started server once, as uvicorn would, then stop.
    monkeypatch.setattr(launcher, "run", lambda server, started: (started(), True)[1])
    return tmp_path


def launch(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["cbs-transcriber", "--no-browser", *arguments])
    launcher.main()


def test_explicit_data_dir_leaves_the_registry_alone(home, monkeypatch):
    launch(monkeypatch, "--data-dir", str(home / "throwaway"))
    assert not libraries.registry_path().exists()


def test_discovered_library_is_remembered(home, monkeypatch):
    launch(monkeypatch)
    assert libraries.load()["current"] == str(libraries.default_new().resolve())

"""Native chooser contracts without opening operating-system dialogs."""

import subprocess
from types import SimpleNamespace

import pytest

from cbs_transcriber import storage


def platform(monkeypatch, name, system):
    monkeypatch.setattr(storage, "sys", SimpleNamespace(platform=name))
    monkeypatch.setattr(storage, "os", SimpleNamespace(name=system))


def test_macos_chooser_preserves_unicode_and_path_spaces(monkeypatch):
    platform(monkeypatch, "darwin", "posix")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="/Users/researcher/Interviews København /\n")

    monkeypatch.setattr(storage.subprocess, "run", run)
    assert storage.choose_folder() == "/Users/researcher/Interviews København /"
    command, options = calls[0]
    assert command[:2] == ["osascript", "-e"]
    assert "return POSIX path of (choose folder" in command[2]
    assert "on error number -128" in command[2]
    assert options == {
        "capture_output": True,
        "encoding": "utf-8",
        "timeout": 300,
        "creationflags": 0,
    }


def test_macos_cancel_returns_no_folder(monkeypatch):
    platform(monkeypatch, "darwin", "posix")
    monkeypatch.setattr(
        storage.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="\n"),
    )
    assert storage.choose_folder() is None


@pytest.mark.parametrize("failure", ["missing", "timeout", "nonzero"])
def test_macos_chooser_errors_are_curated(monkeypatch, failure):
    platform(monkeypatch, "darwin", "posix")

    def run(*args, **kwargs):
        if failure == "missing":
            raise FileNotFoundError("synthetic private path")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("synthetic private path", 300)
        return SimpleNamespace(returncode=1, stdout="synthetic private path")

    monkeypatch.setattr(storage.subprocess, "run", run)
    with pytest.raises(ValueError) as error:
        storage.choose_folder()
    assert str(error.value) == (
        "Folder chooser unavailable. Enter the absolute folder path instead."
    )


def test_windows_chooser_keeps_hidden_powershell_contract(monkeypatch):
    platform(monkeypatch, "win32", "nt")
    monkeypatch.setattr(storage.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="C:\\Research\\København")

    monkeypatch.setattr(storage.subprocess, "run", run)
    assert storage.choose_folder() == "C:\\Research\\København"
    command, options = calls[0]
    assert command[:6] == [
        "powershell.exe",
        "-NoProfile",
        "-STA",
        "-WindowStyle",
        "Hidden",
        "-Command",
    ]
    assert "System.Windows.Forms.FolderBrowserDialog" in command[6]
    assert options["creationflags"] == 0x08000000


def test_other_platform_keeps_manual_path_fallback(monkeypatch):
    platform(monkeypatch, "linux", "posix")
    with pytest.raises(ValueError, match="Enter an absolute folder path on this platform"):
        storage.choose_folder()

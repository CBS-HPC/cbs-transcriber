"""Temporary source launchers only; never install a shortcut or start a real server."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import __main__ as launcher
from cbs_transcriber import shortcuts


@pytest.fixture
def installation(tmp_path, monkeypatch):
    source = tmp_path / "CBS App æ O'Brien $cash &"
    package = source / "src/cbs_transcriber"
    package.mkdir(parents=True)
    (source / "pyproject.toml").touch()
    (source / "uv.lock").touch()
    for executable in ("Scripts/python.exe", "bin/python"):
        python = source / ".venv" / executable
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
    home = tmp_path / "Home ø"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(shortcuts, "__file__", str(package / "shortcuts.py"))
    monkeypatch.setattr(sys, "prefix", str(source / ".venv"))
    return source, home


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_source_shortcut_requires_its_installed_environment(installation, monkeypatch, platform):
    source, home = installation
    monkeypatch.setattr(sys, "platform", platform)
    expected = source / ".venv" / ("Scripts/python.exe" if platform == "win32" else "bin/python")
    assert shortcuts._installed_source() == (source.resolve(), expected)
    monkeypatch.setattr(sys, "prefix", str(source / "another-environment"))
    with pytest.raises(ValueError, match="local .venv must already be installed"):
        shortcuts.create_shortcut([])
    assert not home.exists()


@pytest.mark.parametrize("missing", ["pyproject.toml", "uv.lock", ".venv/bin/python"])
def test_incomplete_source_installation_is_refused(installation, monkeypatch, missing):
    source, home = installation
    monkeypatch.setattr(sys, "platform", "darwin")
    (source / missing).unlink()
    with pytest.raises(ValueError, match="local .venv must already be installed"):
        shortcuts.create_shortcut([])
    assert not home.exists()


@pytest.mark.parametrize(
    "location,folder", [("applications", "Applications"), ("desktop", "Desktop")]
)
def test_mac_launcher_quotes_exact_command_and_never_overwrites(
    installation, monkeypatch, location, folder
):
    source, home = installation
    monkeypatch.setattr(sys, "platform", "darwin")
    arguments = ["--data-dir", str(home / "Library 'ø' $data &"), "--no-browser"]
    target = shortcuts.create_shortcut(arguments, location=location)
    assert target == home / folder / "CBS Transcriber.command"
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#!/bin/sh"
    assert shlex.split(lines[1]) == ["cd", str(source), "||", "exit", "1"]
    assert shlex.split(lines[2]) == [
        "exec",
        str(source / ".venv/bin/python"),
        "-m",
        "cbs_transcriber",
        *arguments,
    ]
    assert b"\r" not in target.read_bytes()
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o700
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        shortcuts.create_shortcut(["--choose-library"], location=location)
    assert target.read_bytes() == before


def test_mac_existing_directory_is_also_retained(installation, monkeypatch):
    _, home = installation
    monkeypatch.setattr(sys, "platform", "darwin")
    target = home / "Applications/CBS Transcriber.command"
    target.mkdir(parents=True)
    # Windows reports EACCES here; mocking macOS does not change the host filesystem.
    with pytest.raises(OSError):
        shortcuts.create_shortcut([])
    assert target.is_dir()


@pytest.mark.parametrize("location,folder", [("applications", "Programs"), ("desktop", "Desktop")])
def test_windows_uses_native_user_folders_and_json_not_shell_paths(
    installation, monkeypatch, location, folder
):
    source, home = installation
    monkeypatch.setattr(sys, "platform", "win32")
    arguments = ["--data-dir", str(home / "Library ø $data &"), "--no-browser"]
    destination = home / "Redirected user folder æ/CBS Transcriber.lnk"
    calls = []

    def native(command, **options):
        calls.append((command, options))
        return SimpleNamespace(stdout=json.dumps({"path": str(destination), "exists": False}))

    monkeypatch.setattr(subprocess, "run", native)
    assert shortcuts.create_shortcut(arguments, location=location) == destination
    command, options = calls[0]
    assert command == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        shortcuts._WINDOWS_SCRIPT,
    ]
    request = json.loads(options["input"])
    assert options["input"].isascii()
    assert request == {
        "folder": folder,
        "source": str(source),
        "python": str(source / ".venv/Scripts/python.exe"),
        "arguments": f'-m cbs_transcriber --data-dir "{arguments[1]}" --no-browser',
    }
    assert str(source) not in command[-1]
    assert options["check"] and options["timeout"] == 30
    assert options["encoding"] == "utf-8"
    assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert "shell" not in options
    # The fixed script publishes with the no-overwrite Move overload, after saving a temporary.
    assert "$shell.SpecialFolders.Item($request.folder)" in command[-1]
    assert "$shell.CreateShortcut($temporary)" in command[-1]
    assert "[IO.File]::Move($temporary, $destination)" in command[-1]
    assert not home.exists()


def test_windows_existing_shortcut_is_refused(installation, monkeypatch):
    _, home = installation
    monkeypatch.setattr(sys, "platform", "win32")
    destination = home / "CBS Transcriber.lnk"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps({"path": str(destination), "exists": True})
        ),
    )
    with pytest.raises(FileExistsError, match="Remove it first"):
        shortcuts.create_shortcut([])
    assert not home.exists()


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError(),
        subprocess.CalledProcessError(1, "powershell"),
        subprocess.TimeoutExpired("powershell", 30),
    ],
)
def test_windows_native_failure_is_actionable(installation, monkeypatch, failure):
    monkeypatch.setattr(sys, "platform", "win32")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(ValueError, match="CBS device policy"):
        shortcuts.create_shortcut([])


@pytest.mark.parametrize("platform,location", [("linux", "applications"), ("darwin", "other")])
def test_unsupported_requests_write_nothing(installation, monkeypatch, platform, location):
    _, home = installation
    monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(ValueError):
        shortcuts.create_shortcut([], location=location)
    assert not home.exists()


def refuse_startup(*args, **kwargs):
    pytest.fail("Shortcut creation must exit before any library/browser/server work")


@pytest.mark.parametrize("custom", [False, True])
def test_cli_creation_exits_before_library_resolution_and_freezes_only_launch_arguments(
    tmp_path, monkeypatch, capsys, custom
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CBS_TRANSCRIBER_DATA_DIR", "ambient library is never captured")
    for owner, name in (
        (launcher.libraries, "startup_library"),
        (launcher, "default_project_parent"),
        (launcher, "create_app"),
        (launcher, "create_chooser_app"),
        (launcher, "server_for"),
        (launcher.webbrowser, "open"),
    ):
        monkeypatch.setattr(owner, name, refuse_startup)
    extra = (
        [
            "--shortcut-location",
            "desktop",
            "--port",
            "9876",
            "--no-browser",
            "--choose-library",
            "--data-dir",
            "Library æ",
            "--projects-dir",
            "Projects ø",
        ]
        if custom
        else []
    )
    monkeypatch.setattr(sys, "argv", ["cbs-transcriber", "--create-shortcut", *extra])
    calls = []

    def create(arguments, *, location):
        calls.append((arguments, location))
        return tmp_path / "test launcher"

    monkeypatch.setattr(shortcuts, "create_shortcut", create)
    launcher.main()
    assert calls == [
        (
            [
                "--port",
                "9876",
                "--data-dir",
                str(tmp_path / "Library æ"),
                "--projects-dir",
                str(tmp_path / "Projects ø"),
                "--choose-library",
                "--no-browser",
            ]
            if custom
            else ["--port", "8765"],
            "desktop" if custom else "applications",
        )
    ]
    assert "Created shortcut:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments", [["--shortcut-location", "desktop"], ["--create-shortcut", "--port", "0"]]
)
def test_cli_invalid_options_never_create_or_start(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["cbs-transcriber", *arguments])
    monkeypatch.setattr(shortcuts, "create_shortcut", refuse_startup)
    monkeypatch.setattr(launcher.libraries, "startup_library", refuse_startup)
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 2


def test_cli_shortcut_error_exits_without_startup(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cbs-transcriber", "--create-shortcut"])
    monkeypatch.setattr(launcher.libraries, "startup_library", refuse_startup)

    def fail(*args, **kwargs):
        raise PermissionError("Synthetic shortcut folder is read-only")

    monkeypatch.setattr(shortcuts, "create_shortcut", fail)
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 2
    assert "Synthetic shortcut folder is read-only" in capsys.readouterr().err

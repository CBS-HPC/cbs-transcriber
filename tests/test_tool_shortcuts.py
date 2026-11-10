"""Installed-layout fixtures and mocked OS calls; never touch a real user's launchers."""

import json
import shlex
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, PathDistribution
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import __main__ as launcher
from cbs_transcriber import shortcuts


@pytest.fixture
def tool_installation(tmp_path, monkeypatch):
    environment = tmp_path / "Tool env æ O'Brien $cash &"
    site = environment / "Lib/site-packages"
    package = site / "cbs_transcriber"
    package.mkdir(parents=True)
    for name in ("shortcuts.py", "__main__.py"):
        (package / name).touch()
    for name in ("Scripts/python.exe", "bin/python"):
        python = environment / name
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
    dist_info = site / "cbs_transcriber-0.1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Name: cbs-transcriber\nVersion: 0.1.0\n", "utf-8")
    (dist_info / "RECORD").write_text(
        "cbs_transcriber/shortcuts.py,,\ncbs_transcriber/__main__.py,,\n", "utf-8"
    )
    installed = PathDistribution(dist_info)

    def distribution(name):
        assert name == "cbs-transcriber"
        return installed

    monkeypatch.setattr(shortcuts, "distribution", distribution, raising=False)
    monkeypatch.setattr(shortcuts, "__file__", str(package / "shortcuts.py"))
    monkeypatch.setattr(sys, "prefix", str(environment))
    monkeypatch.setattr(sys, "executable", str(environment / "bin/python"))
    home = tmp_path / "Home ø"
    monkeypatch.setattr(Path, "home", lambda: home)
    return environment, package, home, installed


def test_installed_mac_shortcut_uses_exact_interpreter_and_environment_cwd(
    tool_installation, monkeypatch
):
    environment, _, home, _ = tool_installation
    monkeypatch.setattr(sys, "platform", "darwin")
    arguments = ["--port", "9876", "--profile", "mvp", "--data-dir", str(home / "Private 'ø' &")]
    # The venv executable may be a symlink to a base interpreter: never resolve it.
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        assert str(path) != sys.executable
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    target = shortcuts.create_shortcut(arguments)
    assert target == home / "Applications/CBS Transcriber.command"
    lines = target.read_text("utf-8").splitlines()
    assert shlex.split(lines[1]) == ["cd", str(environment), "||", "exit", "1"]
    assert shlex.split(lines[2]) == ["exec", sys.executable, "-m", "cbs_transcriber", *arguments]
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        shortcuts.create_shortcut(["--profile", "full"])
    assert target.read_bytes() == before


def test_installed_windows_shortcut_uses_metadata_and_exact_active_python(
    tool_installation, monkeypatch
):
    environment, _, home, _ = tool_installation
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(environment / "Scripts/python.exe"))
    calls = []

    def native(command, **options):
        calls.append((command, options))
        return SimpleNamespace(stdout=json.dumps({"path": str(home / "app.lnk"), "exists": False}))

    monkeypatch.setattr(subprocess, "run", native)
    arguments = ["--profile", "mvp", "--no-browser", "--data-dir", str(home / "Data ø &")]
    assert shortcuts.create_shortcut(arguments, location="desktop") == home / "app.lnk"
    command, options = calls[0]
    request = json.loads(options["input"])
    assert request == {
        "folder": "Desktop",
        "source": str(environment),
        "python": sys.executable,
        "arguments": subprocess.list2cmdline(["-m", "cbs_transcriber", *arguments]),
    }
    assert command[-1] == shortcuts._WINDOWS_SCRIPT
    assert options["input"].isascii()
    assert not home.exists()


@pytest.mark.parametrize(
    "damage",
    [
        "metadata",
        "module_identity",
        "outside_prefix",
        "outside_interpreter",
        "missing_python",
        "relative_python",
        "missing_main",
        "missing_module",
    ],
)
def test_unverified_installed_layout_never_creates_a_shortcut(
    tool_installation, monkeypatch, tmp_path, damage
):
    environment, package, home, installed = tool_installation
    monkeypatch.setattr(sys, "platform", "darwin")
    if damage == "metadata":

        def absent(name):
            raise PackageNotFoundError(name)

        monkeypatch.setattr(shortcuts, "distribution", absent)
    elif damage == "module_identity":
        monkeypatch.setattr(installed, "locate_file", lambda name: environment / "another.py")
    elif damage == "outside_prefix":
        monkeypatch.setattr(sys, "prefix", str(tmp_path / "another environment"))
    elif damage == "outside_interpreter":
        other = tmp_path / "another python"
        other.touch()
        monkeypatch.setattr(sys, "executable", str(other))
    elif damage == "relative_python":
        monkeypatch.setattr(sys, "executable", "python")
    elif damage == "missing_python":
        Path(sys.executable).unlink()
    else:
        (package / ("__main__.py" if damage == "missing_main" else "shortcuts.py")).unlink()
    with pytest.raises(ValueError, match="installed"):
        shortcuts.create_shortcut([])
    assert not home.exists()


@pytest.mark.parametrize("explicit_profile", [False, True])
def test_tool_shortcut_cli_exits_before_startup_and_captures_no_ambient_environment(
    tool_installation, monkeypatch, capsys, explicit_profile
):
    _, _, home, _ = tool_installation
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("CBS_TRANSCRIBER_DATA_DIR", "AMBIENT_LIBRARY_CANARY")
    monkeypatch.setenv("OPENAI_API_KEY", "AMBIENT_TOKEN_CANARY")
    monkeypatch.setenv("CBS_TRANSCRIBER_PROFILE", "full")

    def refuse(*args, **kwargs):
        pytest.fail("Shortcut creation must not open a library, provider or server")

    for owner, name in (
        (launcher.libraries, "startup_library"),
        (launcher, "recover_library_deletions"),
        (launcher, "default_project_parent"),
        (launcher, "create_app"),
        (launcher, "create_chooser_app"),
        (launcher, "server_for"),
        (launcher.webbrowser, "open"),
    ):
        monkeypatch.setattr(owner, name, refuse)
    arguments = ["--profile", "mvp"] if explicit_profile else []
    monkeypatch.setattr(sys, "argv", ["cbs-transcriber", "--create-shortcut", *arguments])
    launcher.main()
    script = (home / "Applications/CBS Transcriber.command").read_text("utf-8")
    command = shlex.split(script.splitlines()[2])
    assert command[4:] == ["--port", "8765", *arguments]
    assert "AMBIENT_" not in script
    assert "Created shortcut:" in capsys.readouterr().out


def test_installed_shortcut_keeps_same_paths_after_in_place_package_update(
    tool_installation, monkeypatch
):
    environment, package, _, _ = tool_installation
    monkeypatch.setattr(sys, "platform", "darwin")
    target = shortcuts.create_shortcut(["--profile", "mvp"])
    original = target.read_bytes()
    (package / "__main__.py").write_text("# Synthetic updated package\n", "utf-8")
    assert environment.is_dir() and Path(sys.executable).is_file()
    assert shlex.split(target.read_text("utf-8").splitlines()[1])[1] == str(environment)
    with pytest.raises(FileExistsError):
        shortcuts.create_shortcut([])
    assert target.read_bytes() == original

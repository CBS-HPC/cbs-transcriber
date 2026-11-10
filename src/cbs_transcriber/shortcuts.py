"""Explicit per-user launchers for an installed Windows/macOS app environment."""

import json
import os
import shlex
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

# Paths and arguments arrive as ASCII JSON on stdin, never as PowerShell source.
# WSH resolves redirected/localised current-user folders, including a OneDrive Desktop.
_WINDOWS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$request = ConvertFrom-Json ([Console]::In.ReadToEnd())
$shell = New-Object -ComObject WScript.Shell
$folder = $shell.SpecialFolders.Item($request.folder)
if ([string]::IsNullOrWhiteSpace($folder)) { throw 'Shortcut folder is unavailable.' }
[IO.Directory]::CreateDirectory($folder) | Out-Null
$destination = [IO.Path]::Combine($folder, 'CBS Transcriber.lnk')
if (Test-Path -LiteralPath $destination) {
    [Console]::Write((@{path=$destination; exists=$true} | ConvertTo-Json -Compress))
    exit 0
}
$temporary = [IO.Path]::Combine($folder, [Guid]::NewGuid().ToString() + '.lnk')
try {
    $shortcut = $shell.CreateShortcut($temporary)
    $shortcut.TargetPath = $request.python
    $shortcut.Arguments = $request.arguments
    $shortcut.WorkingDirectory = $request.source
    $shortcut.Description = 'CBS Transcriber (keep the terminal open; Ctrl+C stops)'
    $shortcut.Save()
    [IO.File]::Move($temporary, $destination)
} finally {
    [IO.File]::Delete($temporary)
}
[Console]::Write((@{path=$destination; exists=$false} | ConvertTo-Json -Compress))
"""


def _installed_source():
    source = Path(__file__).resolve().parents[2]
    environment = source / ".venv"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if (
        not (source / "pyproject.toml").is_file()
        or not (source / "uv.lock").is_file()
        or Path(sys.prefix).resolve() != environment.resolve()
        or not python.is_file()
    ):
        raise ValueError(
            "Create shortcuts from the installed app folder with uv run cbs-transcriber "
            "--create-shortcut. Its local .venv must already be installed."
        )
    # Keep the venv's executable path: resolving a macOS symlink can bypass the venv.
    return source, python


def _installed_launch():
    try:
        return _installed_source()
    except ValueError as source_error:
        # A wheel has no checkout/uv.lock. Bind its actual distribution to this module
        # and current interpreter, rather than guessing a tool location or invoking uv.
        try:
            environment = Path(sys.prefix).resolve()
            python = Path(sys.executable)
            module = Path(__file__).resolve()
            installed = Path(
                distribution("cbs-transcriber").locate_file("cbs_transcriber/shortcuts.py")
            ).resolve()
            if (
                module == installed
                and module.is_relative_to(environment)
                and module.is_file()
                and module.with_name("__main__.py").is_file()
                and python.is_absolute()
                and python.parent.resolve().is_relative_to(environment)
                and python.is_file()
            ):
                # Do not resolve the executable: a macOS venv may link to a base Python.
                return environment, python
        except (PackageNotFoundError, OSError, TypeError, ValueError):
            pass
        raise ValueError(
            f"{source_error} For a wheel/tool install, run cbs-transcriber --create-shortcut "
            "from its installed package environment. Recreate after moving or replacing it."
        ) from None


def create_shortcut(arguments, *, location="applications"):
    """Create one launcher without overwriting an existing file or starting the app."""
    if sys.platform not in {"win32", "darwin"}:
        raise ValueError("Shortcut creation is available on Windows and macOS only.")
    if location not in {"applications", "desktop"}:
        raise ValueError("Choose applications or desktop for the shortcut location.")
    source, python = _installed_launch()
    command = [str(python), "-m", "cbs_transcriber", *arguments]
    if sys.platform == "win32":
        request = {
            "folder": "Desktop" if location == "desktop" else "Programs",
            "source": str(source),
            "python": str(python),
            "arguments": subprocess.list2cmdline(command[1:]),
        }
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT],
                input=json.dumps(request, ensure_ascii=True),
                capture_output=True,
                encoding="utf-8",
                check=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            receipt = json.loads(result.stdout)
            target = Path(receipt["path"])
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(
                "Could not create the shortcut. Check Windows PowerShell/Windows Script Host "
                "availability, folder permissions and CBS device policy."
            ) from exc
        if receipt["exists"]:
            raise FileExistsError(
                f"Shortcut already exists: {target}. Remove it first to recreate it."
            )
        return target

    folder = Path.home() / ("Desktop" if location == "desktop" else "Applications")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "CBS Transcriber.command"
    script = f"#!/bin/sh\ncd {shlex.quote(str(source))} || exit 1\nexec {shlex.join(command)}\n"
    # Exclusive creation also refuses an existing symlink. No application bundle or signing.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(script)
    return target

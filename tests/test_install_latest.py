"""The stable bootstrap uses fake GitHub assets and a mocked installer, never a real install."""

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/install_latest.py"
SPEC = importlib.util.spec_from_file_location("install_latest", SCRIPT)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.fixture
def release():
    bodies = {
        "cbs_transcriber-0.2.0-py3-none-any.whl": b"synthetic wheel bytes",
        "cbs-transcriber-0.2.0-constraints.txt": b"synthetic-dependency==1.0\n",
    }
    assets = [
        {
            "name": name,
            "state": "uploaded",
            "browser_download_url": f"{installer.REPOSITORY}/releases/download/v0.2.0/{name}",
            "size": len(body),
            "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
        }
        for name, body in bodies.items()
    ]
    return {
        "draft": False,
        "prerelease": False,
        "tag_name": "v0.2.0",
        "html_url": f"{installer.REPOSITORY}/releases/tag/v0.2.0",
        "assets": assets,
    }, bodies


@pytest.fixture
def fake_install(monkeypatch, release):
    metadata, bodies = release
    reads, commands, installed_bytes, port_events = [], [], [], []

    class Guard:
        def __enter__(self):
            port_events.append("held")
            return self

        def __exit__(self, *args):
            port_events.append("released")

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            assert address == ("127.0.0.1", 8765)

    def read(url, maximum):
        assert port_events == ["held"]
        reads.append(url)
        if url == installer.LATEST_API:
            return json.dumps(metadata).encode()
        return bodies[url.rsplit("/", 1)[1]]

    def run(command, **kwargs):
        assert port_events == ["held"]
        if command[1:] == ["--version"]:
            return SimpleNamespace(stdout="uv 0.9.29 (synthetic)")
        commands.append(command)
        installed_bytes.append(
            (
                Path(command[-1]).read_bytes(),
                Path(command[command.index("--constraints") + 1]).read_bytes(),
            )
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(installer.socket, "socket", lambda *args: Guard())
    monkeypatch.setattr(installer, "read_url", read)
    monkeypatch.setattr(installer.subprocess, "run", run)
    monkeypatch.setenv("UV", "C:/explicit uv/uv.exe")
    monkeypatch.setattr(installer.shutil, "which", lambda _: pytest.fail("Do not shadow UV"))
    return metadata, bodies, reads, commands, installed_bytes, port_events


@pytest.mark.parametrize("update", [False, True])
def test_one_release_pair_is_verified_then_installed_with_required_flags(fake_install, update):
    metadata, bodies, reads, commands, installed_bytes, port_events = fake_install
    installer.main(["--update"] if update else [])
    assert reads == [
        installer.LATEST_API,
        *[asset["browser_download_url"] for asset in metadata["assets"]],
    ]
    assert len(commands) == 1
    command = commands[0]
    assert command[:9] == [
        "C:/explicit uv/uv.exe",
        "tool",
        "install",
        "--python",
        "3.13",
        "--no-config",
        "--link-mode",
        "copy",
        "--constraints",
    ]
    assert ("--reinstall-package" in command) is update
    assert "--force" not in command
    assert installed_bytes == [tuple(bodies.values())]
    assert not Path(command[-1]).exists()  # verified temporary copies are removed after uv exits
    assert port_events == ["held", "released"]


@pytest.mark.parametrize(
    "damage",
    [
        "draft",
        "prerelease",
        "tag",
        "repository",
        "wheel-version",
        "missing",
        "duplicate",
        "foreign-url",
        "wrong-release-url",
        "digest",
        "size",
        "boolean-size",
        "oversized",
        "state",
        "asset-list",
    ],
)
def test_malformed_or_unmatched_release_refuses_before_install(fake_install, damage):
    metadata, _, _, commands, _, port_events = fake_install
    if damage in {"draft", "prerelease"}:
        metadata[damage] = True
    elif damage == "tag":
        metadata["tag_name"] = "latest"
    elif damage == "repository":
        metadata["html_url"] = "https://github.com/other/repository/releases/tag/v0.2.0"
    elif damage == "wheel-version":
        metadata["assets"][0]["name"] = "cbs_transcriber-latest-py3-none-any.whl"
    elif damage == "missing":
        metadata["assets"].pop()
    elif damage == "duplicate":
        metadata["assets"].append(deepcopy(metadata["assets"][0]))
    elif damage in {"foreign-url", "wrong-release-url"}:
        metadata["assets"][0]["browser_download_url"] = metadata["assets"][0][
            "browser_download_url"
        ].replace("github.com" if damage == "foreign-url" else "v0.2.0", "example.invalid")
    elif damage == "digest":
        metadata["assets"][0]["digest"] = None
    elif damage in {"size", "boolean-size"}:
        metadata["assets"][0]["size"] = 0 if damage == "size" else True
    elif damage == "oversized":
        metadata["assets"][0]["size"] = installer.MAX_WHEEL + 1
    elif damage == "state":
        metadata["assets"][0]["state"] = "new"
    else:
        metadata["assets"] = None
    with pytest.raises(SystemExit) as error:
        installer.main([])
    assert error.value.code == 2
    assert commands == []
    assert port_events == ["held", "released"]


@pytest.mark.parametrize("damage", ["wheel", "constraints", "length"])
def test_changed_asset_bytes_never_reach_uv_install(fake_install, damage):
    _, bodies, _, commands, _, _ = fake_install
    name = list(bodies)[1 if damage == "constraints" else 0]
    body = bodies[name]
    bodies[name] = bytes([body[0] ^ 1]) + body[1:] if damage != "length" else body + b"!"
    with pytest.raises(SystemExit) as error:
        installer.main(["--update"])
    assert error.value.code == 2
    assert commands == []


def test_unicode_digits_cannot_become_a_wheel_version(release):
    metadata, _ = release
    metadata["tag_name"] = "v١.٢.٣"
    metadata["html_url"] = f"{installer.REPOSITORY}/releases/tag/v١.٢.٣"
    for asset in metadata["assets"]:
        asset["name"] = asset["name"].replace("0.2.0", "١.٢.٣")
        asset["browser_download_url"] = asset["browser_download_url"].replace("0.2.0", "١.٢.٣")
    with pytest.raises(ValueError, match="version"):
        installer.release_assets(metadata)


def test_running_default_port_blocks_even_metadata_and_uv_calls(monkeypatch):
    class BusyPort:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            raise OSError("Synthetic running app")

    monkeypatch.setattr(installer.socket, "socket", lambda *args: BusyPort())
    monkeypatch.setattr(installer, "read_url", lambda *args: pytest.fail("No downloads"))
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: pytest.fail("No uv"))
    with pytest.raises(SystemExit) as error:
        installer.main(["--update"])
    assert error.value.code == 2


@pytest.mark.parametrize("value", [b"not JSON", b"null", b"[]"])
def test_bad_metadata_never_installs(fake_install, monkeypatch, value):
    monkeypatch.setattr(installer, "read_url", lambda *args: value)
    with pytest.raises(SystemExit):
        installer.main([])
    assert fake_install[3] == []


def test_download_failure_never_installs(fake_install, monkeypatch):
    def unavailable(*args):
        raise OSError("Synthetic public release unavailable")

    monkeypatch.setattr(installer, "read_url", unavailable)
    with pytest.raises(SystemExit):
        installer.main([])
    assert fake_install[3] == []


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a",
        "https://foreign.invalid/a",
        "https://secret@github.com/a",
        "https://github.com/other/repository/releases/download/v0.2.0/file",
    ],
)
def test_untrusted_download_redirect_is_refused(url):
    request = Request(f"{installer.REPOSITORY}/releases/download/v0.2.0/file")
    with pytest.raises(ValueError, match="redirect"):
        installer.ReleaseRedirects().redirect_request(request, None, 302, "Found", {}, url)


def test_public_api_cannot_redirect_even_to_an_allowed_asset_host():
    with pytest.raises(ValueError, match="redirect"):
        installer.ReleaseRedirects().redirect_request(
            Request(installer.LATEST_API), None, 302, "Found", {}, "https://github.com/file"
        )


def test_github_asset_can_redirect_to_its_https_cdn():
    request = Request(f"{installer.REPOSITORY}/releases/download/v0.2.0/file")
    target = "https://release-assets.githubusercontent.com/asset?signature=synthetic"
    redirected = installer.ReleaseRedirects().redirect_request(
        request, None, 302, "Found", {}, target
    )
    assert redirected.full_url == target


@pytest.mark.parametrize("version", ["uv 0.9.28", "unexpected tool"])
def test_unsupported_uv_refuses_before_downloading_assets(fake_install, monkeypatch, version):
    monkeypatch.setattr(
        installer.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=version)
    )
    with pytest.raises(SystemExit):
        installer.main([])
    assert fake_install[2] == []
    assert fake_install[3] == []


def test_uv_failure_releases_port_and_temporary_downloads(fake_install, monkeypatch):
    def fail(command, **kwargs):
        if command[1:] == ["--version"]:
            return SimpleNamespace(stdout="uv 0.9.29")
        fake_install[3].append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(installer.subprocess, "run", fail)
    with pytest.raises(SystemExit):
        installer.main(["--update"])
    assert fake_install[5] == ["held", "released"]
    assert not Path(fake_install[3][0][-1]).exists()


def test_download_size_is_bounded_without_network(monkeypatch):
    monkeypatch.setattr(
        installer.urllib.request,
        "build_opener",
        lambda *args: SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(b"12345")),
    )
    with pytest.raises(ValueError, match="size limit"):
        installer.read_url(installer.LATEST_API, 4)


def test_cli_help_is_standalone_and_never_imports_the_app():
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--help"], check=True, capture_output=True, text=True
    )
    assert "--update" in result.stdout
    assert result.stderr == ""

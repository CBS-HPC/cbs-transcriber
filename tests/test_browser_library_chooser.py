"""The launcher asks which library to open, remembers it, and switches libraries in place."""

import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect
from test_browser import page as page

from cbs_transcriber import __main__ as launcher
from cbs_transcriber import libraries, storage
from cbs_transcriber.store import Store

pytestmark = pytest.mark.browser


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_until(check, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    return False


@pytest.mark.skipif(os.name != "nt", reason="uses Windows packaged-app redirection")
def test_launcher_chooses_remembers_and_switches_libraries(
    page, tmp_path, monkeypatch, credentials
):
    # Launcher-created apps must not inspect this computer's OS credentials or API-key env.
    monkeypatch.setattr("cbs_transcriber.app.Credentials", lambda: credentials)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("CBS_TRANSCRIBER_DATA_DIR", raising=False)
    monkeypatch.setattr(storage, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(launcher, "legacy_library_root", lambda: tmp_path / "legacy")
    first = Store(libraries.default_new())
    first.create_project("Default interviews")
    redirected = Store(
        tmp_path / "local" / "Packages" / "OpenAI.Codex_x" / "LocalCache/Local/CBS Transcriber"
    )
    redirected.create_project("Codex-era interviews")
    servers = []
    original = launcher.server_for

    def tracked(app, port):
        servers.append(original(app, port))
        return servers[-1]

    monkeypatch.setattr(launcher, "server_for", tracked)
    port = free_port()
    monkeypatch.setattr(
        sys,
        "argv",
        ["cbs-transcriber", "--no-browser", "--port", str(port), "--projects-dir", str(tmp_path)],
    )
    thread = threading.Thread(target=launcher.main, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    try:
        assert wait_until(lambda: httpx.get(url + "/").status_code == 200)
        page.goto(url + "/")
        # Two libraries and nothing remembered: the chooser lists both.
        expect(page.locator("h1")).to_have_text("Choose a library")
        items = page.locator(".library-item")
        expect(items).to_have_count(2)
        codex = items.filter(has_text="App data redirected by OpenAI.Codex")
        expect(codex).to_contain_text("1 project(s)")
        codex.get_by_role("button", name="Open").click()
        expect(page.locator("#projects")).to_contain_text("Codex-era interviews", timeout=30000)
        assert libraries.load()["current"] == str(redirected.root)
        # Switch from inside the app.
        page.locator("#library-open").click()
        current = page.locator("#library-dialog .library-item.current")
        expect(current).to_contain_text("Open now")
        expect(current).to_contain_text(str(redirected.root))
        other = page.locator("#library-dialog .library-item").filter(has_text="Default library")
        page.once("dialog", lambda dialog: dialog.accept())
        other.get_by_role("button", name="Remove from list").click()
        disclosure = page.locator("#library-dialog details").filter(has_text="Hidden libraries")
        expect(disclosure.locator("summary")).to_have_text("Hidden libraries (1)")
        disclosure.locator("summary").click()
        expect(disclosure).to_contain_text("unfinished work")
        disclosure.get_by_role("button", name="Show again").click()
        expect(disclosure).to_have_count(0)
        other = page.locator("#library-dialog .library-item").filter(has_text="Default library")
        other.get_by_role("button", name="Open").click()
        expect(page.locator("#projects")).to_contain_text("Default interviews", timeout=30000)
        expect(page.locator("#projects")).not_to_contain_text("Codex-era interviews")
        assert libraries.load()["current"] == str(first.root)
        assert libraries.load()["recent"][:2] == [str(first.root), str(redirected.root)]
    finally:
        assert wait_until(lambda: bool(servers) and servers[-1].started, 10) or True
        for server in servers:
            server.should_exit = True
        thread.join(30)
    assert not thread.is_alive()

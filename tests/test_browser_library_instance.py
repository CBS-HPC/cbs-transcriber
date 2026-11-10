"""Real browser checks against two isolated synthetic library instances."""

import socket
import threading
import time

import pytest
import uvicorn
from fastapi.responses import Response
from playwright.sync_api import expect
from test_browser import page as page

from cbs_transcriber.app import create_app

pytestmark = pytest.mark.browser


def test_stale_tab_preserves_draft_and_fences_downloads(tmp_path, credentials, page):
    first = create_app(tmp_path / "first", start_worker=False, credentials=credentials)
    second = create_app(tmp_path / "second", start_worker=False, credentials=credentials)
    writes = []

    def install(app, name):
        @app.get("/api/fence-test/shared-id")
        def same_identifier():
            return {"library": name}

        @app.post("/api/fence-test/shared-id")
        def same_identifier_write():
            writes.append(name)
            return {"saved": True}

        @app.get("/api/fence-test/download")
        def download():
            return Response(
                b"synthetic bytes",
                media_type="application/octet-stream",
                headers={"Content-Disposition": 'attachment; filename="synthetic.wav"'},
            )

    install(first, "first")
    install(second, "second")

    class SwitchedApp:
        current = first

        async def __call__(self, scope, receive, send):
            await self.current(scope, receive, send)

    switched = SwitchedApp()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(switched, lifespan="off", log_level="error", access_log=False)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        page.goto(url)
        expect(page.locator("#sidebar-create")).to_be_visible()
        page.locator("#sidebar-create").click()
        page.locator("#name-input").fill("unsaved synthetic draft")
        assert page.evaluate("() => api('/fence-test/shared-id')") == {"library": "first"}
        previous_url = page.url
        switched.current = second
        stale_status = page.evaluate(
            """async () => {
              try { await api('/fence-test/shared-id', {background: true}); return 200; }
              catch (error) { return error.status; }
            }"""
        )
        assert stale_status == 409
        expect(page.locator("#library-instance-notice")).to_be_visible()
        assert page.locator("#name-input").input_value() == "unsaved synthetic draft"
        assert (
            page.evaluate(
                """async () => (await fetch('/api/fence-test/shared-id', {
              method: 'POST', headers: {'X-CBS-Request': '1', ...CBSLibraries.instanceHeaders()}
            })).status"""
            )
            == 409
        )
        assert not writes
        assert (
            page.evaluate(
                """async () =>
                  (await fetch(CBSLibraries.resource('/api/fence-test/download'))).status"""
            )
            == 409
        )
        # Export requires an open dialog's selected scope; its actual GET/POST paths are
        # exercised by test_browser_export_selection.py instead of a hidden fake anchor.
        generic_download_links = ("original-link", "history-export", "annotation-export")
        for identifier in generic_download_links:
            with page.expect_response(
                lambda response: "/api/health" in response.url and response.status == 409
            ):
                page.evaluate(
                    """id => { const link = document.getElementById(id);
                      link.removeAttribute('aria-disabled');
                      link.href = CBSLibraries.resource('/api/fence-test/download');
                      link.click(); }""",
                    identifier,
                )
            expect(page.locator("#toast")).to_be_visible()
            assert page.url == previous_url
            assert page.locator("#name-input").input_value() == "unsaved synthetic draft"
        # Loading the new page gets the new instance. Original and transcript downloads
        # still use the server-provided filename, without buffering the original in JS.
        page.on("dialog", lambda dialog: dialog.accept())
        page.reload()
        assert page.evaluate("() => api('/fence-test/shared-id')") == {"library": "second"}
        for identifier in generic_download_links:
            with page.expect_download() as result:
                page.evaluate(
                    """id => { const link = document.getElementById(id);
                      link.removeAttribute('aria-disabled');
                      link.href = CBSLibraries.resource('/api/fence-test/download');
                      link.click(); }""",
                    identifier,
                )
            assert result.value.suggested_filename == "synthetic.wav"
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()
